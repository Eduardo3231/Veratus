"""Comentário com palavra-chave → resposta privada no Instagram.

Regra da Meta: uma única mensagem por comentário, em até 7 dias. A resposta é
endereçada ao ``comment_id``; por isso esta fila não guarda o ID nem o nome de
quem comentou. O link leva UTMs para a landing, que as transforma na
referência de visita enviada ao WhatsApp.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from .meta_webhooks import env_flag, graph_api_version, normalize_text
from .sqlstore import SqlStore

PRIVATE_REPLY_WINDOW = timedelta(days=7)
MAX_ATTEMPTS = 3
RULES_PATH = Path(__file__).with_name("instagram_comment_rules.json")


class InstagramApiError(RuntimeError):
    def __init__(self, code: str, http_status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status

    @property
    def retriable(self) -> bool:
        return (
            self.http_status is None
            or self.http_status == 429
            or (self.http_status >= 500)
        )


@dataclass(frozen=True)
class CommentEvent:
    comment_id: str
    media_id: str
    text: str
    from_id: str


@dataclass(frozen=True)
class ReplyRule:
    rule_id: str
    keywords: frozenset[str]
    message: str
    utm_campaign: str


def parse_comments(payload: dict[str, Any]) -> list[CommentEvent]:
    events: list[CommentEvent] = []
    if payload.get("object") != "instagram":
        return events
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            if (change or {}).get("field") != "comments":
                continue
            value = change.get("value") or {}
            comment_id = str(value.get("id") or "")
            if not comment_id:
                continue
            events.append(
                CommentEvent(
                    comment_id=comment_id[:100],
                    media_id=str((value.get("media") or {}).get("id") or "")[:100],
                    text=str(value.get("text") or "")[:2200],
                    from_id=str((value.get("from") or {}).get("id") or ""),
                )
            )
    return events


def load_rules(path: Path = RULES_PATH) -> tuple[str, list[ReplyRule]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = [
        ReplyRule(
            rule_id=item["id"],
            keywords=frozenset(normalize_text(word) for word in item["keywords"]),
            message=item["message"],
            utm_campaign=item["utm_campaign"],
        )
        for item in data["rules"]
    ]
    return data["landing_url"], rules


def match_rule(text: str, rules: list[ReplyRule]) -> ReplyRule | None:
    tokens = set(re.findall(r"[a-z0-9]+", normalize_text(text)))
    return next((rule for rule in rules if rule.keywords & tokens), None)


def reply_text(rule: ReplyRule, media_id: str, landing_url: str) -> str:
    query = urlencode(
        {
            "utm_source": "instagram",
            "utm_medium": "comment_dm",
            "utm_campaign": rule.utm_campaign,
            "utm_content": media_id or "sem_midia",
        }
    )
    return rule.message.format(link=f"{landing_url}?{query}")


class InstagramClient:
    """Private reply: ``POST graph.instagram.com/{ig_user_id}/messages``."""

    def __init__(
        self,
        access_token: str,
        ig_user_id: str,
        *,
        session: requests.Session | None = None,
        timeout: int = 20,
    ) -> None:
        self.access_token = access_token
        self.ig_user_id = ig_user_id
        self.session = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> InstagramClient | None:
        token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
        user_id = os.getenv("INSTAGRAM_USER_ID", "").strip()
        return cls(token, user_id) if token and user_id else None

    def send_private_reply(self, comment_id: str, text: str) -> str:
        response = self.session.post(
            f"https://graph.instagram.com/{graph_api_version()}/"
            f"{self.ig_user_id}/messages",
            headers={"Authorization": f"Bearer {self.access_token}"},
            json={"recipient": {"comment_id": comment_id}, "message": {"text": text}},
            timeout=self.timeout,
        )
        if not response.ok:
            try:
                code = str(response.json().get("error", {}).get("code", "unknown"))
            except ValueError:
                code = "unknown"
            raise InstagramApiError(f"META_ERROR_{code}", response.status_code)
        try:
            return str(response.json().get("message_id") or "")
        except ValueError as exc:
            raise InstagramApiError(
                "UNEXPECTED_RESPONSE", response.status_code
            ) from exc


SCHEMA = """
CREATE TABLE IF NOT EXISTS ig_comment_replies (
    comment_id TEXT PRIMARY KEY,
    media_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    received_at TEXT NOT NULL,
    processed_at TEXT,
    error_code TEXT,
    reply_message_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_ig_comment_replies_status
    ON ig_comment_replies(status, received_at);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CommentReplyQueue(SqlStore):
    SCHEMA = SCHEMA

    def enqueue(
        self,
        payload: dict[str, Any],
        rules: list[ReplyRule],
        *,
        own_account_id: str = "",
    ) -> dict[str, int]:
        """Queue matching comments once; other comments are not stored at all."""
        counts = {"queued": 0, "ignored": 0, "duplicates": 0}
        now = _now().isoformat()
        with self._connect() as (db, mark):
            for event in parse_comments(payload):
                rule = match_rule(event.text, rules)
                if rule is None or (own_account_id and event.from_id == own_account_id):
                    counts["ignored"] += 1
                    continue
                inserted = db.execute(
                    self._sql(
                        "INSERT INTO ig_comment_replies (comment_id, media_id, "
                        "rule_id, status, attempts, received_at) "
                        "VALUES (?, ?, ?, 'PENDING', 0, ?) "
                        "ON CONFLICT (comment_id) DO NOTHING",
                        mark,
                    ),
                    [event.comment_id, event.media_id, rule.rule_id, now],
                ).rowcount
                counts["queued" if inserted else "duplicates"] += 1
        return counts

    def process(
        self,
        *,
        client: InstagramClient | None,
        enabled: bool,
        landing_url: str,
        rules: list[ReplyRule],
        now: datetime | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Send pending replies, or preview them when sending is switched off."""
        now = now or _now()
        by_id = {rule.rule_id: rule for rule in rules}
        blocked = []
        if not enabled:
            blocked.append("INSTAGRAM_DM_DISABLED")
        if client is None:
            blocked.append("INSTAGRAM_CREDENTIALS_MISSING")
        with self._connect() as (db, mark):
            rows = [
                dict(row)
                for row in db.execute(
                    self._sql(
                        "SELECT * FROM ig_comment_replies WHERE status = 'PENDING' "
                        "ORDER BY received_at LIMIT ?",
                        mark,
                    ),
                    [max(1, min(limit, 200))],
                ).fetchall()
            ]
        result: dict[str, Any] = {
            "sent": [],
            "expired": [],
            "failed": [],
            "preview": [],
            "blocked_reasons": blocked,
        }
        for row in rows:
            rule = by_id.get(row["rule_id"])
            if now - datetime.fromisoformat(row["received_at"]) > PRIVATE_REPLY_WINDOW:
                self._finish(row["comment_id"], "EXPIRED", now)
                result["expired"].append(row["comment_id"])
                continue
            if rule is None:
                self._finish(row["comment_id"], "FAILED", now, "RULE_REMOVED")
                result["failed"].append(row["comment_id"])
                continue
            text = reply_text(rule, row["media_id"], landing_url)
            if blocked:
                result["preview"].append(
                    {"comment_id": row["comment_id"], "text": text}
                )
                continue
            if not self._claim(row["comment_id"]):
                continue  # another process took it; Meta allows one reply only
            try:
                message_id = client.send_private_reply(row["comment_id"], text)
            except InstagramApiError as exc:
                final = not exc.retriable or row["attempts"] + 1 >= MAX_ATTEMPTS
                self._finish(
                    row["comment_id"],
                    "FAILED" if final else "PENDING",
                    now,
                    exc.code,
                    attempted=True,
                )
                result["failed"].append(
                    {"comment_id": row["comment_id"], "error": exc.code}
                )
                continue
            self._finish(
                row["comment_id"], "SENT", now, message_id=message_id, attempted=True
            )
            result["sent"].append(row["comment_id"])
        result["external_messages_sent"] = len(result["sent"])
        return result

    def list_replies(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict]:
        where, params = ("WHERE status = ? ", [status]) if status else ("", [])
        params.append(max(1, min(limit, 500)))
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql(
                    f"SELECT * FROM ig_comment_replies {where}"
                    "ORDER BY received_at DESC LIMIT ?",
                    mark,
                ),
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def _claim(self, comment_id: str) -> bool:
        with self._connect() as (db, mark):
            return bool(
                db.execute(
                    self._sql(
                        "UPDATE ig_comment_replies SET status = 'SENDING' "
                        "WHERE comment_id = ? AND status = 'PENDING'",
                        mark,
                    ),
                    [comment_id],
                ).rowcount
            )

    def _finish(
        self,
        comment_id: str,
        status: str,
        now: datetime,
        error_code: str | None = None,
        *,
        message_id: str | None = None,
        attempted: bool = False,
    ) -> None:
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "UPDATE ig_comment_replies SET status = ?, processed_at = ?, "
                    "error_code = ?, reply_message_id = ?, attempts = attempts + ? "
                    "WHERE comment_id = ?",
                    mark,
                ),
                [
                    status,
                    now.isoformat(),
                    error_code,
                    message_id,
                    int(attempted),
                    comment_id,
                ],
            )


def dm_enabled() -> bool:
    return env_flag("INSTAGRAM_DM_ENABLED")
