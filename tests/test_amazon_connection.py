"""Amazon SP-API read-only connection, against a simulated API."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import integrations.webhook as webhook_module
from integrations.webhook import app, rate_store
from veratus_agents import amazon_sp
from veratus_agents.marketplace_clients import MarketplaceApiError, connection_status

ADMIN = {"X-Veratus-Admin-Token": "amazon-admin-token"}
LWA = {"access_token": "Atza|tok-1", "expires_in": 3600, "token_type": "bearer"}
PARTICIPATIONS = {
    "payload": [
        {
            "marketplace": {"id": "ATVPDKIKX0DER", "countryCode": "US"},
            "participation": {"isParticipating": False, "hasSuspendedListings": False},
        },
        {
            "marketplace": {"id": "A2Q3Y263D00KWC", "countryCode": "BR"},
            "participation": {"isParticipating": True, "hasSuspendedListings": False},
        },
    ]
}
PRODUCT_TYPES = {"productTypes": [{"name": "WATCH", "displayName": "Relógio"}]}


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


def _client(session: Session) -> amazon_sp.AmazonSellingPartnerClient:
    return amazon_sp.AmazonSellingPartnerClient(
        "amzn1.application-oa2-client.x",
        "secret-value",
        "Atzr|refresh",
        session=session,
    )


def test_lwa_token_is_requested_once_and_sent_in_the_amazon_header():
    api = Session(
        posts=[Response(LWA)],
        gets=[Response(PARTICIPATIONS), Response(PRODUCT_TYPES)],
    )
    client = _client(api)

    account = client.account_identity()
    categories = client.discover_categories()

    token_call = api.calls[0]
    assert token_call[1] == "https://api.amazon.com/auth/o2/token"
    assert token_call[2]["data"] == {
        "grant_type": "refresh_token",
        "refresh_token": "Atzr|refresh",
        "client_id": "amzn1.application-oa2-client.x",
        "client_secret": "secret-value",
    }
    reads = [call for call in api.calls if call[0] == "GET"]
    assert [call[1] for call in reads] == [
        "https://sellingpartnerapi-na.amazon.com/sellers/v1/marketplaceParticipations",
        "https://sellingpartnerapi-na.amazon.com/definitions/2020-09-01/productTypes",
    ]
    assert all(
        call[2]["headers"]["x-amz-access-token"] == "Atza|tok-1" for call in reads
    )
    assert reads[1][2]["params"]["marketplaceIds"] == "A2Q3Y263D00KWC"
    assert len([call for call in api.calls if call[0] == "POST"]) == 1
    assert (
        account["participating"] is True
        and account["marketplace_id"] == "A2Q3Y263D00KWC"
    )
    assert categories == [
        {
            "external_category_id": "WATCH",
            "external_category_name": "Relógio",
            "source": "Amazon Product Type Definitions",
        }
    ]


def test_an_expired_token_gets_one_forced_refresh():
    api = Session(
        posts=[Response(LWA), Response({**LWA, "access_token": "Atza|tok-2"})],
        gets=[
            Response({"errors": [{"code": "Unauthorized"}]}, 401),
            Response(PARTICIPATIONS),
        ],
    )

    _client(api).account_identity()

    reads = [call for call in api.calls if call[0] == "GET"]
    assert [call[2]["headers"]["x-amz-access-token"] for call in reads] == [
        "Atza|tok-1",
        "Atza|tok-2",
    ]


def test_errors_carry_only_amazon_codes():
    api = Session(
        posts=[Response(LWA)],
        gets=[
            Response(
                {"errors": [{"code": "InvalidInput", "message": "seller 123 detail"}]},
                400,
            )
        ],
    )

    with pytest.raises(MarketplaceApiError) as error:
        _client(api).account_identity()

    assert error.value.code == "AMAZON_INVALIDINPUT"
    assert "detail" not in str(error.value)
    revoked = Session(posts=[Response({"error": "invalid_grant"}, 400)])
    with pytest.raises(MarketplaceApiError, match="AMAZON_LWA_REFRESH_FAILED"):
        _client(revoked).account_identity()


@pytest.fixture
def amazon_env(tmp_path):
    env = {
        "VERATUS_ADMIN_TOKEN": ADMIN["X-Veratus-Admin-Token"],
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "AMAZON_SP_LWA_CLIENT_ID": "amzn1.application-oa2-client.x",
        "AMAZON_SP_LWA_CLIENT_SECRET": "secret-value",
        "AMAZON_SP_REFRESH_TOKEN": "Atzr|refresh",
    }
    rate_store.clear()
    webhook_module._marketplace_store_cached.cache_clear()
    with patch.dict("os.environ", env, clear=False):
        yield env
    webhook_module._marketplace_store_cached.cache_clear()


def test_status_shows_which_settings_exist_never_their_values(amazon_env):
    client = app.test_client()
    assert client.get("/integrations/amazon/status").status_code == 401

    with patch.dict("os.environ", {"AMAZON_SP_REFRESH_TOKEN": ""}):
        response = client.get("/integrations/amazon/status", headers=ADMIN)

    assert response.json["status"] == "not_configured"
    assert response.json["configuration"] == {
        "AMAZON_SP_LWA_CLIENT_ID": True,
        "AMAZON_SP_LWA_CLIENT_SECRET": True,
        "AMAZON_SP_REFRESH_TOKEN": False,
    }
    assert "secret-value" not in response.get_data(as_text=True)


def test_check_confirms_the_brazil_store_read_only(amazon_env, monkeypatch):
    api = Session(
        posts=[Response(LWA)], gets=[Response(PARTICIPATIONS), Response(PRODUCT_TYPES)]
    )
    monkeypatch.setattr(
        amazon_sp.AmazonSellingPartnerClient,
        "from_env",
        classmethod(lambda cls, session=None: _client(api)),
    )

    response = app.test_client().post("/integrations/amazon/check", headers=ADMIN)

    report = response.json["report"]
    assert report["readiness"] == "CONNECTED_READ_ONLY"
    assert report["account_info"]["participating"] is True
    assert report["external_category"]["external_category_id"] == "WATCH"
    body = response.get_data(as_text=True)
    assert "Atza|tok-1" not in body and "secret-value" not in body
    status = app.test_client().get("/integrations/amazon/status", headers=ADMIN)
    assert status.json["last_check"]["readiness"] == "CONNECTED_READ_ONLY"


def test_account_without_the_brazil_store_is_not_ready(amazon_env, monkeypatch):
    only_us = {"payload": PARTICIPATIONS["payload"][:1]}
    api = Session(posts=[Response(LWA)], gets=[Response(only_us)])
    monkeypatch.setattr(
        amazon_sp.AmazonSellingPartnerClient,
        "from_env",
        classmethod(lambda cls, session=None: _client(api)),
    )

    report = (
        app.test_client()
        .post("/integrations/amazon/check", headers=ADMIN)
        .json["report"]
    )

    assert report["readiness"] == "NOT_READY"
    assert report["errors"] == ["AMAZON_BR_MARKETPLACE_NOT_ACTIVE"]


def test_connection_status_counts_amazon_only_when_configured(amazon_env):
    assert connection_status()["amazon"]["credentials"] == "PRESENT"
    with patch.dict("os.environ", {"AMAZON_SP_LWA_CLIENT_SECRET": ""}):
        assert connection_status()["amazon"]["credentials"] == "MISSING"
