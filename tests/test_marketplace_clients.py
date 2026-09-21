from __future__ import annotations

from unittest.mock import patch

import pytest

from veratus_agents.marketplace_clients import (
    MarketplaceApiError,
    MercadoLivreReadOnlyClient,
    TikTokShopReadOnlyClient,
    assess_origin,
    connection_status,
    credential_matrix,
)


class Response:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return self.data


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return next(self.responses)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return next(self.responses)


def test_credentials_missing_are_reported_without_values():
    names = {item["variable"] for item in credential_matrix()}
    with patch.dict("os.environ", {name: "" for name in names}, clear=False):
        matrix = credential_matrix()
        status = connection_status()

    assert all(item["status"] == "MISSING" for item in matrix)
    assert status["mercado-livre"]["auth"] == "MISSING"
    assert all("value" not in item for item in matrix)


def test_credentials_present_remain_untested_until_real_auth():
    with patch.dict(
        "os.environ", {"MERCADO_LIVRE_ACCESS_TOKEN": "secret"}, clear=False
    ):
        status = connection_status()["mercado-livre"]

    assert status["credentials"] == "PRESENT"
    assert status["auth"] == "NOT_TESTED"


def test_mercado_livre_account_category_attribute_and_lookup_reads():
    session = Session(
        [
            Response(
                {"id": 123, "site_id": "MLB", "status": {"site_status": "active"}}
            ),
            Response(
                [
                    {
                        "category_id": "MLB1",
                        "category_name": "Relógios",
                        "domain_id": "MLB-WATCHES",
                    }
                ]
            ),
            Response(
                [
                    {
                        "id": "MATERIAL",
                        "name": "Material",
                        "tags": {"required": True},
                        "value_type": "string",
                    }
                ]
            ),
            Response({"results": ["MLB123"], "paging": {"total": 1}}),
        ]
    )
    client = MercadoLivreReadOnlyClient("secret", session=session)

    assert client.account_identity()["id"] == 123
    assert (
        client.discover_categories("Relógio automático")[0]["external_category_id"]
        == "MLB1"
    )
    assert client.discover_attributes("MLB1")[0]["classification"] == "REQUIRED"
    assert client.lookup_listing("123", "arctic-white")["remote_ids"] == ["MLB123"]
    assert all(call[0] == "GET" for call in session.calls)


def test_auth_failure_is_normalized_without_remote_body():
    client = MercadoLivreReadOnlyClient(
        "invalid", session=Session([Response({"message": "token details"}, 401)])
    )
    with pytest.raises(MarketplaceApiError, match="AUTH_INVALID_OR_EXPIRED"):
        client.account_identity()


def test_tiktok_signature_and_read_only_account_call():
    session = Session(
        [
            Response(
                {
                    "code": 0,
                    "data": {"shops": [{"id": "1", "cipher": "c", "region": "BR"}]},
                }
            )
        ]
    )
    client = TikTokShopReadOnlyClient("app", "secret", "token", session=session)

    assert client.account_identity()["shops"][0]["region"] == "BR"
    query = session.calls[0][2]["params"]
    assert query["app_key"] == "app"
    assert len(query["sign"]) == 64
    assert "token" not in str(query)


def test_origin_unsupported_is_explicit_and_never_replaced():
    result = assess_origin(
        channel="mercado-livre", origin_country="CH", supported_countries=["BR"]
    )
    assert result["status"] == "ORIGIN_NOT_SUPPORTED"
    assert "não substituir" in result["what_must_change"]
