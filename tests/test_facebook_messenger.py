"""Facebook como canal principal: Messenger da Página, venda direta, 24 h."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from cryptography.fernet import Fernet

from integrations import webhook
from veratus_agents.channel_adapters import (
    DirectInbox,
    FacebookMessengerAdapter,
    parse_messenger_messages,
)
from veratus_agents.conversations import (
    CHANNELS,
    MARKETPLACES,
    ConversationEngine,
    ConversationStore,
    InboundText,
    TemplateSalesModel,
)
from veratus_agents.sales_flow import SALES_FLOW, flow_report

KEY = Fernet.generate_key().decode("ascii")
PAGE = "1000000000001"
PSID = "2000000000002"


def _payload(mid="m_1", text="Olá, isso ainda está disponível?", sender=PSID, **extra):
    page = {"id": PAGE}
    return {
        "object": "page",
        "entry": [
            {
                "id": PAGE,
                "messaging": [
                    {
                        "sender": {"id": sender},
                        # A Página recebe (entrada do cliente, não envio).
                        "recipient": page,
                        "timestamp": 1760000000000,
                        "message": {"mid": mid, "text": text, **extra},
                    }
                ],
            }
        ],
    }


def _inbox(tmp_path):
    return DirectInbox(tmp_path / "dm.sqlite3", salt="sal", encryption_key=KEY)


class _Session:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs["json"]))

        class _Response:
            ok = True

            @staticmethod
            def json():
                return {"recipient_id": PSID, "message_id": "m_out_1"}

        return _Response()


def test_facebook_is_the_first_channel_and_sells_directly():
    assert CHANNELS[0] == "facebook"
    assert SALES_FLOW[0]["channel"] == "facebook"
    # Venda direta (link do Mercado Pago), não marketplace sem contato.
    assert "facebook" not in MARKETPLACES
    report = flow_report(env={})
    assert report["channels"][0]["label"] == "Facebook"
    assert report["channels"][0]["send_enabled"] is False


def test_messenger_parser_keeps_only_text_from_people():
    assert [m["message_id"] for m in parse_messenger_messages(_payload(), PAGE)] == [
        "m_1"
    ]
    assert parse_messenger_messages(_payload(is_echo=True), PAGE) == []
    assert parse_messenger_messages(_payload(sender=PAGE), PAGE) == []
    assert parse_messenger_messages({**_payload(), "object": "instagram"}, PAGE) == []


def test_messenger_only_answers_who_wrote_inside_the_window(tmp_path):
    inbox = _inbox(tmp_path)
    session = _Session()
    adapter = FacebookMessengerAdapter(
        inbox, access_token="t", page_id=PAGE, session=session, enabled=True
    )
    never_wrote = adapter.send("fb_" + "0" * 32, "Olá", idempotency_key="a")
    assert never_wrote["status"] == "BLOCKED"
    assert "CUSTOMER_SERVICE_WINDOW_CLOSED" in never_wrote["reasons"]

    now = datetime.now(timezone.utc)
    conversation_id, _ = inbox.receive("facebook", PSID, "m_1", "oi", now)
    assert conversation_id.startswith("fb_")
    sent = adapter.send(conversation_id, "Olá! Posso ajudar?", idempotency_key="b")
    assert sent == {"status": "SENT", "message_id": "m_out_1"}
    url, body = session.calls[-1]
    assert url.endswith(f"/{PAGE}/messages") and "graph.facebook.com" in url
    assert body["messaging_type"] == "RESPONSE"
    assert adapter.send(conversation_id, "Olá!", idempotency_key="b")["status"] == (
        "duplicate"
    )
    assert len(session.calls) == 1


def test_messenger_human_agent_tag_only_for_a_person_after_24h(tmp_path):
    inbox = _inbox(tmp_path)
    session = _Session()
    adapter = FacebookMessengerAdapter(
        inbox, access_token="t", page_id=PAGE, session=session, enabled=True
    )
    two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
    conversation_id, _ = inbox.receive("facebook", PSID, "m_1", "oi", two_days_ago)

    by_agent = adapter.send(conversation_id, "Oi!", idempotency_key="a")
    assert by_agent["status"] == "BLOCKED"
    by_person = adapter.send(
        conversation_id,
        "Oi, aqui é o fundador.",
        idempotency_key="b",
        human_written=True,
    )
    assert by_person["status"] == "SENT"
    assert session.calls[-1][1]["tag"] == "HUMAN_AGENT"


def test_messenger_send_waits_for_the_flag_and_the_credentials(tmp_path):
    inbox = _inbox(tmp_path)
    conversation_id, _ = inbox.receive(
        "facebook", PSID, "m_1", "oi", datetime.now(timezone.utc)
    )
    with patch.dict("os.environ", {"FACEBOOK_MESSAGING_ENABLED": "false"}):
        blocked = FacebookMessengerAdapter(
            inbox, access_token=None, page_id=None, session=_Session()
        ).send(conversation_id, "Oi!", idempotency_key="a")
    assert set(blocked["reasons"]) == {
        "FACEBOOK_MESSAGING_DISABLED",
        "FACEBOOK_CREDENTIALS_MISSING",
    }


def _signed(body):
    digest = hmac.new(b"segredo-fb", body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={digest}",
        "Content-Type": "application/json",
    }


def test_facebook_webhook_verifies_stores_and_triggers_the_channel(tmp_path):
    webhook._autoprocess_running.clear()
    webhook._autoprocess_again.clear()
    env = {
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "VERATUS_SESSION_SALT": "sal",
        "VERATUS_TOKEN_ENCRYPTION_KEY": KEY,
        "FACEBOOK_APP_SECRET": "segredo-fb",
        "FACEBOOK_VERIFY_TOKEN": "verifica",
        "META_PAGE_ID": PAGE,
    }
    body = json.dumps(_payload()).encode()
    triggered = []
    with (
        patch.dict("os.environ", env),
        patch.object(webhook, "_autoprocess", triggered.append),
    ):
        client = webhook.app.test_client()
        challenge = client.get(
            "/integrations/facebook/webhook?hub.mode=subscribe"
            "&hub.verify_token=verifica&hub.challenge=42"
        )
        forged = client.post(
            "/integrations/facebook/webhook",
            data=body,
            headers={**_signed(body), "X-Hub-Signature-256": "sha256=00"},
        )
        first = client.post(
            "/integrations/facebook/webhook", data=body, headers=_signed(body)
        )
        replay = client.post(
            "/integrations/facebook/webhook", data=body, headers=_signed(body)
        )
    assert (challenge.status_code, challenge.text) == (200, "42")
    assert forged.status_code == 401
    assert first.json["messages"] == 1 and replay.json["messages"] == 0
    # O reenvio da Meta não dispara o canal de novo.
    assert triggered == ["facebook"]


def test_engine_sells_on_facebook_with_the_direct_sale_terms(tmp_path):
    sent = []

    class _Adapter:
        api_status = "SIMULATED"

        def __init__(self):
            self.limits = {"max_text": 2000}

        def window_open(self, conversation_id, now):
            return True

        def send(self, conversation_id, text, *, idempotency_key, human_written=False):
            sent.append(text)
            return {"status": "SENT", "message_id": f"m{len(sent)}"}

    engine = ConversationEngine(
        ConversationStore(tmp_path / "cv.sqlite3", encryption_key=KEY),
        adapters={"facebook": _Adapter()},
        model=TemplateSalesModel(),
        alert_sender=lambda alert: "TEST",
        mode_resolver=lambda _channel: "AUTO",
        knowledge_context=dict,
    )
    first = engine.handle(
        InboundText("facebook", "fb_1", "m1", "Olá, isso ainda está disponível?")
    )
    price = engine.handle(
        InboundText("facebook", "fb_1", "m2", "quanto custa o Royal Blue?")
    )
    assert first["intent"] == "disponibilidade" and price["action"] == "SEND"
    # Disponibilidade não é prometida sem estoque confirmado.
    assert "disponível" not in sent[0].lower()
    assert "R$ 289,90" in sent[1] and "frete grátis" in sent[1]


def test_meta_catalog_feed_is_off_until_the_flag_and_lists_only_ready_items():
    webhook.rate_store.clear()
    client = webhook.app.test_client()
    with patch.dict("os.environ", {"META_CATALOG_FEED_ENABLED": "false"}):
        assert client.get("/feeds/meta-catalog.csv").status_code == 404
    with patch.dict("os.environ", {"META_CATALOG_FEED_ENABLED": "true"}):
        feed = client.get("/feeds/meta-catalog.csv")
    assert feed.status_code == 200
    assert feed.headers["Content-Type"].startswith("text/csv")
    header, *rows = feed.text.splitlines()
    assert (
        header
        == "id,title,description,availability,condition,price,link,image_link,brand"
    )
    # Sem estoque confirmado, nenhuma peça entra no feed.
    assert rows == []


def test_meta_catalog_report_needs_the_admin_token():
    webhook.rate_store.clear()
    client = webhook.app.test_client()
    with patch.dict("os.environ", {"VERATUS_ADMIN_TOKEN": "admin-test-key"}):
        denied = client.get("/os/meta-catalog")
        report = client.get(
            "/os/meta-catalog", headers={"X-Veratus-Admin-Token": "admin-test-key"}
        )
    assert denied.status_code in (401, 403)
    assert report.status_code == 200 and report.json["external_write"] is False


def test_site_button_asking_availability_and_value_still_gets_the_price():
    from veratus_agents.conversations import classify

    site = (
        "Olá! Vim pelo site da Veratus e tenho interesse em Polar Blue.\n"
        "Gostaria de confirmar disponibilidade e valor."
    )
    assert classify(site, {}).intent == "preco"
    assert classify("Olá, isso ainda está disponível?", {}).intent == "disponibilidade"
