from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

CHANNEL = "mercado-livre"


class OAuthConfigurationError(RuntimeError):
    pass


class OAuthStateError(RuntimeError):
    pass


class TokenStorageError(RuntimeError):
    pass


def _valid_postgres_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"postgres", "postgresql"}
        and parsed.hostname
        and parsed.path not in {"", "/"}
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _event_id(payload: dict[str, Any]) -> str:
    provider_id = payload.get("_id") or payload.get("id")
    if provider_id:
        basis = f"provider:{provider_id}"
    else:
        stable = {
            key: payload.get(key)
            for key in ("topic", "resource", "user_id", "application_id", "sent")
        }
        basis = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return "ml_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    user_id: str | None
    scope: str | None

    def public_status(self) -> dict[str, Any]:
        return {
            "stored": True,
            "expires_at": _iso(self.expires_at) if self.expires_at else None,
            "expired": bool(self.expires_at and self.expires_at <= _utcnow()),
            "user_id_present": bool(self.user_id),
            "refresh_token_present": bool(self.refresh_token),
        }


class MercadoLivreOAuthStore:
    """Durable encrypted OAuth state, tokens and notification queue.

    PostgreSQL is used whenever DATABASE_URL is configured. SQLite exists only for
    local development and tests. Raw credentials never leave this class.
    """

    def __init__(
        self,
        *,
        encryption_key: str,
        database_url: str | None = None,
        sqlite_path: str | Path | None = None,
    ) -> None:
        if not encryption_key:
            raise OAuthConfigurationError("TOKEN_ENCRYPTION_KEY_MISSING")
        try:
            self._cipher = Fernet(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise OAuthConfigurationError("TOKEN_ENCRYPTION_KEY_INVALID") from exc
        if database_url and not _valid_postgres_url(database_url):
            raise OAuthConfigurationError("DATABASE_URL_INVALID")
        self.database_url = database_url
        self.sqlite_path = Path(sqlite_path or "runtime/mercado-livre-oauth.sqlite3")
        if not self.database_url:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @classmethod
    def from_env(cls) -> MercadoLivreOAuthStore:
        from .config import AgentSettings

        settings = AgentSettings.from_env()
        return cls(
            encryption_key=os.getenv("VERATUS_TOKEN_ENCRYPTION_KEY", "").strip(),
            database_url=os.getenv("DATABASE_URL", "").strip() or None,
            sqlite_path=settings.runtime_dir / "mercado-livre-oauth.sqlite3",
        )

    def _connect_postgres(self):
        try:
            import psycopg
        except ImportError as exc:
            raise OAuthConfigurationError("POSTGRES_DRIVER_MISSING") from exc
        return psycopg.connect(self.database_url, connect_timeout=5)

    def _ensure_schema(self) -> None:
        if self.database_url:
            with self._connect_postgres() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS marketplace_oauth_states (
                        state_hash TEXT PRIMARY KEY,
                        channel TEXT NOT NULL,
                        expires_at TIMESTAMPTZ NOT NULL,
                        consumed_at TIMESTAMPTZ
                    );
                    CREATE TABLE IF NOT EXISTS marketplace_oauth_tokens (
                        channel TEXT PRIMARY KEY,
                        encrypted_payload TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS marketplace_notification_queue (
                        event_id TEXT PRIMARY KEY,
                        channel TEXT NOT NULL,
                        topic TEXT NOT NULL,
                        resource TEXT NOT NULL,
                        application_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        processed_at TIMESTAMPTZ
                    );
                    CREATE INDEX IF NOT EXISTS idx_marketplace_notification_pending
                        ON marketplace_notification_queue(channel, status, created_at);
                    """
                )
            return
        with sqlite3.connect(self.sqlite_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS marketplace_oauth_states (
                    state_hash TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS marketplace_oauth_tokens (
                    channel TEXT PRIMARY KEY,
                    encrypted_payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS marketplace_notification_queue (
                    event_id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    application_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    processed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_marketplace_notification_pending
                    ON marketplace_notification_queue(channel, status, created_at);
                """
            )

    def create_state(self, ttl_seconds: int = 600) -> str:
        state = secrets.token_urlsafe(32)
        expires_at = _utcnow() + timedelta(seconds=max(60, min(ttl_seconds, 1800)))
        if self.database_url:
            with self._connect_postgres() as connection:
                connection.execute(
                    "INSERT INTO marketplace_oauth_states(state_hash,channel,expires_at) VALUES (%s,%s,%s)",
                    (_state_hash(state), CHANNEL, expires_at),
                )
        else:
            with sqlite3.connect(self.sqlite_path) as connection:
                connection.execute(
                    "INSERT INTO marketplace_oauth_states(state_hash,channel,expires_at) VALUES (?,?,?)",
                    (_state_hash(state), CHANNEL, _iso(expires_at)),
                )
        return state

    def consume_state(self, state: str) -> None:
        if not state or len(state) > 256:
            raise OAuthStateError("INVALID_OAUTH_STATE")
        digest = _state_hash(state)
        now = _utcnow()
        if self.database_url:
            with self._connect_postgres() as connection:
                cursor = connection.execute(
                    """UPDATE marketplace_oauth_states SET consumed_at=%s
                    WHERE state_hash=%s AND channel=%s AND consumed_at IS NULL
                      AND expires_at>%s RETURNING state_hash""",
                    (now, digest, CHANNEL, now),
                )
                if cursor.fetchone() is None:
                    raise OAuthStateError("INVALID_OAUTH_STATE")
            return
        with sqlite3.connect(self.sqlite_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT expires_at,consumed_at FROM marketplace_oauth_states WHERE state_hash=? AND channel=?",
                (digest, CHANNEL),
            ).fetchone()
            if row is None or row[1] is not None or _parse_datetime(row[0]) <= now:
                raise OAuthStateError("INVALID_OAUTH_STATE")
            connection.execute(
                "UPDATE marketplace_oauth_states SET consumed_at=? WHERE state_hash=?",
                (_iso(now), digest),
            )

    def save_tokens(self, token_response: dict[str, Any]) -> StoredTokens:
        access_token = str(token_response.get("access_token") or "").strip()
        if not access_token:
            raise TokenStorageError("ACCESS_TOKEN_MISSING")
        previous = self.load_tokens(required=False)
        refresh_token = str(token_response.get("refresh_token") or "").strip() or (
            previous.refresh_token if previous else None
        )
        expires_in = token_response.get("expires_in")
        expires_at = (
            _utcnow() + timedelta(seconds=max(0, int(expires_in)))
            if expires_in is not None
            else None
        )
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": _iso(expires_at) if expires_at else None,
            "user_id": str(token_response.get("user_id") or "").strip() or None,
            "scope": str(token_response.get("scope") or "").strip() or None,
        }
        encrypted = self._cipher.encrypt(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        now = _utcnow()
        if self.database_url:
            with self._connect_postgres() as connection:
                connection.execute(
                    """INSERT INTO marketplace_oauth_tokens(channel,encrypted_payload,updated_at)
                    VALUES (%s,%s,%s) ON CONFLICT(channel) DO UPDATE SET
                    encrypted_payload=EXCLUDED.encrypted_payload,updated_at=EXCLUDED.updated_at""",
                    (CHANNEL, encrypted, now),
                )
        else:
            with sqlite3.connect(self.sqlite_path) as connection:
                connection.execute(
                    """INSERT INTO marketplace_oauth_tokens(channel,encrypted_payload,updated_at)
                    VALUES (?,?,?) ON CONFLICT(channel) DO UPDATE SET
                    encrypted_payload=excluded.encrypted_payload,updated_at=excluded.updated_at""",
                    (CHANNEL, encrypted, _iso(now)),
                )
        return StoredTokens(
            access_token,
            refresh_token,
            expires_at,
            payload["user_id"],
            payload["scope"],
        )

    def load_tokens(self, *, required: bool = True) -> StoredTokens | None:
        if self.database_url:
            with self._connect_postgres() as connection:
                row = connection.execute(
                    "SELECT encrypted_payload FROM marketplace_oauth_tokens WHERE channel=%s",
                    (CHANNEL,),
                ).fetchone()
        else:
            with sqlite3.connect(self.sqlite_path) as connection:
                row = connection.execute(
                    "SELECT encrypted_payload FROM marketplace_oauth_tokens WHERE channel=?",
                    (CHANNEL,),
                ).fetchone()
        if row is None:
            if required:
                raise TokenStorageError("OAUTH_TOKENS_MISSING")
            return None
        try:
            data = json.loads(self._cipher.decrypt(row[0].encode("ascii")))
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise TokenStorageError("OAUTH_TOKENS_UNREADABLE") from exc
        return StoredTokens(
            data["access_token"],
            data.get("refresh_token"),
            _parse_datetime(data.get("expires_at")),
            data.get("user_id"),
            data.get("scope"),
        )

    def has_tokens(self) -> bool:
        return self.load_tokens(required=False) is not None

    def enqueue_notification(self, payload: dict[str, Any]) -> tuple[str, bool]:
        event_id = _event_id(payload)
        record = (
            event_id,
            CHANNEL,
            str(payload["topic"]),
            str(payload["resource"]),
            str(payload["application_id"]),
            str(payload["user_id"]),
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
            "PENDING_SYNC_MONITOR",
            _utcnow(),
        )
        if self.database_url:
            with self._connect_postgres() as connection:
                cursor = connection.execute(
                    """INSERT INTO marketplace_notification_queue(
                    event_id,channel,topic,resource,application_id,user_id,payload_json,status,created_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(event_id) DO NOTHING RETURNING event_id""",
                    record,
                )
                inserted = cursor.fetchone() is not None
        else:
            local_record = (*record[:-1], _iso(record[-1]))
            with sqlite3.connect(self.sqlite_path) as connection:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO marketplace_notification_queue(
                    event_id,channel,topic,resource,application_id,user_id,payload_json,status,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    local_record,
                )
                inserted = cursor.rowcount == 1
        return event_id, inserted

    def notification_count(self) -> int:
        query = "SELECT COUNT(*) FROM marketplace_notification_queue WHERE channel="
        if self.database_url:
            with self._connect_postgres() as connection:
                return int(connection.execute(query + "%s", (CHANNEL,)).fetchone()[0])
        with sqlite3.connect(self.sqlite_path) as connection:
            return int(connection.execute(query + "?", (CHANNEL,)).fetchone()[0])


def validate_notification_payload(
    payload: Any,
    *,
    expected_application_id: str,
    expected_user_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("INVALID_NOTIFICATION_PAYLOAD")
    required = ("resource", "user_id", "topic", "application_id")
    if any(key not in payload for key in required):
        raise ValueError("INVALID_NOTIFICATION_PAYLOAD")
    resource = payload.get("resource")
    topic = payload.get("topic")
    if (
        not isinstance(resource, str)
        or not resource.startswith("/")
        or len(resource) > 500
        or not isinstance(topic, str)
        or not topic
        or len(topic) > 100
    ):
        raise ValueError("INVALID_NOTIFICATION_PAYLOAD")
    application_id = str(payload.get("application_id"))
    user_id = str(payload.get("user_id"))
    if not expected_application_id or application_id != str(expected_application_id):
        raise ValueError("NOTIFICATION_APPLICATION_MISMATCH")
    if expected_user_id and user_id != str(expected_user_id):
        raise ValueError("NOTIFICATION_ACCOUNT_MISMATCH")
    attempts = payload.get("attempts")
    if attempts is not None and (not isinstance(attempts, int) or attempts < 0):
        raise ValueError("INVALID_NOTIFICATION_PAYLOAD")
    clean = {
        key: payload[key]
        for key in (
            "_id",
            "id",
            "resource",
            "user_id",
            "topic",
            "application_id",
            "attempts",
            "sent",
            "received",
            "actions",
        )
        if key in payload
    }
    if "actions" in clean and not (
        isinstance(clean["actions"], list)
        and all(isinstance(item, str) for item in clean["actions"])
    ):
        raise ValueError("INVALID_NOTIFICATION_PAYLOAD")
    return clean


def persisted_credentials_available() -> bool:
    try:
        return MercadoLivreOAuthStore.from_env().has_tokens()
    except (OAuthConfigurationError, TokenStorageError, OSError):
        return False
