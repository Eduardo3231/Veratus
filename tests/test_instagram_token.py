"""Renovação automática do token longo do Instagram, com alerta se falhar."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet

from integrations import webhook
from veratus_agents.instagram_token import InstagramTokenStore

KEY = Fernet.generate_key().decode()
T0 = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
ADMIN = {"X-Veratus-Admin-Token": "admin-test-key"}


class _Response:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code, self.ok, self._body = status, status < 400, body

    def json(self) -> dict:
        return self._body


class _Graph:
    def __init__(self, *responses: _Response) -> None:
        self.responses, self.calls = list(responses), []

    def get(self, url: str, params: dict, timeout: int) -> _Response:
        self.calls.append(params)
        return self.responses.pop(0)


def _store(tmp_path, **changes) -> InstagramTokenStore:
    values = {"encryption_key": KEY, "seed_token": "token-semente"}
    values.update(changes)
    return InstagramTokenStore(tmp_path / "ops.sqlite3", **values)


def test_env_token_is_stored_encrypted_and_waits_24_hours(tmp_path) -> None:
    store = _store(tmp_path)

    assert store.refresh_if_due(now=T0)["status"] == "NOT_DUE"
    raw = (tmp_path / "ops.sqlite3").read_bytes()
    assert b"token-semente" not in raw
    assert store.current() == "token-semente"
    assert store.refresh_if_due(now=T0 + timedelta(hours=23))["status"] == "NOT_DUE"


def test_renews_before_expiry_and_keeps_the_new_token(tmp_path) -> None:
    store = _store(tmp_path)
    store.refresh_if_due(now=T0)
    graph = _Graph(
        _Response(200, {"access_token": "token-novo", "expires_in": 5184000})
    )

    result = store.refresh_if_due(now=T0 + timedelta(days=1), session=graph)

    assert result["status"] == "REFRESHED"
    assert graph.calls[0]["grant_type"] == "ig_refresh_token"
    assert graph.calls[0]["access_token"] == "token-semente"
    assert store.current() == "token-novo"
    status = store.status(now=T0 + timedelta(days=1))
    assert status["days_left"] == 60.0 and status["alert"] is False
    assert "token-novo" not in str(status)
    later = T0 + timedelta(days=45)
    assert store.refresh_if_due(now=later)["status"] == "NOT_DUE"
    assert (
        store.refresh_if_due(
            now=T0 + timedelta(days=52),
            session=_Graph(
                _Response(200, {"access_token": "token-3", "expires_in": 5184000})
            ),
        )["status"]
        == "REFRESHED"
    )


def test_failure_raises_an_alert_without_leaking_the_token(tmp_path, caplog) -> None:
    store = _store(tmp_path)
    store.refresh_if_due(now=T0)
    graph = _Graph(_Response(400, {"error": {"code": 190}}))

    with caplog.at_level(logging.ERROR, logger="veratus.instagram_token"):
        result = store.refresh_if_due(now=T0 + timedelta(days=2), session=graph)

    assert result == {"status": "FAILED", "error": "HTTP_400", "alert": True}
    assert store.status()["alert"] is True and store.status()["failures"] == 1
    assert "ALERT instagram_token_refresh_failed" in caplog.text
    assert "token-semente" not in caplog.text
    assert store.current() == "token-semente"


def test_only_one_worker_renews_at_a_time(tmp_path) -> None:
    first, second = _store(tmp_path), _store(tmp_path)
    first.refresh_if_due(now=T0)
    graph = _Graph(
        _Response(200, {"access_token": "token-novo", "expires_in": 5184000})
    )
    due = T0 + timedelta(days=1)

    # The second worker checks while the first holds the reservation.
    assert first._claim(due)
    assert second.refresh_if_due(now=due, session=graph)["status"] == (
        "IN_PROGRESS_ELSEWHERE"
    )
    assert graph.calls == []


def test_missing_encryption_key_blocks_and_alerts(tmp_path) -> None:
    store = _store(tmp_path, encryption_key="")

    assert store.refresh_if_due(now=T0)["status"] == "BLOCKED"
    assert store.status()["alert"] is True


def test_admin_status_never_returns_the_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VERATUS_AGENT_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("VERATUS_ADMIN_TOKEN", "admin-test-key")
    monkeypatch.setenv("VERATUS_TOKEN_ENCRYPTION_KEY", KEY)
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "token-semente")
    webhook.rate_store.clear()
    webhook.app.config.update(TESTING=True)
    client = webhook.app.test_client()

    assert client.get("/integrations/instagram/token/status").status_code == 401
    response = client.get("/integrations/instagram/token/status", headers=ADMIN)

    assert response.status_code == 200
    assert response.get_json()["token_present"] is True
    assert b"token-semente" not in response.data


def test_client_sends_with_the_renewed_token(tmp_path, monkeypatch) -> None:
    from veratus_agents.instagram_comments import InstagramClient

    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "token-semente")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "ig-user")
    store = _store(tmp_path)
    store.refresh_if_due(now=T0)
    store.refresh_if_due(
        now=T0 + timedelta(days=1),
        session=_Graph(
            _Response(200, {"access_token": "token-novo", "expires_in": 5184000})
        ),
    )

    assert InstagramClient.from_env(token=store.current()).access_token == "token-novo"
    assert InstagramClient.from_env().access_token == "token-semente"
