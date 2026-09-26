"""WhatsApp Cloud API: receber, preparar rascunho e enviar só texto aprovado.

Fluxo: webhook assinado → conversa pseudonimizada (HMAC do número) → Sales Agent
(rascunho, revisor e gate) → decisão humana → envio. Sem
``WHATSAPP_SEND_ENABLED=true``, credenciais e janela de atendimento aberta,
nada sai. O número do cliente fica cifrado (Fernet) e nunca aparece em log,
listagem ou evidência.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests
from cryptography.fernet import Fernet

from .meta_webhooks import env_flag, graph_api_version, normalize_text
from .sqlstore import SqlStore

MAX_TEXT_LENGTH = 4096
SERVICE_WINDOW = timedelta(hours=24)
REFERRAL_FIELDS = ("source_url", "source_type", "source_id", "headline", "ctwa_clid")
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content")
STATES = ("BOT", "HUMAN", "CLOSED")

# The landing pre-fills: "Referência da visita: VT-XXXX | produto=<id>" and
# "Origem da visita: utm_source=... | utm_medium=...".
_VISIT_REF = re.compile(r"\bVT-[A-Z0-9]{1,13}\b")
_PRODUCT = re.compile(r"\bproduto=([a-z0-9-]{2,60})\b")
_ORIGIN = re.compile(r"Origem da visita:\s*([^\n]+)")
_HANDOFF_PHRASES = (
    "atendente",
    "humano",
    "humana",
    "pessoa real",
    "falar com alguem",
    "falar com uma pessoa",
)


class WhatsAppConfigurationError(RuntimeError):
    """A required secret is missing or invalid; the webhook answers 503."""


class WhatsAppApiError(RuntimeError):
    def __init__(self, code: str, http_status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class InboundMessage:
    message_id: str
    wa_id: str
    sent_at: datetime
    kind: str
    text: str | None
    referral: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StatusUpdate:
    message_id: str
    status: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _from_epoch(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return _now()


def parse_notification(
    payload: dict[str, Any],
) -> tuple[list[InboundMessage], list[StatusUpdate]]:
    """Extract messages and delivery statuses; anything unknown is ignored."""
    messages: list[InboundMessage] = []
    statuses: list[StatusUpdate] = []
    if payload.get("object") != "whatsapp_business_account":
        return messages, statuses
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            if (change or {}).get("field") != "messages":
                continue
            value = change.get("value") or {}
            for item in value.get("messages") or []:
                message_id = str(item.get("id") or "")
                wa_id = str(item.get("from") or "")
                if not message_id or not wa_id:
                    continue
                kind = str(item.get("type") or "unknown")
                text = (item.get("text") or {}).get("body") if kind == "text" else None
                referral = {
                    key: str(val)[:500]
                    for key, val in (item.get("referral") or {}).items()
                    if key in REFERRAL_FIELDS and val
                }
                messages.append(
                    InboundMessage(
                        message_id=message_id[:200],
                        wa_id=wa_id[:32],
                        sent_at=_from_epoch(item.get("timestamp")),
                        kind=kind[:40],
                        text=str(text)[:MAX_TEXT_LENGTH] if text else None,
                        referral=referral,
                    )
                )
            for item in value.get("statuses") or []:
                if item.get("id") and item.get("status"):
                    statuses.append(
                        StatusUpdate(str(item["id"])[:200], str(item["status"])[:40])
                    )
    return messages, statuses


def extract_attribution(text: str) -> dict[str, Any]:
    """Read the visit reference, product and UTMs the landing put in the message."""
    visit = _VISIT_REF.search(text)
    product = _PRODUCT.search(text)
    utm: dict[str, str] = {}
    origin = _ORIGIN.search(text)
    if origin:
        for part in origin.group(1).split("|"):
            key, _, value = part.strip().partition("=")
            if key in UTM_KEYS and value.strip():
                utm[key] = value.strip()[:80]
    return {
        "visit_ref": visit.group(0) if visit else None,
        "product_id": product.group(1) if product else None,
        "utm": utm,
    }


def wants_human(text: str) -> bool:
    normalized = normalize_text(text)
    return any(phrase in normalized for phrase in _HANDOFF_PHRASES)


def send_block_reasons(
    conversation: dict[str, Any],
    text: str | None,
    *,
    now: datetime,
    enabled: bool,
    credentials_present: bool,
) -> list[str]:
    reasons = []
    if not enabled:
        reasons.append("WHATSAPP_SEND_DISABLED")
    if not credentials_present:
        reasons.append("WHATSAPP_CREDENTIALS_MISSING")
    if conversation["state"] == "CLOSED":
        reasons.append("CONVERSATION_CLOSED")
    last_inbound = conversation.get("last_inbound_at")
    if not last_inbound or now - datetime.fromisoformat(last_inbound) > SERVICE_WINDOW:
        # Outside the 24 h window only approved templates may be sent.
        reasons.append("CUSTOMER_SERVICE_WINDOW_CLOSED")
    if not text or not text.strip() or len(text) > MAX_TEXT_LENGTH:
        reasons.append("INVALID_TEXT")
    return reasons


class WhatsAppCloudClient:
    """Minimal sender for ``POST /{phone_number_id}/messages``."""

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        *,
        session: requests.Session | None = None,
        timeout: int = 20,
    ) -> None:
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.session = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> WhatsAppCloudClient | None:
        token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
        phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        return cls(token, phone_number_id) if token and phone_number_id else None

    def send_text(self, to: str, body: str) -> str:
        response = self.session.post(
            f"https://graph.facebook.com/{graph_api_version()}/"
            f"{self.phone_number_id}/messages",
            headers={"Authorization": f"Bearer {self.access_token}"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": body},
            },
            timeout=self.timeout,
        )
        if not response.ok:
            # Only Meta's numeric error code travels; never the body or token.
            try:
                code = str(response.json().get("error", {}).get("code", "unknown"))
            except ValueError:
                code = "unknown"
            raise WhatsAppApiError(f"META_ERROR_{code}", response.status_code)
        try:
            return str(response.json()["messages"][0]["id"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise WhatsAppApiError("UNEXPECTED_RESPONSE", response.status_code) from exc


SCHEMA = """
CREATE TABLE IF NOT EXISTS wa_conversations (
    conversation_id TEXT PRIMARY KEY,
    wa_id_encrypted TEXT NOT NULL,
    wa_id_last4 TEXT NOT NULL,
    state TEXT NOT NULL,
    visit_ref TEXT,
    product_id TEXT,
    utm_json TEXT NOT NULL,
    ctwa_clid TEXT,
    referral_source_url TEXT,
    last_inbound_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wa_messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES wa_conversations(conversation_id),
    direction TEXT NOT NULL,
    kind TEXT NOT NULL,
    body TEXT,
    sent_at TEXT NOT NULL,
    delivery_status TEXT,
    draft_status TEXT,
    run_id TEXT,
    send_key TEXT UNIQUE,
    actor TEXT
);
CREATE INDEX IF NOT EXISTS idx_wa_messages_conversation
    ON wa_messages(conversation_id, sent_at);
CREATE TABLE IF NOT EXISTS wa_audit (
    audit_id TEXT PRIMARY KEY,
    conversation_id TEXT,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    at TEXT NOT NULL
);
"""

DraftRunner = Callable[..., dict[str, Any]]
RunLookup = Callable[[str], dict[str, Any] | None]


class WhatsAppChannel(SqlStore):
    SCHEMA = SCHEMA

    def __init__(
        self,
        sqlite_path: str | Path,
        database_url: str | None = None,
        *,
        salt: str,
        encryption_key: str,
    ) -> None:
        if not salt:
            raise WhatsAppConfigurationError("VERATUS_SESSION_SALT")
        try:
            self._cipher = Fernet(encryption_key.encode("ascii"))
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            raise WhatsAppConfigurationError("VERATUS_TOKEN_ENCRYPTION_KEY") from exc
        self._salt = salt.encode("utf-8")
        super().__init__(sqlite_path, database_url)

    def conversation_id(self, wa_id: str) -> str:
        digest = hmac.new(self._salt, wa_id.encode("utf-8"), hashlib.sha256)
        return "wa_" + digest.hexdigest()[:32]

    # -- inbound -----------------------------------------------------------------

    def receive(self, payload: dict[str, Any]) -> dict[str, int]:
        """Store new messages and statuses. Meta retries, so replays are no-ops."""
        inbound, statuses = parse_notification(payload)
        counts = {"messages": 0, "duplicates": 0, "statuses": 0, "handoffs": 0}
        now = _now().isoformat()
        with self._connect() as (db, mark):
            for message in inbound:
                conversation_id = self.conversation_id(message.wa_id)
                db.execute(
                    self._sql(
                        "INSERT INTO wa_conversations (conversation_id, "
                        "wa_id_encrypted, wa_id_last4, state, utm_json, created_at, "
                        "updated_at) VALUES (?, ?, ?, 'BOT', '{}', ?, ?) "
                        "ON CONFLICT (conversation_id) DO NOTHING",
                        mark,
                    ),
                    [
                        conversation_id,
                        self._cipher.encrypt(message.wa_id.encode()).decode("ascii"),
                        message.wa_id[-4:],
                        now,
                        now,
                    ],
                )
                conversation = self._conversation_row(db, mark, conversation_id)
                handoff = bool(message.text and wants_human(message.text))
                state = conversation["state"]
                if handoff and state == "BOT":
                    state = "HUMAN"
                draft_status = (
                    "NOT_TEXT"
                    if not message.text
                    else "SKIPPED_HUMAN"
                    if state != "BOT"
                    else "NEW"
                )
                inserted = db.execute(
                    self._sql(
                        "INSERT INTO wa_messages (message_id, conversation_id, "
                        "direction, kind, body, sent_at, draft_status) "
                        "VALUES (?, ?, 'IN', ?, ?, ?, ?) "
                        "ON CONFLICT (message_id) DO NOTHING",
                        mark,
                    ),
                    [
                        message.message_id,
                        conversation_id,
                        message.kind,
                        message.text,
                        message.sent_at.isoformat(),
                        draft_status,
                    ],
                ).rowcount
                if not inserted:
                    counts["duplicates"] += 1
                    continue
                counts["messages"] += 1
                self._apply_inbound(db, mark, conversation, message, state, now)
                if state != conversation["state"]:
                    counts["handoffs"] += 1
                    self._audit(
                        db,
                        mark,
                        conversation_id,
                        "HANDOFF_REQUESTED",
                        "customer",
                        {"message_id": message.message_id},
                    )
            for status in statuses:
                counts["statuses"] += db.execute(
                    self._sql(
                        "UPDATE wa_messages SET delivery_status = ? "
                        "WHERE message_id = ? AND direction = 'OUT'",
                        mark,
                    ),
                    [status.status, status.message_id],
                ).rowcount
        return counts

    def _apply_inbound(
        self,
        db: Any,
        mark: str,
        conversation: dict[str, Any],
        message: InboundMessage,
        state: str,
        now: str,
    ) -> None:
        # First touch wins for attribution; later messages only fill gaps.
        attribution = extract_attribution(message.text or "")
        utm = json.loads(conversation["utm_json"] or "{}")
        for key, value in attribution["utm"].items():
            utm.setdefault(key, value)
        last_inbound = conversation["last_inbound_at"]
        sent_at = message.sent_at.isoformat()
        db.execute(
            self._sql(
                "UPDATE wa_conversations SET state = ?, visit_ref = ?, "
                "product_id = ?, utm_json = ?, ctwa_clid = ?, "
                "referral_source_url = ?, last_inbound_at = ?, updated_at = ? "
                "WHERE conversation_id = ?",
                mark,
            ),
            [
                state,
                conversation["visit_ref"] or attribution["visit_ref"],
                conversation["product_id"] or attribution["product_id"],
                json.dumps(utm, sort_keys=True),
                conversation["ctwa_clid"] or message.referral.get("ctwa_clid"),
                conversation["referral_source_url"]
                or message.referral.get("source_url"),
                max(last_inbound, sent_at) if last_inbound else sent_at,
                now,
                conversation["conversation_id"],
            ],
        )

    # -- drafting ----------------------------------------------------------------

    def draft_pending(self, runner: DraftRunner, *, limit: int = 20) -> dict[str, Any]:
        """Ask the Sales Agent for a draft of each new message in BOT conversations.

        The runner is idempotent per (conversation, event_id), so a retry or two
        processes drafting at once reuse the same run.
        """
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql(
                    "SELECT m.message_id, m.conversation_id, m.body, c.product_id "
                    "FROM wa_messages m JOIN wa_conversations c "
                    "ON c.conversation_id = m.conversation_id "
                    "WHERE m.direction = 'IN' AND m.draft_status = 'NEW' "
                    "AND c.state = 'BOT' ORDER BY m.sent_at LIMIT ?",
                    mark,
                ),
                [max(1, min(limit, 100))],
            ).fetchall()
        drafted, failed = [], []
        for row in rows:
            try:
                result = runner(
                    customer_ref=row["conversation_id"],
                    message=row["body"],
                    source="whatsapp",
                    product_hint=row["product_id"],
                    event_id=row["message_id"],
                )
                run_id, status = str(result["run_id"]), "DRAFTED"
            except Exception as exc:  # noqa: BLE001 - one failure must not stop the batch
                run_id, status = None, "FAILED"
                failed.append(
                    {"message_id": row["message_id"], "error": type(exc).__name__}
                )
            with self._connect() as (db, mark):
                db.execute(
                    self._sql(
                        "UPDATE wa_messages SET draft_status = ?, run_id = ? "
                        "WHERE message_id = ? AND draft_status = 'NEW'",
                        mark,
                    ),
                    [status, run_id, row["message_id"]],
                )
            if run_id:
                drafted.append({"message_id": row["message_id"], "run_id": run_id})
        return {"drafted": drafted, "failed": failed}

    # -- outbound ----------------------------------------------------------------

    def send(
        self,
        conversation_id: str,
        *,
        actor: str,
        run_lookup: RunLookup,
        client: WhatsAppCloudClient | None,
        enabled: bool,
        run_id: str | None = None,
        text: str | None = None,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Send a human-approved Sales Agent reply or a text written by a human."""
        if bool(run_id) == bool(text):
            raise ValueError("send_requires_run_id_or_text")
        if text and not idempotency_key:
            raise ValueError("idempotency_key_required")
        now = now or _now()
        reasons: list[str] = []
        with self._connect() as (db, mark):
            conversation = self._conversation_row(db, mark, conversation_id)
            if conversation is None:
                raise KeyError(conversation_id)
            if run_id:
                send_key = f"run:{run_id}"
                linked = db.execute(
                    self._sql(
                        "SELECT message_id FROM wa_messages WHERE run_id = ? "
                        "AND conversation_id = ? AND direction = 'IN'",
                        mark,
                    ),
                    [run_id, conversation_id],
                ).fetchone()
                run = run_lookup(run_id) if linked else None
                body = (run or {}).get("approved_reply")
                if linked is None:
                    reasons.append("RUN_NOT_FROM_THIS_CONVERSATION")
                elif not run or run.get("status") != "approved" or not body:
                    reasons.append("RUN_NOT_APPROVED")
            else:
                send_key, body = f"human:{idempotency_key}", text
            previous = db.execute(
                self._sql(
                    "SELECT message_id FROM wa_messages WHERE send_key = ?", mark
                ),
                [send_key],
            ).fetchone()
        if previous is not None:
            return {"status": "duplicate", "message_id": previous["message_id"]}
        reasons += send_block_reasons(
            conversation,
            body,
            now=now,
            enabled=enabled,
            credentials_present=client is not None,
        )
        detail = {"send_key": send_key, "reasons": reasons}
        if reasons:
            self._record_audit(conversation_id, "SEND_BLOCKED", actor, detail)
            return {
                "status": "BLOCKED",
                "reasons": reasons,
                "preview": {"to": f"***{conversation['wa_id_last4']}", "text": body},
                "external_message_sent": False,
            }
        wa_id = self._cipher.decrypt(conversation["wa_id_encrypted"].encode()).decode()
        try:
            message_id = client.send_text(wa_id, body)
        except WhatsAppApiError as exc:
            detail.update(error=exc.code, http_status=exc.http_status)
            self._record_audit(conversation_id, "SEND_FAILED", actor, detail)
            return {
                "status": "FAILED",
                "error": exc.code,
                "external_message_sent": False,
            }
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "INSERT INTO wa_messages (message_id, conversation_id, direction, "
                    "kind, body, sent_at, delivery_status, run_id, send_key, actor) "
                    "VALUES (?, ?, 'OUT', 'text', ?, ?, 'accepted', ?, ?, ?)",
                    mark,
                ),
                [
                    message_id,
                    conversation_id,
                    body,
                    now.isoformat(),
                    run_id,
                    send_key,
                    actor,
                ],
            )
            self._audit(db, mark, conversation_id, "SENT", actor, detail)
        return {
            "status": "SENT",
            "message_id": message_id,
            "external_message_sent": True,
        }

    # -- operator views ------------------------------------------------------------

    def set_state(
        self, conversation_id: str, state: str, *, actor: str
    ) -> dict[str, Any]:
        if state not in STATES:
            raise ValueError("invalid_state")
        with self._connect() as (db, mark):
            conversation = self._conversation_row(db, mark, conversation_id)
            if conversation is None:
                raise KeyError(conversation_id)
            db.execute(
                self._sql(
                    "UPDATE wa_conversations SET state = ?, updated_at = ? "
                    "WHERE conversation_id = ?",
                    mark,
                ),
                [state, _now().isoformat(), conversation_id],
            )
            self._audit(
                db,
                mark,
                conversation_id,
                "STATE_CHANGED",
                actor,
                {"from": conversation["state"], "to": state},
            )
            row = self._conversation_row(db, mark, conversation_id)
        return self._public(row)

    def list_conversations(
        self, *, state: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        where, params = ("WHERE state = ? ", [state]) if state else ("", [])
        params.append(max(1, min(limit, 500)))
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql(
                    f"SELECT * FROM wa_conversations {where}"
                    "ORDER BY last_inbound_at DESC LIMIT ?",
                    mark,
                ),
                params,
            ).fetchall()
        return [self._public(dict(row)) for row in rows]

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as (db, mark):
            row = self._conversation_row(db, mark, conversation_id)
            if row is None:
                return None
            messages = db.execute(
                self._sql(
                    "SELECT message_id, direction, kind, body, sent_at, "
                    "delivery_status, draft_status, run_id, actor FROM wa_messages "
                    "WHERE conversation_id = ? ORDER BY sent_at",
                    mark,
                ),
                [conversation_id],
            ).fetchall()
        return {**self._public(row), "messages": [dict(item) for item in messages]}

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        last_inbound = row["last_inbound_at"]
        return {
            "conversation_id": row["conversation_id"],
            "contact": f"***{row['wa_id_last4']}",
            "state": row["state"],
            "visit_ref": row["visit_ref"],
            "product_id": row["product_id"],
            "utm": json.loads(row["utm_json"] or "{}"),
            "click_to_whatsapp_ad": bool(row["ctwa_clid"]),
            "referral_source_url": row["referral_source_url"],
            "last_inbound_at": last_inbound,
            "service_window_open": bool(
                last_inbound
                and _now() - datetime.fromisoformat(last_inbound) <= SERVICE_WINDOW
            ),
        }

    def _conversation_row(
        self, db: Any, mark: str, conversation_id: str
    ) -> dict[str, Any] | None:
        row = db.execute(
            self._sql("SELECT * FROM wa_conversations WHERE conversation_id = ?", mark),
            [conversation_id],
        ).fetchone()
        return dict(row) if row is not None else None

    def _record_audit(
        self, conversation_id: str, action: str, actor: str, detail: dict[str, Any]
    ) -> None:
        with self._connect() as (db, mark):
            self._audit(db, mark, conversation_id, action, actor, detail)

    def _audit(
        self,
        db: Any,
        mark: str,
        conversation_id: str,
        action: str,
        actor: str,
        detail: dict[str, Any],
    ) -> None:
        db.execute(
            self._sql(
                "INSERT INTO wa_audit (audit_id, conversation_id, action, actor, "
                "detail_json, at) VALUES (?, ?, ?, ?, ?, ?)",
                mark,
            ),
            [
                f"wa_audit_{uuid4().hex}",
                conversation_id,
                action,
                actor[:100],
                json.dumps(detail, sort_keys=True, default=str),
                _now().isoformat(),
            ],
        )


def send_enabled() -> bool:
    return env_flag("WHATSAPP_SEND_ENABLED")
