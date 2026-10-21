"""Envio: rastreio cifrado, um aviso de despacho por pedido e leitura do Mercado Envios."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from integrations import webhook
from veratus_agents.conversations import (
    SHIPPED_NOTICE,
    ConversationEngine,
    ConversationStore,
    InboundText,
    TemplateSalesModel,
)
from veratus_agents.marketplace_clients import (
    MarketplaceApiError,
    MercadoLivreReadOnlyClient,
)
from veratus_agents.orders import (
    OrderConflictError,
    OrderLedger,
    OrderRecord,
    StatusChange,
    TrackingStorageError,
    sync_mercado_livre_shipment,
)

KEY = Fernet.generate_key().decode("ascii")
ADMIN = {"X-Veratus-Admin-Token": "admin-test-key"}
CODE = "AA123456789BR"
ORDER = "wa-20261019-01"


def _order(ledger, order_id=ORDER, channel="whatsapp"):
    ledger.record(
        OrderRecord.model_validate(
            {
                "order_id": order_id,
                "sku": "ocean-blue",
                "channel": channel,
                "sale_price": "289.90",
                "payment_method": "pix",
                "status": "PAID",
                "evidence_ref": "teste-envio",
                "recorded_by": "fundador",
            }
        )
    )


def _shipped(event_id=f"envio-{ORDER}", **extra):
    return StatusChange(
        event_id=event_id, status="SHIPPED", recorded_by="fundador", **extra
    )


def test_shipped_route_keeps_the_code_encrypted_and_queues_one_notice(tmp_path):
    webhook.rate_store.clear()
    env = {
        "VERATUS_ADMIN_TOKEN": "admin-test-key",
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "VERATUS_SESSION_SALT": "sal",
        "VERATUS_TOKEN_ENCRYPTION_KEY": KEY,
    }
    body = {**_shipped().model_dump(mode="json"), "tracking_code": CODE}
    with patch.dict("os.environ", env):
        engine = webhook._conversation_engine()
        key = engine.store.conversation("whatsapp", "wa_1")["conv_key"]
        engine.store.update(key, order_id=ORDER)
        ledger = webhook._order_ledger()
        _order(ledger)
        client = webhook.app.test_client()
        first = client.post(f"/os/orders/{ORDER}/status", headers=ADMIN, json=body)
        again = client.post(f"/os/orders/{ORDER}/status", headers=ADMIN, json=body)
        listing = client.get("/os/orders", headers=ADMIN)
        detail = client.get(f"/os/orders/{ORDER}", headers=ADMIN)
        database = webhook.AgentSettings.from_env().operations_db

    assert (first.status_code, first.json["status"]) == (200, "changed")
    # SHADOW (padrão): o aviso vai para a fila, uma vez por pedido.
    assert [n["action"] for n in first.json["notices"]] == ["QUEUE"]
    assert [n["action"] for n in again.json["notices"]] == ["DUPLICATE"]
    [draft] = [d for d in engine.store.queue() if d["intent"] == "pedido_enviado"]
    assert draft["status"] == "SHADOW_DRAFT" and CODE in draft["reply"]
    for response in (first, again, listing, detail):
        assert CODE not in response.get_data(as_text=True)
    assert detail.json["order"]["shipping"]["tracking_code_stored"] is True
    assert ledger.tracking_code(ORDER) == CODE
    with sqlite3.connect(database) as db:
        rows = [
            row
            for table in ("orders_ledger_shipping", "orders_ledger_events", "cv_events")
            for row in db.execute(f"SELECT * FROM {table}")
        ]
    assert rows and CODE not in json.dumps(rows)
    # O código gravado nunca é trocado; sem chave, nada é gravado.
    with pytest.raises(OrderConflictError):
        ledger.change_status(ORDER, _shipped("rastreio-2", tracking_code="BB1BR"))
    keyless = OrderLedger(tmp_path / "sem-chave.sqlite3")
    _order(keyless)
    with pytest.raises(TrackingStorageError):
        keyless.change_status(ORDER, _shipped(tracking_code=CODE))
    assert keyless.get(ORDER)["status"] == "PAID"


class Adapter:
    api_status = "SIMULATED"

    def __init__(self, window=True):
        self.limits = {"max_text": 4096}
        self.window, self.sent, self.templates = window, [], []

    def window_open(self, conversation_id, now):
        return self.window

    def send(self, conversation_id, text, *, idempotency_key, human_written=False):
        self.sent.append(text)
        return {"status": "SENT", "message_id": f"m{len(self.sent)}"}

    def send_template(self, conversation_id, name, *, idempotency_key, params=None):
        self.templates.append((name, params))
        return {"status": "SENT", "message_id": "t1"}


def _engine(tmp_path, mode="AUTO", window=True):
    ledger = OrderLedger(tmp_path / "orders.sqlite3", encryption_key=KEY)
    _order(ledger)
    ledger.change_status(ORDER, _shipped(tracking_code=CODE))
    adapter = Adapter(window)
    engine = ConversationEngine(
        ConversationStore(tmp_path / "cv.sqlite3", encryption_key=KEY),
        adapters={"whatsapp": adapter},
        model=TemplateSalesModel(),
        alert_sender=lambda alert: "TEST",
        mode_resolver=lambda _channel: mode,
        knowledge_context=dict,
        order_lookup=ledger.get,
        tracking_lookup=ledger.tracking_code,
    )
    key = engine.store.conversation("whatsapp", "c1")["conv_key"]
    engine.store.update(key, order_id=ORDER)
    return engine, adapter


def test_shipped_notice_outside_the_window_only_with_the_approved_template(tmp_path):
    engine, adapter = _engine(tmp_path / "aberta")
    assert [n["action"] for n in engine.shipped_notice(ORDER)] == ["SEND"]
    assert [n["action"] for n in engine.shipped_notice(ORDER)] == ["DUPLICATE"]
    assert adapter.sent == [SHIPPED_NOTICE.format(code=CODE)]

    closed, closed_adapter = _engine(tmp_path / "fechada", window=False)
    [queued] = closed.shipped_notice(ORDER)
    assert queued["action"] == "QUEUE" and "TEMPLATE_NOT_APPROVED" in queued["reason"]
    assert (closed_adapter.sent, closed_adapter.templates) == ([], [])

    approved = {"pedido_enviado": {"status": "APPROVED"}}
    templated, templated_adapter = _engine(tmp_path / "aprovado", window=False)
    with patch("veratus_agents.whatsapp.load_templates", return_value=approved):
        [sent] = templated.shipped_notice(ORDER)
    assert sent["action"] == "SEND" and templated_adapter.sent == []
    assert templated_adapter.templates == [("pedido_enviado", [ORDER, CODE])]
    events = templated.store.events(event="ORDER_SHIPPED_NOTICE")
    assert events and CODE not in json.dumps(events)


@pytest.mark.parametrize(("mode", "action"), [("AUTO", "SEND"), ("AUTO_SAFE", "QUEUE")])
def test_rastreio_answers_with_the_stored_code_and_sends_only_in_auto(
    tmp_path, mode, action
):
    engine, adapter = _engine(tmp_path, mode=mode)
    result = engine.handle(
        InboundText("whatsapp", "c1", "m-1", "Oi! Qual o código de rastreio?")
    )

    assert result["action"] == action
    reply = adapter.sent[-1] if action == "SEND" else engine.store.queue()[0]["reply"]
    assert CODE in reply
    assert [e["reason"] for e in engine.store.events(event="TASK")] == []


class FakeResponse:
    ok, status_code = True, 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, data):
        self.calls, self.data = [], data

    def get(self, url, **kwargs):
        self.calls.append(url)
        return FakeResponse(self.data)


def test_mercado_livre_shipment_is_read_only_behind_the_flags(tmp_path, monkeypatch):
    shipment = {
        "id": 4012345678,
        "status": "delivered",
        "tracking_number": "ME1234567BR",
        "receiver_address": {"street_name": "Rua Teste"},
    }
    session = FakeSession(shipment)
    client = MercadoLivreReadOnlyClient("token", session=session)
    ledger = OrderLedger(tmp_path / "orders.sqlite3", encryption_key=KEY)
    _order(ledger, "ml-2000001234", channel="mercado_livre")
    monkeypatch.delenv("MERCADO_LIVRE_SYNC_ENABLED", raising=False)
    monkeypatch.delenv("MERCADO_LIVRE_LABELS_ENABLED", raising=False)

    off = sync_mercado_livre_shipment(ledger, client, "ml-2000001234", "4012345678")
    with pytest.raises(MarketplaceApiError):
        client.get_shipment_label("4012345678")
    assert off == {"status": "BLOCKED", "reason": "SYNC_DISABLED"}
    assert session.calls == []  # desligado, nada sai

    monkeypatch.setenv("MERCADO_LIVRE_SYNC_ENABLED", "true")
    on = sync_mercado_livre_shipment(ledger, client, "ml-2000001234", "4012345678")
    assert on["changes"] == ["changed", "changed"]
    assert ledger.get("ml-2000001234")["status"] == "DELIVERED"
    assert ledger.tracking_code("ml-2000001234") == "ME1234567BR"
    assert "Rua Teste" not in json.dumps(client.get_shipment("4012345678"))
    assert session.calls[0] == "https://api.mercadolibre.com/shipments/4012345678"
