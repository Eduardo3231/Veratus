"""Shopee shop authorization and read-only checks, against a simulated API."""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

import integrations.marketplace_connect as connect_module
import integrations.webhook as webhook_module
from integrations.webhook import app, rate_store
from veratus_agents import shopee
from veratus_agents.marketplace_clients import MarketplaceApiError, connection_status
from veratus_agents.mercado_livre_oauth import MarketplaceTokenStore

ADMIN = {"X-Veratus-Admin-Token": "shopee-admin-token"}
PARTNER_ID = 2001887
TOKEN = {
    "access_token": "tok-1",
    "refresh_token": "ref-1",
    "expire_in": 14400,
    "error": "",
}
SHOP = {"shop_name": "Veratus", "region": "BR", "status": "NORMAL", "error": ""}
CATEGORIES = {
    "error": "",
    "response": {
        "category_list": [
            {
                "category_id": 100,
                "display_category_name": "Relógios",
                "has_children": True,
            },
            {
                "category_id": 101,
                "display_category_name": "Relógios de Pulso",
                "has_children": False,
            },
            {"category_id": 5, "display_category_name": "Moda", "has_children": False},
        ]
    },
}


class Response:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return self.data


class Session:
    def __init__(self, *, gets=(), posts=()):
        self.gets = list(gets)
        self.posts = list(posts)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.gets.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.posts.pop(0)


@pytest.fixture
def shopee_env(tmp_path):
    env = {
        "VERATUS_ADMIN_TOKEN": ADMIN["X-Veratus-Admin-Token"],
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "VERATUS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "SHOPEE_PARTNER_ID": str(PARTNER_ID),
        "SHOPEE_PARTNER_KEY": "test-partner-key",
        "DATABASE_URL": "",
    }
    rate_store.clear()
    connect_module._store_cached.cache_clear()
    webhook_module._marketplace_store_cached.cache_clear()
    with patch.dict("os.environ", env, clear=False):
        yield env
    connect_module._store_cached.cache_clear()
    webhook_module._marketplace_store_cached.cache_clear()


def _fake_api(monkeypatch, session: Session) -> None:
    monkeypatch.setattr(
        shopee.ShopeeClient,
        "from_env",
        classmethod(
            lambda cls, session_=None: cls(
                PARTNER_ID, "test-partner-key", session=session
            )
        ),
    )


def test_sign_follows_the_documented_base_string():
    expected = hmac.new(
        b"key", b"2001887/api/v2/shop/get_shop_info1655714431tok-1123", hashlib.sha256
    ).hexdigest()

    assert (
        shopee.sign(
            "key", PARTNER_ID, "/api/v2/shop/get_shop_info", 1655714431, "tok-1", "123"
        )
        == expected
    )


def test_start_requires_admin_and_signs_the_authorization_link(shopee_env):
    client = app.test_client()
    assert client.get("/integrations/shopee/oauth/start").status_code == 401

    response = client.get("/integrations/shopee/oauth/start?format=json", headers=ADMIN)

    assert response.status_code == 200
    link = urlparse(response.json["authorization_url"])
    query = parse_qs(link.query)
    assert f"{link.scheme}://{link.netloc}{link.path}" == (
        "https://partner.shopeemobile.com/api/v2/shop/auth_partner"
    )
    assert query["partner_id"] == [str(PARTNER_ID)]
    assert len(query["sign"][0]) == 64
    redirect_url = query["redirect"][0]
    assert redirect_url.startswith(
        "https://veratus.onrender.com/integrations/shopee/oauth/callback/"
    )
    assert len(redirect_url.rsplit("/", 1)[1]) >= 32
    assert "test-partner-key" not in response.get_data(as_text=True)


def test_start_explains_missing_app_keys(shopee_env):
    with patch.dict("os.environ", {"SHOPEE_PARTNER_ID": ""}):
        response = app.test_client().get(
            "/integrations/shopee/oauth/start?format=json", headers=ADMIN
        )

    assert response.status_code == 503
    assert response.json["message"] == "oauth_configuration_incomplete"


def test_callback_stores_the_shop_token_and_checks_the_account(shopee_env, monkeypatch):
    api = Session(posts=[Response(TOKEN)], gets=[Response(SHOP), Response(CATEGORIES)])
    _fake_api(monkeypatch, api)
    client = app.test_client()
    link = client.get("/integrations/shopee/oauth/start?format=json", headers=ADMIN)
    callback = urlparse(
        parse_qs(urlparse(link.json["authorization_url"]).query)["redirect"][0]
    )

    response = client.get(f"{callback.path}?code=auth-code-123&shop_id=555")

    assert response.status_code == 200
    assert response.json["status"] == "connected_read_only"
    assert response.json["external_writes"] is False
    body = response.get_data(as_text=True)
    assert "tok-1" not in body and "ref-1" not in body
    token_call = api.calls[0]
    assert token_call[1].endswith("/api/v2/auth/token/get")
    assert token_call[2]["json"] == {
        "code": "auth-code-123",
        "shop_id": 555,
        "partner_id": PARTNER_ID,
    }
    status = client.get("/integrations/shopee/status", headers=ADMIN)
    assert status.json["status"] == "authorized"
    check = webhook_module._marketplace_store().connection_checks()
    shopee_check = next(item for item in check if item["channel"] == "shopee")
    assert shopee_check["readiness"] == "CONNECTED_READ_ONLY"
    assert shopee_check["external_category"]["external_category_id"] == 101
    # The same state cannot be used twice.
    replay = client.get(f"{callback.path}?code=auth-code-123&shop_id=555")
    assert replay.status_code == 400


def test_callback_rejects_bad_state_and_main_account(shopee_env):
    client = app.test_client()
    assert (
        client.get(
            "/integrations/shopee/oauth/callback/forged?code=abcdefgh&shop_id=1"
        ).status_code
        == 400
    )
    link = client.get("/integrations/shopee/oauth/start?format=json", headers=ADMIN)
    path = urlparse(
        parse_qs(urlparse(link.json["authorization_url"]).query)["redirect"][0]
    ).path

    response = client.get(f"{path}?code=abcdefgh&main_account_id=9")

    assert response.status_code == 400
    assert response.json["message"] == "authorize_a_single_shop"


def test_expired_token_is_refreshed_and_auth_errors_retry_once(tmp_path):
    store = MarketplaceTokenStore(
        encryption_key=Fernet.generate_key().decode("ascii"),
        sqlite_path=tmp_path / "tokens.sqlite3",
        channel="shopee",
    )
    store.save_tokens(
        {
            "access_token": "old",
            "refresh_token": "ref-1",
            "expires_in": 0,
            "user_id": "555",
        }
    )
    api = Session(
        posts=[
            Response({**TOKEN, "access_token": "tok-2"}),
            Response({**TOKEN, "access_token": "tok-3"}),
        ],
        gets=[Response({"error": "invalid_access_token"}), Response(SHOP)],
    )
    client = shopee.ShopeeReadOnlyClient(
        shopee.ShopeeTokenManager(
            store, shopee.ShopeeClient(PARTNER_ID, "k", session=api)
        )
    )

    account = client.account_identity()

    assert account["shop_name"] == "Veratus" and account["id"] == "555"
    assert [call[1].rsplit("/", 1)[1] for call in api.calls] == [
        "get",  # expired: refresh first
        "get_shop_info",
        "get",  # 401: refresh once more
        "get_shop_info",
    ]
    assert api.calls[0][2]["json"]["refresh_token"] == "ref-1"
    assert store.load_tokens().access_token == "tok-3"


def test_remote_errors_carry_only_the_code():
    api = Session(
        gets=[Response({"error": "error_param", "message": "shop 555 secret detail"})]
    )
    client = shopee.ShopeeClient(PARTNER_ID, "k", session=api)

    with pytest.raises(MarketplaceApiError) as error:
        client.shop_get("/api/v2/shop/get_shop_info", "tok", "555")

    assert error.value.code == "SHOPEE_ERROR_PARAM"
    assert "secret" not in str(error.value)


def test_app_keys_alone_do_not_count_as_a_connected_shop(shopee_env):
    assert connection_status()["shopee"]["credentials"] == "MISSING"

    MarketplaceTokenStore.from_env("shopee").save_tokens(
        {
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_in": 14400,
            "user_id": "555",
        }
    )

    assert connection_status()["shopee"]["credentials"] == "PRESENT"
