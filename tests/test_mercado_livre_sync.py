"""Mercado Livre: fila de notificações, pedidos, pós-venda e escrita de anúncio.

Tudo com sessão falsa: nada sai para a API real.
"""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

from cryptography.fernet import Fernet

from integrations import webhook
from veratus_agents.channel_adapters import MercadoLivreConversationAdapter
from veratus_agents.marketplace_clients import (
    MercadoLivreReadOnlyClient,
    MercadoLivreWriteClient,
)
from veratus_agents.marketplace_ops import (
    ListingState,
    MarketplaceStore,
    write_mercado_livre_listing,
)
from veratus_agents.marketplace_service import MarketplaceConnectionService
from veratus_agents.operations import AGENT_REGISTRY
from veratus_agents.orders import OrderLedger, OrderRecord

KEY = Fernet.generate_key().decode("ascii")
ADMIN = {"X-Veratus-Admin-Token": "admin-test-key"}
PUBLISH_FLAGS = {
    "MARKETPLACE_MODE": "LIVE",
    "PUBLISH_ENABLED": "true",
    "MERCADO_LIVRE_ENABLED": "true",
    "MERCADO_LIVRE_PUBLISH_ENABLED": "true",
}


class FakeResponse:
    ok, status_code = True, 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeSession:
    """Responde por (método, caminho) e guarda cada chamada."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, **kwargs):
        path = url.removeprefix("https://api.mercadolibre.com")
        self.calls.append((method, path))
        return FakeResponse(self.routes[(method, path)](kwargs.get("json")))

    def get(self, url, **kwargs):
        return self.request("GET", url)


def _env(tmp_path):
    return {
        "VERATUS_ADMIN_TOKEN": "admin-test-key",
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "VERATUS_TOKEN_ENCRYPTION_KEY": KEY,
        "MERCADO_LIVRE_CLIENT_ID": "123",
        "MERCADO_LIVRE_NOTIFICATION_IP_ALLOWLIST": "",
        "MERCADO_LIVRE_SYNC_ENABLED": "false",
        "CONVERSATION_AUTOPROCESS_ENABLED": "false",
    }


def test_queue_imports_the_order_and_its_shipment_only_by_readback(tmp_path):
    order = {
        "id": 2000001234,
        "status": "paid",
        "date_created": "2026-10-18T10:00:00.000-03:00",
        "total_amount": 289.9,
        "currency_id": "BRL",
        "pack_id": 2000009999,
        "order_items": [{"item": {"id": "MLB9", "seller_sku": "ocean-blue"}}],
        "buyer": {"id": 7, "nickname": "APELIDO_FICTICIO"},
    }
    shipment = {
        "order_id": 2000001234,
        "status": "shipped",
        "tracking_number": "ME1234567BR",
        "receiver_address": {"street_name": "Rua Ficticia"},
    }
    session = FakeSession(
        {
            ("GET", "/orders/2000001234"): lambda _: order,
            ("GET", "/shipments/4012345678"): lambda _: shipment,
        }
    )
    reader = MercadoLivreReadOnlyClient("t", session=session)
    client = webhook.app.test_client()
    with (
        patch.dict("os.environ", _env(tmp_path)),
        patch.object(webhook, "_mercado_livre_reader", lambda: reader),
    ):
        for topic, resource in (
            ("orders_v2", "/orders/2000001234"),
            ("shipments", "/shipments/4012345678"),
            ("items", "/items/MLB9"),
            ("messages", "0a1b2c3d4e"),  # o resource de messages vem sem barra
        ):
            accepted = client.post(
                "/integrations/mercado-livre/notifications",
                json={
                    "_id": f"{topic}-1",
                    "resource": resource,
                    "user_id": 9,
                    "topic": topic,
                    "application_id": 123,
                },
            )
            assert accepted.status_code == 200, accepted.json
        off, calls_off = webhook._mercado_livre_sync(), list(session.calls)
        with patch.dict("os.environ", {"MERCADO_LIVRE_SYNC_ENABLED": "true"}):
            on = webhook._mercado_livre_sync()
            again = webhook._mercado_livre_sync()
            replay = webhook._ml_order_notification(
                reader, {"event_id": "replay-1", "resource": "/orders/2000001234"}
            )
        ledger = webhook._order_ledger()
        stored, tracking = (
            ledger.get("ml-2000001234"),
            ledger.tracking_code("ml-2000001234"),
        )

    # Flag desligada: a fila espera intacta e nada sai.
    assert off == [] and calls_off == []
    assert {item["topic"]: item["status"] for item in on} == {
        "orders_v2": "SYNCED",
        "shipments": "SYNCED",
        "items": "IGNORED",
        "messages": "PROCESSED",
    }
    assert again == [] and replay == "UNCHANGED"
    assert {method for method, _ in session.calls} == {"GET"}
    assert session.calls.count(("GET", "/shipments/4012345678")) == 1
    # Um pedido, pago pela leitura de volta, despachado pelo envio lido.
    assert (stored["status"], stored["payment_method"], stored["channel"]) == (
        "SHIPPED",
        "marketplace",
        "mercado_livre",
    )
    assert stored["conversation_ref"] == "pack-2000009999"
    assert stored["sale_price"] == "289.90" and tracking == "ME1234567BR"
    assert [event["event_type"] for event in stored["events"]] == [
        "ORDER_RECORDED",
        "STATUS_CHANGED",
        "STATUS_CHANGED",
    ]
    assert "FICTICI" not in json.dumps(stored).upper()


def test_post_sale_enters_only_what_the_buyer_wrote(tmp_path, monkeypatch):
    unread = {"results": [{"resource": "/packs/2000009999/sellers/555", "count": 1}]}
    pack = {
        "messages": [
            {
                "id": "m1",
                "from": {"user_id": 555},
                "text": "Pedido enviado.",
                "message_date": {"created": "2026-10-19T10:00:00Z"},
            },
            {
                "id": "m2",
                "from": {"user_id": 777},
                "text": "Chegou sem a caixa.",
                "message_date": {"created": "2026-10-19T11:00:00Z", "read": None},
            },
            {
                "id": "m3",
                "from": {"user_id": 777},
                "text": "Obrigado",
                "message_date": {"created": "2026-10-19T09:00:00Z", "read": "sim"},
            },
        ]
    }
    session = FakeSession(
        {
            ("GET", "/messages/unread"): lambda _: unread,
            ("GET", "/messages/packs/2000009999/sellers/555"): lambda _: pack,
        }
    )
    adapter = MercadoLivreConversationAdapter(
        token_provider=lambda: "t", seller_id="555", session=session
    )
    monkeypatch.delenv("MERCADO_LIVRE_SYNC_ENABLED", raising=False)
    off = adapter.list_unread_post_sale()
    monkeypatch.setenv("MERCADO_LIVRE_SYNC_ENABLED", "true")
    ledger = OrderLedger(tmp_path / "orders.sqlite3")
    ledger.record(
        OrderRecord.model_validate(
            {
                "order_id": "ml-2000001234",
                "sku": "ocean-blue",
                "channel": "mercado_livre",
                "conversation_ref": "pack-2000009999",
                "sale_price": "289.90",
                "payment_method": "marketplace",
                "evidence_ref": "mercado-livre:orders",
                "recorded_by": "mercado-livre-sync",
            }
        )
    )
    with patch.object(webhook, "_order_ledger", lambda: ledger):
        [inbound] = webhook._mercado_livre_post_sale(adapter, 20)

    assert off == [] and session.calls[0] == ("GET", "/messages/unread")
    # Só a mensagem não lida do comprador; o pack liga a conversa ao pedido.
    assert (inbound.conversation_id, inbound.message_id, inbound.text) == (
        "pack-2000009999",
        "pm-m2",
        "Chegou sem a caixa.",
    )
    assert inbound.product_hint == "ocean-blue"


def test_listing_write_and_sync_follow_flags_cap_and_public_copy(tmp_path, monkeypatch):
    for name in (*PUBLISH_FLAGS, "MERCADO_LIVRE_SYNC_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    item, posted = {"id": "MLB1", "status": "active"}, []

    def write(body):
        item.update({k: body[k] for k in ("price", "available_quantity") if k in body})
        return {"id": "MLB1"}

    session = FakeSession(
        {
            ("GET", "/sites/MLB/domain_discovery/search"): lambda _: [
                {"category_id": "MLB1234"}
            ],
            ("POST", "/items"): lambda body: posted.append(body) or write(body),
            ("PUT", "/items/MLB1"): write,
            ("PUT", "/items/MLB1/description"): lambda _: {},
            ("GET", "/items/MLB1"): lambda _: dict(item),
        }
    )
    reader = MercadoLivreReadOnlyClient("t", session=session)
    writer = MercadoLivreWriteClient(reader)
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")
    # Joia de revenda: preço público, sob encomenda, teto de 2 no canal.
    product = {
        "sku": "VRT-JWL-CHN-001",
        "status": "APPROVED",
        "name": "Corrente Grumet, tom dourado, ref. 001",
        "collection": "feminine",
        "category": "jewelry_accessories",
        "subcategory": "chain",
        "sale_price": "79.90",
        "price_source": "regra do fundador",
        "inventory_mode": "ON_DEMAND",
        "channel_stock_cap": 2,
        "supplier_ref": "ref-privada-77",
        "unverified_attributes": ["material"],
    }
    draft = {
        "title": "Corrente Grumet tom dourado",
        "description": "Corrente de elo grumet em tom dourado.",
        "category_id": "PENDING_ML_CHAIN_CATEGORY",
        "listing_type_id": "gold_special",
        "images": ["assets/jewelry/corrente-grumet-001-1.webp"],
    }
    sku, base = product["sku"], "https://veratus.example"
    leaky = {**draft, "description": "Material ref-privada-77."}
    store.upsert_listing(sku, "mercado-livre", ListingState.DRAFT, payload=leaky)

    off = write_mercado_livre_listing(store, writer, product, base_url=base)
    for name, value in PUBLISH_FLAGS.items():
        monkeypatch.setenv(name, value)
    store.request_approval("mercado-livre", sku, {})
    store.resolve_approval(f"publish_mercado-livre_{sku}", "APPROVED")
    no_role = write_mercado_livre_listing(store, writer, product, base_url=base)
    # Só o teste dá PUBLISH ao papel; no código, isso é decisão do fundador.
    agent = AGENT_REGISTRY["mercado-livre-agent"]
    monkeypatch.setitem(
        AGENT_REGISTRY,
        "mercado-livre-agent",
        replace(agent, permissions=agent.permissions | {"PUBLISH"}),
    )
    leaked = write_mercado_livre_listing(store, writer, product, base_url=base)
    assert {
        "MARKETPLACE_MODE_LOCAL",
        "GLOBAL_PUBLISH_DISABLED",
        "CHANNEL_PUBLISH_DISABLED",
        "PUBLISH_APPROVAL_REQUIRED",
        "AGENT_PUBLISH_PERMISSION_REQUIRED",
    } <= set(off["reasons"])
    assert no_role["reasons"] == ["AGENT_PUBLISH_PERMISSION_REQUIRED"]
    assert leaked["reasons"] == ["COPY_PRIVATE_DATA", "COPY_UNVERIFIED_ATTRIBUTE"]
    assert session.calls == []

    store.upsert_listing(sku, "mercado-livre", ListingState.DRAFT, payload=draft)
    created = write_mercado_livre_listing(store, writer, product, base_url=base)
    listing = store.listing("mercado-livre", sku)
    assert created["status"] == "PUBLISHED"
    # Categoria do preditor; estoque nunca acima do teto do canal.
    assert (posted[0]["category_id"], posted[0]["available_quantity"]) == (
        "MLB1234",
        2,
    )
    assert posted[0]["price"] == 79.9
    assert (listing["external_listing_id"], listing["remote_version"]) == ("MLB1", 1)
    assert listing["state"] == "PUBLISHED" and listing["last_publish_at"]

    # O anúncio mudou no painel: o plano só lê, registra e marca UPDATE_PENDING.
    item.update(price=99.9, available_quantity=10)
    service = MarketplaceConnectionService(store, mercado_factory=lambda: reader)
    blocked = service.sync_plan(lambda _: product)
    monkeypatch.setenv("MERCADO_LIVRE_SYNC_ENABLED", "true")
    writes = [call for call in session.calls if call[0] != "GET"]
    plan = service.sync_plan(lambda _: product)
    assert blocked["reason"] == "SYNC_DISABLED"
    assert set(plan["plan"][0]["changes"]) == {"price", "available_quantity"}
    assert [call for call in session.calls if call[0] != "GET"] == writes
    assert {item["type"] for item in store.sync_conflicts()} == {
        "PRICE_MISMATCH",
        "STOCK_MISMATCH",
    }
    assert store.listing("mercado-livre", sku)["state"] == "UPDATE_PENDING"

    # Aplicar é o mesmo cliente de escrita: preço público e estoque até o teto.
    applied = write_mercado_livre_listing(store, writer, product, base_url=base)
    listing = store.listing("mercado-livre", sku)
    assert applied["status"] == "UPDATED"
    assert (item["price"], item["available_quantity"]) == (79.9, 2)
    assert store.sync_conflicts() == []
    assert (listing["state"], listing["remote_version"]) == ("PUBLISHED", 2)


def test_remote_pause_goes_through_the_publish_guard(tmp_path):
    webhook.rate_store.clear()
    with (
        patch.dict("os.environ", _env(tmp_path)),
        patch.object(
            webhook, "_mercado_livre_reader", side_effect=AssertionError("rede")
        ),
    ):
        store = webhook._marketplace_store()
        store.upsert_listing("ocean-blue", "mercado-livre", ListingState.PUBLISHED)
        store.set_remote_ids("mercado-livre", "ocean-blue", ["MLB1"])
        response = webhook.app.test_client().post(
            "/os/listings/mercado-livre/ocean-blue/pause", headers=ADMIN
        )
        state = store.listing("mercado-livre", "ocean-blue")["state"]

    # Sem aprovação, flags e papel com PUBLISH: nada sai e o local não diverge.
    assert response.status_code == 409
    assert {"PUBLISH_APPROVAL_REQUIRED", "AGENT_PUBLISH_PERMISSION_REQUIRED"} <= set(
        response.json["reasons"]
    )
    assert response.json["external_write"] is False and state == "PUBLISHED"
