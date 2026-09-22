from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

import integrations.webhook as webhook_module
from integrations.webhook import app, rate_store
from veratus_agents.marketplace_clients import (
    MarketplaceApiError,
    MercadoLivreOAuthTokenManager,
)
from veratus_agents.mercado_livre_oauth import (
    MercadoLivreOAuthStore,
    OAuthStateError,
)


class Response:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return self.data


class Session:
    def __init__(self, *, gets=None, posts=None):
        self.gets = list(gets or [])
        self.posts = list(posts or [])
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.gets.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.posts.pop(0)


def test_public_landing_is_still_served():
    response = app.test_client().get("/")

    assert response.status_code == 200
    assert "VERATUS" in response.get_data(as_text=True)


@pytest.fixture
def oauth_env(tmp_path):
    env = {
        "VERATUS_ADMIN_TOKEN": "oauth-admin-token",
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "VERATUS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "MERCADO_LIVRE_CLIENT_ID": "123456",
        "MERCADO_LIVRE_CLIENT_SECRET": "client-secret-value",
        "MERCADO_LIVRE_REDIRECT_URI": "https://veratus.onrender.com/integrations/mercado-livre/oauth/callback",
        "MERCADO_LIVRE_OAUTH_STATE_TTL_SECONDS": "600",
    }
    rate_store.clear()
    webhook_module._mercado_oauth_store_cached.cache_clear()
    webhook_module._operational_runtime_instance = None
    webhook_module._runtime_engine = None
    with patch.dict("os.environ", env, clear=False):
        yield env
    webhook_module._mercado_oauth_store_cached.cache_clear()
    webhook_module._operational_runtime_instance = None
    webhook_module._runtime_engine = None


def test_state_is_random_single_use_and_expires(tmp_path):
    store = MercadoLivreOAuthStore(
        encryption_key=Fernet.generate_key().decode("ascii"),
        sqlite_path=tmp_path / "oauth.sqlite3",
    )
    first = store.create_state()
    second = store.create_state()

    assert first != second
    assert len(first) >= 32
    store.consume_state(first)
    with pytest.raises(OAuthStateError):
        store.consume_state(first)
    with pytest.raises(OAuthStateError):
        store.consume_state("not-a-real-state")


def test_oauth_start_requires_admin_and_returns_official_url(oauth_env):
    client = app.test_client()
    assert client.get("/integrations/mercado-livre/oauth/start").status_code == 401

    response = client.get(
        "/integrations/mercado-livre/oauth/start?format=json",
        headers={"X-Veratus-Admin-Token": oauth_env["VERATUS_ADMIN_TOKEN"]},
    )

    assert response.status_code == 200
    parsed = urlparse(response.json["authorization_url"])
    query = parse_qs(parsed.query)
    assert parsed.netloc == "auth.mercadolivre.com.br"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == [oauth_env["MERCADO_LIVRE_CLIENT_ID"]]
    assert query["redirect_uri"] == [oauth_env["MERCADO_LIVRE_REDIRECT_URI"]]
    assert len(query["state"][0]) >= 32


def test_oauth_callback_rejects_invalid_state_without_exchange(oauth_env):
    client = app.test_client()
    with patch(
        "veratus_agents.marketplace_clients.MercadoLivreReadOnlyClient.exchange_authorization_code"
    ) as exchange:
        response = client.get(
            "/integrations/mercado-livre/oauth/callback?state=invalid&code=valid-code-123"
        )

    assert response.status_code == 400
    assert response.json == {"status": "error", "message": "invalid_oauth_state"}
    exchange.assert_not_called()


def test_oauth_callback_sanitizes_persistence_failure(oauth_env):
    client = app.test_client()
    with patch.object(
        webhook_module, "_mercado_oauth_store", side_effect=RuntimeError("db-secret")
    ):
        response = client.get(
            "/integrations/mercado-livre/oauth/callback?state=invalid&code=valid-code-123"
        )

    assert response.status_code == 503
    assert response.json == {
        "status": "blocked",
        "message": "oauth_persistence_unavailable",
    }
    assert "db-secret" not in response.get_data(as_text=True)


def test_oauth_callback_persists_tokens_without_exposing_them(oauth_env):
    client = app.test_client()
    start = client.get(
        "/integrations/mercado-livre/oauth/start?format=json",
        headers={"X-Veratus-Admin-Token": oauth_env["VERATUS_ADMIN_TOKEN"]},
    )
    state = parse_qs(urlparse(start.json["authorization_url"]).query)["state"][0]
    execution = {
        "tasks": [
            {
                "action": "CONSOLIDATE_MERCADO_LIVRE_READINESS",
                "result": {"read_only_connection": True},
            }
        ]
    }
    token_payload = {
        "access_token": "access-secret-value",
        "refresh_token": "refresh-secret-value",
        "expires_in": 21600,
        "user_id": 42,
        "scope": "offline_access read",
    }
    with (
        patch(
            "veratus_agents.marketplace_clients.MercadoLivreReadOnlyClient.exchange_authorization_code",
            return_value=token_payload,
        ),
        patch.object(
            webhook_module._operational_runtime(), "execute", return_value=execution
        ),
    ):
        response = client.get(
            f"/integrations/mercado-livre/oauth/callback?state={state}&code=valid-code-123"
        )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.json["status"] == "connected_read_only"
    assert response.json["publish_enabled"] is False
    assert "access-secret-value" not in body
    assert "refresh-secret-value" not in body
    stored = webhook_module._mercado_oauth_store().load_tokens()
    assert stored.access_token == "access-secret-value"
    assert stored.refresh_token == "refresh-secret-value"


def test_callback_error_consumes_state_and_redacts_description(oauth_env):
    client = app.test_client()
    start = client.get(
        "/integrations/mercado-livre/oauth/start?format=json",
        headers={"X-Veratus-Admin-Token": oauth_env["VERATUS_ADMIN_TOKEN"]},
    )
    state = parse_qs(urlparse(start.json["authorization_url"]).query)["state"][0]
    response = client.get(
        "/integrations/mercado-livre/oauth/callback",
        query_string={
            "state": state,
            "error": "access_denied",
            "error_description": "do-not-reflect-this-value",
        },
    )

    assert response.status_code == 400
    assert "do-not-reflect-this-value" not in response.get_data(as_text=True)
    replay = client.get(
        "/integrations/mercado-livre/oauth/callback",
        query_string={"state": state, "code": "valid-code-123"},
    )
    assert replay.json["message"] == "invalid_oauth_state"


def test_token_refresh_rotates_refresh_token_and_retries_once(tmp_path):
    store = MercadoLivreOAuthStore(
        encryption_key=Fernet.generate_key().decode("ascii"),
        sqlite_path=tmp_path / "oauth.sqlite3",
    )
    store.save_tokens(
        {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_in": 3600,
            "user_id": 42,
        }
    )
    session = Session(
        gets=[Response({}, 401), Response({"id": 42, "site_id": "MLB", "status": {}})],
        posts=[
            Response(
                {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 21600,
                    "user_id": 42,
                }
            )
        ],
    )
    manager = MercadoLivreOAuthTokenManager(
        store,
        client_id="123456",
        client_secret="secret",
        redirect_uri="https://veratus.onrender.com/callback",
        session=session,
    )

    assert manager.client().account_identity()["id"] == 42
    assert [call[0] for call in session.calls] == ["GET", "POST", "GET"]
    assert store.load_tokens().refresh_token == "new-refresh"
    assert "old-refresh" not in str(session.calls[0])


def test_expired_token_refreshes_before_remote_call(tmp_path):
    store = MercadoLivreOAuthStore(
        encryption_key=Fernet.generate_key().decode("ascii"),
        sqlite_path=tmp_path / "oauth.sqlite3",
    )
    store.save_tokens(
        {
            "access_token": "expired-access",
            "refresh_token": "refresh-once",
            "expires_in": 0,
            "user_id": 42,
        }
    )
    session = Session(
        gets=[Response({"id": 42, "site_id": "MLB", "status": {}})],
        posts=[
            Response(
                {
                    "access_token": "fresh-access",
                    "refresh_token": "rotated-refresh",
                    "expires_in": 21600,
                    "user_id": 42,
                }
            )
        ],
    )
    manager = MercadoLivreOAuthTokenManager(
        store,
        client_id="123456",
        client_secret="secret",
        redirect_uri="https://veratus.onrender.com/callback",
        session=session,
    )

    assert manager.client().health_check()["status"] == "HEALTHY"
    assert [call[0] for call in session.calls] == ["POST", "GET"]


def test_notification_receipt_is_validated_and_idempotent(oauth_env):
    store = webhook_module._mercado_oauth_store()
    store.save_tokens(
        {
            "access_token": "access-secret-value",
            "refresh_token": "refresh-secret-value",
            "expires_in": 21600,
            "user_id": 42,
        }
    )
    payload = {
        "_id": "notification-123",
        "resource": "/items/MLB123",
        "user_id": 42,
        "topic": "items",
        "application_id": 123456,
        "attempts": 1,
        "sent": "2026-09-21T10:00:00Z",
        "received": "2026-09-21T10:00:01Z",
    }
    client = app.test_client()

    first = client.post("/integrations/mercado-livre/notifications", json=payload)
    second = client.post(
        "/integrations/mercado-livre/notifications", json={**payload, "attempts": 2}
    )
    invalid = client.post(
        "/integrations/mercado-livre/notifications",
        json={**payload, "application_id": 999999},
    )

    assert first.status_code == 200
    assert first.json["status"] == "accepted"
    assert first.json["processing"] == "PENDING_SYNC_MONITOR"
    assert second.status_code == 200
    assert second.json["status"] == "duplicate"
    assert invalid.status_code == 400
    assert store.notification_count() == 1


def test_notification_rejects_source_outside_allowlist(oauth_env):
    client = app.test_client()
    with patch.dict(
        "os.environ",
        {"MERCADO_LIVRE_NOTIFICATION_IP_ALLOWLIST": "44.212.211.213"},
        clear=False,
    ):
        response = client.post(
            "/integrations/mercado-livre/notifications",
            json={
                "resource": "/items/MLB123",
                "user_id": 42,
                "topic": "items",
                "application_id": 123456,
            },
        )

    assert response.status_code == 403
    assert response.json["message"] == "notification_source_denied"


def test_exchange_failure_response_does_not_expose_code_or_secrets(oauth_env):
    client = app.test_client()
    start = client.get(
        "/integrations/mercado-livre/oauth/start?format=json",
        headers={"X-Veratus-Admin-Token": oauth_env["VERATUS_ADMIN_TOKEN"]},
    )
    state = parse_qs(urlparse(start.json["authorization_url"]).query)["state"][0]
    code = "sensitive-code-123"
    with patch(
        "veratus_agents.marketplace_clients.MercadoLivreReadOnlyClient.exchange_authorization_code",
        side_effect=MarketplaceApiError("AUTH_INVALID_OR_EXPIRED", http_status=401),
    ):
        response = client.get(
            "/integrations/mercado-livre/oauth/callback",
            query_string={"state": state, "code": code},
        )

    body = response.get_data(as_text=True)
    assert response.status_code == 502
    assert code not in body
    assert oauth_env["MERCADO_LIVRE_CLIENT_SECRET"] not in body
