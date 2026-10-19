"""Shopee, TikTok Shop, Amazon e conteúdo do TikTok: uma prova por regra."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from cryptography.fernet import Fernet

import integrations.marketplace_connect as connect_module
import integrations.webhook as webhook_module
from integrations.webhook import app, rate_store
from veratus_agents import tiktok_content
from veratus_agents.amazon_sp import ORDER_FIELDS, AmazonSellingPartnerClient
from veratus_agents.marketplace_clients import MarketplaceApiError
from veratus_agents.marketplace_orders import (
    FLAGS,
    amazon_order,
    import_order,
    sync_orders,
)
from veratus_agents.mercado_livre_oauth import StoredTokens
from veratus_agents.orders import make_order_ledger
from veratus_agents.shopee import ShopeeClient, ShopeeReadOnlyClient, ShopeeTokenManager
from veratus_agents.tiktok_shop import TikTokShopClient

ADMIN = {"X-Veratus-Admin-Token": "admin-teste"}


class Resp:
    def __init__(self, data, status_code=200):
        self.data, self.status_code, self.ok = data, status_code, status_code < 400

    def json(self):
        return self.data


class Sessao:
    def __init__(self, *, gets=(), posts=()):
        self.respostas = {"GET": list(gets), "POST": list(posts)}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.respostas["GET"].pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.respostas["POST"].pop(0)


class Loja:
    """Token guardado, sem validade: nenhuma renovação."""

    def load_tokens(self, required=True):
        return StoredTokens("tok", "ref", None, "123", None)


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    env = {
        "VERATUS_ADMIN_TOKEN": ADMIN["X-Veratus-Admin-Token"],
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "VERATUS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "DATABASE_URL": "",
        "PUBLIC_BASE_URL": "https://veratus.onrender.com",
        "SHOPEE_PARTNER_ID": "2001887",
        "SHOPEE_PARTNER_KEY": "chave-parceiro",
        "TIKTOK_SHOP_APP_KEY": "app-chave",
        "TIKTOK_SHOP_APP_SECRET": "segredo-app",
        "TIKTOK_SHOP_SERVICE_ID": "7000",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name in (*FLAGS.values(), "TIKTOK_CONTENT_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    caches = (
        connect_module._store_cached,
        connect_module._ledger_cached,
        webhook_module._marketplace_store_cached,
    )
    rate_store.clear()
    for cache in caches:
        cache.cache_clear()
    yield tmp_path
    for cache in caches:
        cache.cache_clear()


@pytest.mark.parametrize("canal", ["shopee", "tiktok-shop"])
def test_push_confere_assinatura_antes_e_so_enfileira_uma_vez(
    canal, ambiente, monkeypatch
):
    if canal == "shopee":
        rota, chave, segredo = (
            "/integrations/shopee/push",
            "SHOPEE_SYNC_ENABLED",
            ("SHOPEE_PARTNER_KEY"),
        )
        dados = {"ordersn": "2410ABC123", "status": "READY_TO_SHIP", "buyer_name": "c"}
        corpo = json.dumps({"code": 3, "shop_id": 123, "data": dados}).encode()
        base = b"https://veratus.onrender.com/integrations/shopee/push|" + corpo
        assinatura = hmac.new(b"chave-parceiro", base, hashlib.sha256).hexdigest()
    else:
        rota, chave, segredo = (
            "/integrations/tiktok-shop/webhook",
            ("TIKTOK_SHOP_SYNC_ENABLED"),
            "TIKTOK_SHOP_APP_SECRET",
        )
        dados = {"order_id": "5761234", "order_status": "ON_HOLD", "buyer_email": "c"}
        corpo = json.dumps({"type": 1, "tts_notification_id": "n1", "data": dados})
        corpo = corpo.encode()
        base = b"app-chave" + corpo
        assinatura = hmac.new(b"segredo-app", base, hashlib.sha256).hexdigest()
    client = app.test_client()

    def enviar(header):
        return client.post(rota, data=corpo, headers={"Authorization": header})

    monkeypatch.setenv(segredo, "")
    assert enviar(assinatura).status_code == 503  # sem segredo: a plataforma reenvia
    monkeypatch.setenv(
        segredo, "chave-parceiro" if canal == "shopee" else "segredo-app"
    )
    assert enviar("0" * 64).status_code == 401
    assert enviar(assinatura).status_code == 503  # chave desligada: nada entra
    monkeypatch.setenv(chave, "true")
    primeira, repetida = enviar(assinatura), enviar(assinatura)

    assert (primeira.status_code, primeira.json["status"]) == (200, "accepted")
    assert repetida.json["status"] == "duplicate"
    with sqlite3.connect(ambiente / "mercado-livre-oauth.sqlite3") as db:
        fila = db.execute("SELECT payload_json FROM marketplace_notification_queue")
        linhas = [row[0] for row in fila]
    assert len(linhas) == 1 and "buyer" not in linhas[0]


@pytest.mark.parametrize("canal", ["shopee", "tiktok-shop", "amazon"])
def test_chave_desligada_nao_chama_rede(canal, ambiente, monkeypatch):
    def rede(*args, **kwargs):
        raise AssertionError("chamada de rede com a chave desligada")

    monkeypatch.setattr(requests.Session, "request", rede)
    resposta = app.test_client().post(
        f"/integrations/{canal}/orders/sync", headers=ADMIN
    )
    leitura = {
        "shopee": lambda: ShopeeReadOnlyClient(None).get_order_list(0, 60),
        "tiktok-shop": lambda: TikTokShopClient("k", "s", "t").search_orders(0, 60),
        "amazon": lambda: AmazonSellingPartnerClient("i", "s", "r").get_orders(
            datetime.now(timezone.utc)
        ),
    }

    assert resposta.json == {
        "status": "BLOCKED",
        "channel": canal,
        "reason": "SYNC_DISABLED",
    }
    with pytest.raises(MarketplaceApiError, match="SYNC_DISABLED"):
        leitura[canal]()


def test_pedido_da_shopee_vira_um_pedido_no_ledger_sem_dado_do_comprador(
    ambiente, monkeypatch
):
    monkeypatch.setenv("SHOPEE_SYNC_ENABLED", "true")
    documento = ".".join(["000"] * 3) + "-00"  # montado em tempo de execução
    pago = {"order_sn": "2410ABC123", "order_status": "READY_TO_SHIP"}
    dois_skus = {"order_sn": "2410XYZ999", "order_status": "READY_TO_SHIP"}

    def linha(sku):
        return {
            "item_sku": sku,
            "model_quantity_purchased": 1,
            "model_original_price": 199.9,
            "model_discounted_price": 179.9,
        }

    def detalhe(pedido, *skus):
        return {
            **pedido,
            "currency": "BRL",
            "create_time": 1_760_000_000,
            "buyer_username": "cliente-teste",
            "buyer_cpf_id": documento,
            "recipient_address": {"name": "Cliente Teste", "city": "Recife"},
            "item_list": [linha(sku) for sku in skus],
        }

    def listar(*pedidos):
        return Resp({"error": "", "response": {"order_list": list(pedidos)}})

    detalhes = [detalhe(pago, "corrente-01"), detalhe(dois_skus, "c-1", "c-2")]
    entregue = {**pago, "order_status": "COMPLETED"}
    sessao = Sessao(
        gets=[
            listar(pago, dois_skus),
            Resp({"error": "", "response": {"order_list": detalhes}}),
            listar(entregue),
            listar(entregue),
        ]
    )
    client = ShopeeReadOnlyClient(
        ShopeeTokenManager(Loja(), ShopeeClient(2001887, "chave", session=sessao))
    )
    ledger = make_order_ledger(ambiente / "ops.sqlite3")

    rodadas = [
        sync_orders("shopee", ledger, client_factory=lambda: client) for _ in range(3)
    ]

    assert [item["status"] for item in rodadas[0]["orders"]] == ["IMPORTED", "BLOCKED"]
    assert rodadas[0]["orders"][1]["reason"] == "MULTI_SKU_ORDER"
    assert rodadas[1]["orders"][0]["status"] == "SYNCED"
    assert rodadas[2]["orders"][0]["status"] == "UNCHANGED"
    pedido = ledger.get("shopee-2410ABC123")
    assert (pedido["status"], pedido["sku"], pedido["sale_price"]) == (
        "DELIVERED",
        "corrente-01",
        "199.90",
    )
    assert pedido["discount"] == "20.00"
    assert ledger.get("shopee-2410XYZ999") is None
    assert len(ledger.list_orders()) == 1
    pedido_detalhe = sessao.calls[1][2]["params"]
    assert (
        pedido_detalhe["response_optional_fields"] == "item_list,pay_time,total_amount"
    )
    guardado = json.dumps([ledger.list_orders(), rodadas], default=str)
    assert "cliente" not in guardado.lower() and documento not in guardado


def test_amazon_le_pedidos_sem_rdt_e_so_com_campos_do_ledger(ambiente, monkeypatch):
    monkeypatch.setenv("AMAZON_SYNC_ENABLED", "true")
    pedido = {
        "AmazonOrderId": "702-1234567-1234567",
        "OrderStatus": "Unshipped",
        "PurchaseDate": "2026-10-01T10:00:00Z",
        "BuyerInfo": {"BuyerName": "Cliente Teste"},
        "ShippingAddress": {"City": "Recife"},
    }
    item = {
        "SellerSKU": "corrente-01",
        "QuantityOrdered": 1,
        "ItemPrice": {"CurrencyCode": "BRL", "Amount": "199.90"},
        "Title": "Corrente",
    }
    sessao = Sessao(
        posts=[Resp({"access_token": "lwa", "expires_in": 3600})],
        gets=[
            Resp({"payload": {"Orders": [pedido]}}),
            Resp({"payload": {"OrderItems": [item]}}),
        ],
    )
    client = AmazonSellingPartnerClient("id", "segredo", "refresh", session=sessao)
    ledger = make_order_ledger(ambiente / "ops.sqlite3")

    lidos = client.get_orders(datetime(2026, 10, 1, tzinfo=timezone.utc))
    itens = client.get_order_items(lidos[0]["AmazonOrderId"])
    resultado = import_order(ledger, amazon_order(lidos[0], itens))

    assert set(lidos[0]) <= set(ORDER_FIELDS)
    assert "Title" not in itens[0]
    assert resultado["status"] == "IMPORTED" and resultado["order_status"] == "PAID"
    assert [url.rsplit(".com", 1)[1] for _, url, _ in sessao.calls] == [
        "/auth/o2/token",
        "/orders/v0/orders",
        "/orders/v0/orders/702-1234567-1234567/orderItems",
    ]


def test_tiktok_shop_autoriza_guarda_cifrado_e_renova(ambiente, monkeypatch):
    monkeypatch.setenv("TIKTOK_SHOP_SYNC_ENABLED", "true")
    client = app.test_client()
    inicio = client.get(
        "/integrations/tiktok-shop/oauth/start?format=json", headers=ADMIN
    )
    query = parse_qs(urlparse(inicio.json["authorization_url"]).query)
    agora = int(time.time())

    def get_falso(self, url, **kwargs):
        if url.endswith("/api/v2/token/get"):
            assert kwargs["params"]["auth_code"] == "codigo-12345"
            data = {
                "access_token": "tok-1",
                "access_token_expire_in": agora + 3600,
                "refresh_token": "ref-1",
                "seller_base_region": "BR",
            }
            return Resp({"code": 0, "data": data})
        return Resp({}, 500)  # checagem da conta: fica para depois

    monkeypatch.setattr(requests.Session, "get", get_falso)
    callback = f"/integrations/tiktok-shop/oauth/callback?code=codigo-12345&state={query['state'][0]}"
    primeira, repetida = client.get(callback), client.get(callback)
    store = connect_module.token_store("tiktok-shop")

    assert query["service_id"] == ["7000"]
    assert primeira.status_code == 200 and primeira.json["seller_region"] == "BR"
    assert repetida.status_code == 400  # state de uso único
    assert store.load_tokens().access_token == "tok-1"
    assert b"tok-1" not in (ambiente / "mercado-livre-oauth.sqlite3").read_bytes()

    store.save_tokens(
        {"access_token": "tok-1", "refresh_token": "ref-1", "expires_in": 0}
    )
    novo = {"access_token": "tok-2", "access_token_expire_in": agora + 3600}
    lojas = {"shops": [{"id": "1", "cipher": "c", "region": "BR"}]}
    sessao = Sessao(
        gets=[Resp({"code": 0, "data": novo}), Resp({"code": 0, "data": lojas})]
    )
    loja = TikTokShopClient(
        "app-chave", "segredo-app", session=sessao, token_store=store
    )

    assert loja.account_identity()["shops"][0]["region"] == "BR"
    assert sessao.calls[0][1].endswith("/api/v2/token/refresh")
    assert sessao.calls[1][2]["headers"]["x-tts-access-token"] == "tok-2"


def test_tiktok_so_manda_midia_aprovada_para_a_caixa_de_entrada(monkeypatch):
    video = "https://veratus.onrender.com/social/reels/corrente.mp4"
    item = {"media": [video], "caption": "Corrente em tom dourado"}

    def proibido():
        raise AssertionError("cliente criado sem precisar")

    monkeypatch.delenv("TIKTOK_CONTENT_ENABLED", raising=False)
    desligado = tiktok_content.upload_to_inbox(item, client_factory=proibido)
    monkeypatch.setenv("TIKTOK_CONTENT_ENABLED", "true")
    aprovadas = {}
    monkeypatch.setattr(
        "veratus_agents.social_scheduler.load_approved_media", lambda: aprovadas
    )
    sem_aprovacao = tiktok_content.upload_to_inbox(item, client_factory=proibido)
    aprovadas["/social/reels/corrente.mp4"] = "sha"
    sessao = Sessao(posts=[Resp({"data": {"publish_id": "v_inbox_file~1"}})])
    cliente = tiktok_content.TikTokContentClient(
        "chave", "segredo", Loja(), session=sessao
    )

    enviado = tiktok_content.upload_to_inbox(item, client_factory=lambda: cliente)

    assert desligado == tiktok_content.DISABLED
    assert sem_aprovacao["reason"] == "TIKTOK_MEDIA_BLOCKED"
    assert enviado == {
        "status": "NEEDS_MANUAL",
        "reason": "TIKTOK_INBOX",
        "publish_id": "v_inbox_file~1",
    }
    ((_, url, kwargs),) = sessao.calls
    assert url.endswith("/v2/post/publish/inbox/video/init/")
    assert kwargs["json"] == {
        "source_info": {"source": "PULL_FROM_URL", "video_url": video}
    }
    assert "video.publish" not in cliente.authorization_url("estado")
