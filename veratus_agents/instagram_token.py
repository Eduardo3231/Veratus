"""Renovação automática do token longo do Instagram, com alerta se falhar.

Na API do Instagram com login do Instagram, o token longo vale 60 dias e pode
ser renovado depois de 24 h de vida:
``GET graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token``.
O ``INSTAGRAM_ACCESS_TOKEN`` do ambiente só semeia o primeiro valor; cada token
renovado fica cifrado no banco (Fernet, ``VERATUS_TOKEN_ENCRYPTION_KEY``). Uma
reserva no banco garante que só um worker renova por vez. O token nunca vai
para log, resposta HTTP ou status.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from cryptography.fernet import Fernet, InvalidToken

from .sqlstore import SqlStore

LOGGER = logging.getLogger("veratus.instagram_token")
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
MIN_AGE = timedelta(hours=24)  # Meta refuses to refresh a younger token
RENEW_BEFORE = timedelta(days=10)
ALERT_BEFORE = timedelta(days=7)
ATTEMPT_GAP = timedelta(hours=1)
CHECK_EVERY = timedelta(hours=6)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ig_access_token (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token_encrypted TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT,
    refreshed_at TEXT,
    last_attempt_at TEXT,
    last_error TEXT,
    failures INTEGER NOT NULL DEFAULT 0
);
"""


class InstagramTokenStore(SqlStore):
    SCHEMA = SCHEMA

    def __init__(
        self,
        sqlite_path: str | Path,
        database_url: str | None = None,
        *,
        encryption_key: str,
        seed_token: str = "",
    ) -> None:
        super().__init__(sqlite_path, database_url)
        self._cipher = (
            Fernet(encryption_key.encode("ascii")) if encryption_key else None
        )
        self.seed_token = seed_token.strip()

    @classmethod
    def from_settings(
        cls, sqlite_path: str | Path, database_url: str | None
    ) -> InstagramTokenStore:
        return cls(
            sqlite_path,
            database_url,
            encryption_key=os.getenv("VERATUS_TOKEN_ENCRYPTION_KEY", "").strip(),
            seed_token=os.getenv("INSTAGRAM_ACCESS_TOKEN", ""),
        )

    def _row(self) -> dict[str, Any] | None:
        with self._connect() as (db, _):
            row = db.execute("SELECT * FROM ig_access_token WHERE id = 1").fetchone()
        return dict(row) if row else None

    def _seed(self, now: datetime) -> dict[str, Any] | None:
        """Store the env token once; its age is unknown, so count from now."""
        if not self.seed_token or self._cipher is None:
            return None
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "INSERT INTO ig_access_token (id, token_encrypted, issued_at) "
                    "VALUES (1, ?, ?) ON CONFLICT (id) DO NOTHING",
                    mark,
                ),
                [self._encrypt(self.seed_token), now.isoformat()],
            )
        return self._row()

    def _encrypt(self, token: str) -> str:
        return self._cipher.encrypt(token.encode("utf-8")).decode("ascii")

    def current(self) -> str:
        """Token to use now: the stored one, else the env seed."""
        row = self._row()
        if row and self._cipher is not None:
            try:
                return self._cipher.decrypt(row["token_encrypted"].encode()).decode()
            except InvalidToken:
                LOGGER.error("instagram_token_unreadable reason=encryption_key_changed")
        return self.seed_token

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        """Safe summary for admins and alerts; never includes the token."""
        now = now or datetime.now(timezone.utc)
        row = self._row()
        expires = row and row["expires_at"]
        days_left = (
            (datetime.fromisoformat(expires) - now).total_seconds() / 86400
            if expires
            else None
        )
        failing = bool(row and row["failures"])
        expiring = days_left is not None and days_left <= ALERT_BEFORE.days
        return {
            "token_present": bool(row or self.seed_token),
            "stored": row is not None,
            "encryption_key_present": self._cipher is not None,
            "expires_at": expires or None,
            "days_left": round(days_left, 1) if days_left is not None else None,
            "refreshed_at": row["refreshed_at"] if row else None,
            "last_error": row["last_error"] if row else None,
            "failures": row["failures"] if row else 0,
            "alert": failing
            or expiring
            or bool(self.seed_token and self._cipher is None),
        }

    def _claim(self, now: datetime) -> bool:
        """Only one worker renews: reserve the attempt with a conditional update."""
        with self._connect() as (db, mark):
            return bool(
                db.execute(
                    self._sql(
                        "UPDATE ig_access_token SET last_attempt_at = ? WHERE id = 1 "
                        "AND (last_attempt_at IS NULL OR last_attempt_at < ?)",
                        mark,
                    ),
                    [now.isoformat(), (now - ATTEMPT_GAP).isoformat()],
                ).rowcount
            )

    def refresh_if_due(
        self, *, now: datetime | None = None, session: Any = requests
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        if self._cipher is None:
            if self.seed_token:
                LOGGER.error(
                    "instagram_token_refresh_blocked reason=encryption_key_missing"
                )
            return {"status": "BLOCKED", "reason": "ENCRYPTION_KEY_MISSING"}
        row = self._row() or self._seed(now)
        if row is None:
            return {"status": "NO_TOKEN"}
        issued = datetime.fromisoformat(row["issued_at"])
        expires = row["expires_at"] and datetime.fromisoformat(row["expires_at"])
        if now - issued < MIN_AGE or (expires and expires - now > RENEW_BEFORE):
            return {"status": "NOT_DUE", "expires_at": row["expires_at"]}
        if not self._claim(now):
            return {"status": "IN_PROGRESS_ELSEWHERE"}
        try:
            response = session.get(
                REFRESH_URL,
                params={
                    "grant_type": "ig_refresh_token",
                    "access_token": self.current(),
                },
                timeout=20,
            )
            body = response.json() if response.ok else {}
            token, lifetime = body.get("access_token"), int(body.get("expires_in", 0))
            if not token or lifetime <= 0:
                raise ValueError(f"HTTP_{response.status_code}")
        except (requests.RequestException, ValueError) as exc:
            code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            return self._fail(code[:80])
        new_expiry = (now + timedelta(seconds=lifetime)).isoformat()
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "UPDATE ig_access_token SET token_encrypted = ?, issued_at = ?, "
                    "expires_at = ?, refreshed_at = ?, last_error = NULL, "
                    "failures = 0 WHERE id = 1",
                    mark,
                ),
                [self._encrypt(token), now.isoformat(), new_expiry, now.isoformat()],
            )
        LOGGER.info("instagram_token_refreshed expires_at=%s", new_expiry)
        return {"status": "REFRESHED", "expires_at": new_expiry}

    def _fail(self, code: str) -> dict[str, Any]:
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "UPDATE ig_access_token SET last_error = ?, "
                    "failures = failures + 1 WHERE id = 1",
                    mark,
                ),
                [code],
            )
        status = self.status()
        # ALERT: Render keeps this line; the admin status endpoint shows it too.
        LOGGER.error(
            "ALERT instagram_token_refresh_failed code=%s failures=%s days_left=%s",
            code,
            status["failures"],
            status["days_left"],
        )
        return {"status": "FAILED", "error": code, "alert": True}


_watchers: set[int] = set()
_watchers_lock = threading.Lock()


def start_refresh_watcher(store: InstagramTokenStore) -> bool:
    """Check now and every 6 h in a daemon thread; once per process."""
    with _watchers_lock:
        if os.getpid() in _watchers:
            return False
        _watchers.add(os.getpid())

    def run() -> None:
        stop = threading.Event()
        while not stop.is_set():
            try:
                store.refresh_if_due()
            except Exception:
                LOGGER.exception("ALERT instagram_token_watcher_error")
            stop.wait(CHECK_EVERY.total_seconds())

    threading.Thread(target=run, name="instagram-token-refresh", daemon=True).start()
    return True
