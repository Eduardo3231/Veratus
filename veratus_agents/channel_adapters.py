"""Adaptadores de conversa: mesma interface (receber, responder, janela, limites).

- WhatsApp: o canal atual (``whatsapp.py``), com janela de 24 h, templates de
  utilidade (``pedido_confirmado``, ``pedido_enviado``, ``alerta_fundador``) e
  marketing só com opt-in registrado.
- Instagram Direct: campo ``messages`` do webhook já existente, janela de 24 h;
  a tag ``HUMAN_AGENT`` só em mensagem escrita por pessoa.
- Mercado Livre: perguntas pré-venda e mensagens pós-venda.
- Shopee, TikTok Shop e Amazon: ``API_CONTRACT_UNVERIFIED``; o rascunho vai
  para a fila e o fundador cola no painel do canal.

Nenhum envio acontece com as chaves desligadas; o motivo volta como ``BLOCKED``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

import requests
from cryptography.fernet import Fernet

from .meta_webhooks import env_flag, graph_api_version
from .sqlstore import SqlStore

SERVICE_WINDOW = timedelta(hours=24)
HUMAN_AGENT_WINDOW = timedelta(days=7)
UTILITY_TEMPLATES = ("pedido_confirmado", "pedido_enviado", "alerta_fundador")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _api_error_code(response: Any) -> str:
    try:
        return str(response.json().get("error", {}).get("code", "unknown"))
    except (ValueError, AttributeError):
        return "unknown"


# -- WhatsApp ---------------------------------------------------------------------


class WhatsAppAdapter:
    channel = "whatsapp"
    api_status = "VERIFIED_CLOUD_API"
    limits: ClassVar[dict[str, Any]] = {"max_text": 4096, "window_hours": 24}

    def __init__(
        self,
        whatsapp_channel: Any,
        *,
        client: Any,
        enabled: bool | None = None,
    ) -> None:
        self.wa = whatsapp_channel
        self.client = client
        self.enabled = enabled

    def window_open(self, conversation_id: str, now: datetime) -> bool:
        conversation = self.wa.get(conversation_id)
        return bool(conversation and conversation["service_window_open"])

    def send(
        self,
        conversation_id: str,
        text: str,
        *,
        idempotency_key: str,
        human_written: bool = False,
    ) -> dict[str, Any]:
        from .whatsapp import send_enabled

        actor = "founder" if human_written else "sales-agent"
        return self.wa.send(
            conversation_id,
            actor=actor,
            run_lookup=lambda _run_id: None,
            client=self.client,
            enabled=send_enabled() if self.enabled is None else self.enabled,
            text=text,
            idempotency_key=idempotency_key,
        )

    def pending(self, limit: int = 20) -> list[Any]:
        from .conversations import InboundText

        return [
            InboundText(
                channel="whatsapp",
                conversation_id=row["conversation_id"],
                message_id=row["message_id"],
                text=row["body"],
                product_hint=row["product_id"],
                origin={"utm": row.get("utm") or {}},
            )
            for row in self.wa.pending_inbound(limit=limit)
        ]

    def mark(self, message_id: str, status: str) -> None:
        self.wa.mark_inbound(message_id, status)


def send_whatsapp_template(
    client: Any,
    to: str,
    template: str,
    parameters: list[str],
    *,
    language: str = "pt_BR",
) -> str:
    """Template aprovado na Meta (fora da janela de 24 h).

    Só templates de utilidade são aceitos aqui; marketing exige opt-in e passa
    por ``marketing_allowed``.
    """
    if template not in UTILITY_TEMPLATES:
        raise ValueError("template_nao_permitido")
    response = client.session.post(
        f"https://graph.facebook.com/{graph_api_version()}/"
        f"{client.phone_number_id}/messages",
        headers={"Authorization": f"Bearer {client.access_token}"},
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": language},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": value[:1000]}
                            for value in parameters
                        ],
                    }
                ],
            },
        },
        timeout=client.timeout,
    )
    if not response.ok:
        from .whatsapp import WhatsAppApiError

        raise WhatsAppApiError(
            f"META_ERROR_{_api_error_code(response)}", response.status_code
        )
    return str(response.json()["messages"][0]["id"])


def marketing_allowed(opt_in: dict[str, Any] | None) -> bool:
    """Marketing só para quem deu opt-in registrado (com data e origem)."""
    return bool(opt_in and opt_in.get("granted_at") and opt_in.get("source"))


# -- Instagram Direct --------------------------------------------------------------

INBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS dm_conversations (
    conv_key TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    contact_encrypted TEXT NOT NULL,
    contact_last4 TEXT NOT NULL,
    last_inbound_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dm_messages (
    message_id TEXT PRIMARY KEY,
    conv_key TEXT NOT NULL,
    direction TEXT NOT NULL,
    body TEXT,
    sent_at TEXT NOT NULL,
    status TEXT NOT NULL,
    send_key TEXT UNIQUE,
    actor TEXT
);
"""


class DirectInbox(SqlStore):
    """Conversas de Direct: contato cifrado, id pseudonimizado (HMAC)."""

    SCHEMA = INBOX_SCHEMA

    def __init__(
        self,
        sqlite_path: str | Path,
        database_url: str | None = None,
        *,
        salt: str,
        encryption_key: str,
    ) -> None:
        if not salt:
            raise ValueError("VERATUS_SESSION_SALT")
        self._salt = salt.encode("utf-8")
        self._cipher = Fernet(encryption_key.encode("ascii"))
        super().__init__(sqlite_path, database_url)

    def conversation_id(self, channel: str, contact: str) -> str:
        digest = hmac.new(self._salt, f"{channel}:{contact}".encode(), hashlib.sha256)
        return "ig_" + digest.hexdigest()[:32]

    def receive(
        self,
        channel: str,
        contact: str,
        message_id: str,
        text: str | None,
        sent_at: datetime,
    ) -> tuple[str, bool]:
        conversation_id = self.conversation_id(channel, contact)
        key = f"{channel}:{conversation_id}"
        now = _now().isoformat()
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "INSERT INTO dm_conversations (conv_key, channel, contact_encrypted, "
                    "contact_last4, created_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (conv_key) DO NOTHING",
                    mark,
                ),
                [
                    key,
                    channel,
                    self._cipher.encrypt(contact.encode()).decode("ascii"),
                    contact[-4:],
                    now,
                ],
            )
            inserted = db.execute(
                self._sql(
                    "INSERT INTO dm_messages (message_id, conv_key, direction, body, "
                    "sent_at, status) VALUES (?, ?, 'IN', ?, ?, 'NEW') "
                    "ON CONFLICT (message_id) DO NOTHING",
                    mark,
                ),
                [message_id, key, text, sent_at.isoformat()],
            ).rowcount
            if inserted:
                db.execute(
                    self._sql(
                        "UPDATE dm_conversations SET last_inbound_at = ? "
                        "WHERE conv_key = ? AND (last_inbound_at IS NULL "
                        "OR last_inbound_at < ?)",
                        mark,
                    ),
                    [sent_at.isoformat(), key, sent_at.isoformat()],
                )
        return conversation_id, bool(inserted)

    def contact(self, channel: str, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as (db, mark):
            row = db.execute(
                self._sql("SELECT * FROM dm_conversations WHERE conv_key = ?", mark),
                [f"{channel}:{conversation_id}"],
            ).fetchone()
        if row is None:
            return None
        row = dict(row)
        row["contact"] = self._cipher.decrypt(
            row.pop("contact_encrypted").encode()
        ).decode()
        return row

    def pending(self, channel: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql(
                    "SELECT m.message_id, m.conv_key, m.body FROM dm_messages m "
                    "WHERE m.direction = 'IN' AND m.status = 'NEW' "
                    "AND m.conv_key LIKE ? ORDER BY m.sent_at LIMIT ?",
                    mark,
                ),
                [f"{channel}:%", max(1, min(limit, 100))],
            ).fetchall()
        return [dict(row) for row in rows]

    def mark(self, message_id: str, status: str) -> None:
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "UPDATE dm_messages SET status = ? WHERE message_id = ?", mark
                ),
                [status, message_id],
            )

    def sent_before(self, send_key: str) -> str | None:
        with self._connect() as (db, mark):
            row = db.execute(
                self._sql(
                    "SELECT message_id FROM dm_messages WHERE send_key = ?", mark
                ),
                [send_key],
            ).fetchone()
        return row["message_id"] if row else None

    def record_out(
        self, channel: str, conversation_id: str, message_id: str, body: str, **kw: Any
    ) -> None:
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "INSERT INTO dm_messages (message_id, conv_key, direction, body, "
                    "sent_at, status, send_key, actor) VALUES (?, ?, 'OUT', ?, ?, "
                    "'accepted', ?, ?)",
                    mark,
                ),
                [
                    message_id,
                    f"{channel}:{conversation_id}",
                    body,
                    _now().isoformat(),
                    kw.get("send_key"),
                    kw.get("actor"),
                ],
            )


def parse_instagram_messages(
    payload: dict[str, Any], own_account_id: str = ""
) -> list[dict[str, Any]]:
    """Eventos ``messaging`` de texto; ecos da própria conta são ignorados."""
    messages = []
    if payload.get("object") != "instagram":
        return messages
    for entry in payload.get("entry") or []:
        for item in (entry or {}).get("messaging") or []:
            message = (item or {}).get("message") or {}
            sender = str(((item or {}).get("sender") or {}).get("id") or "")
            if (
                not message.get("mid")
                or not sender
                or message.get("is_echo")
                or (own_account_id and sender == own_account_id)
            ):
                continue
            timestamp = item.get("timestamp")
            try:
                sent_at = datetime.fromtimestamp(int(timestamp) / 1000, timezone.utc)
            except (TypeError, ValueError):
                sent_at = _now()
            messages.append(
                {
                    "message_id": str(message["mid"])[:200],
                    "contact": sender[:64],
                    "text": str(message.get("text") or "")[:2000] or None,
                    "sent_at": sent_at,
                }
            )
    return messages


class InstagramDirectAdapter:
    channel = "instagram"
    api_status = "VERIFIED_GRAPH_API"
    limits: ClassVar[dict[str, Any]] = {
        "max_text": 1000,
        "window_hours": 24,
        "human_agent_days": 7,
    }

    def __init__(
        self,
        inbox: DirectInbox,
        *,
        access_token: str | None,
        ig_user_id: str | None,
        session: Any = None,
        enabled: bool | None = None,
        timeout: int = 20,
    ) -> None:
        self.inbox = inbox
        self.access_token = access_token
        self.ig_user_id = ig_user_id
        self.session = session or requests.Session()
        self.enabled = enabled
        self.timeout = timeout

    def receive(self, payload: dict[str, Any], own_account_id: str = "") -> int:
        count = 0
        for item in parse_instagram_messages(payload, own_account_id):
            _, inserted = self.inbox.receive(
                self.channel,
                item["contact"],
                item["message_id"],
                item["text"],
                item["sent_at"],
            )
            count += inserted
        return count

    def _last_inbound(self, conversation_id: str) -> datetime | None:
        row = self.inbox.contact(self.channel, conversation_id)
        if not row or not row["last_inbound_at"]:
            return None
        return datetime.fromisoformat(row["last_inbound_at"])

    def window_open(self, conversation_id: str, now: datetime) -> bool:
        last = self._last_inbound(conversation_id)
        return bool(last and now - last <= SERVICE_WINDOW)

    def send(
        self,
        conversation_id: str,
        text: str,
        *,
        idempotency_key: str,
        human_written: bool = False,
    ) -> dict[str, Any]:
        send_key = f"{self.channel}:{idempotency_key}"
        if previous := self.inbox.sent_before(send_key):
            return {"status": "duplicate", "message_id": previous}
        enabled = (
            env_flag("INSTAGRAM_MESSAGING_ENABLED")
            if self.enabled is None
            else self.enabled
        )
        now = _now()
        last = self._last_inbound(conversation_id)
        reasons = []
        if not enabled:
            reasons.append("INSTAGRAM_MESSAGING_DISABLED")
        if not self.access_token or not self.ig_user_id:
            reasons.append("INSTAGRAM_CREDENTIALS_MISSING")
        in_window = bool(last and now - last <= SERVICE_WINDOW)
        # A tag HUMAN_AGENT estende a janela para 7 dias, só para texto de pessoa.
        human_window = bool(human_written and last and now - last <= HUMAN_AGENT_WINDOW)
        if not in_window and not human_window:
            reasons.append("CUSTOMER_SERVICE_WINDOW_CLOSED")
        if not text.strip() or len(text) > self.limits["max_text"]:
            reasons.append("INVALID_TEXT")
        if reasons:
            return {"status": "BLOCKED", "reasons": reasons}
        contact = self.inbox.contact(self.channel, conversation_id)
        body: dict[str, Any] = {
            "recipient": {"id": contact["contact"]},
            "message": {"text": text},
        }
        if not in_window and human_window:
            body.update(messaging_type="MESSAGE_TAG", tag="HUMAN_AGENT")
        response = self.session.post(
            f"https://graph.instagram.com/{graph_api_version()}/{self.ig_user_id}/messages",
            headers={"Authorization": f"Bearer {self.access_token}"},
            json=body,
            timeout=self.timeout,
        )
        if not response.ok:
            return {
                "status": "FAILED",
                "error": f"META_ERROR_{_api_error_code(response)}",
            }
        message_id = str(response.json().get("message_id") or f"ig-{uuid4().hex}")
        self.inbox.record_out(
            self.channel,
            conversation_id,
            message_id,
            text,
            send_key=send_key,
            actor="founder" if human_written else "sales-agent",
        )
        return {"status": "SENT", "message_id": message_id}

    def pending(self, limit: int = 20) -> list[Any]:
        from .conversations import InboundText

        return [
            InboundText(
                channel=self.channel,
                conversation_id=row["conv_key"].split(":", 1)[1],
                message_id=row["message_id"],
                text=row["body"] or "",
            )
            for row in self.inbox.pending(self.channel, limit)
            if row["body"]
        ]

    def mark(self, message_id: str, status: str) -> None:
        self.inbox.mark(message_id, status)


# -- Mercado Livre -----------------------------------------------------------------

ML_API = "https://api.mercadolibre.com"


class MercadoLivreConversationAdapter:
    """Perguntas pré-venda (listar e responder) e mensagens pós-venda.

    ``conversation_id``: ``q-<question_id>`` para pergunta e
    ``pack-<pack_id>`` para pós-venda. O gate do canal proíbe link, @ e contato.
    """

    channel = "mercado_livre"
    api_status = "VERIFIED_PUBLIC_DOCS"
    limits: ClassVar[dict[str, Any]] = {"max_text": 2000, "post_sale_max_text": 350}

    def __init__(
        self,
        *,
        token_provider: Callable[[], str] | None,
        seller_id: str | None,
        session: Any = None,
        enabled: bool | None = None,
        timeout: int = 20,
    ) -> None:
        self.token_provider = token_provider
        self.seller_id = seller_id
        self.session = session or requests.Session()
        self.enabled = enabled
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token_provider()}"}

    def list_unanswered_questions(self, limit: int = 50) -> list[dict[str, Any]]:
        if self.token_provider is None:
            return []
        response = self.session.get(
            f"{ML_API}/my/received_questions/search",
            headers=self._headers(),
            params={"status": "UNANSWERED", "limit": limit, "api_version": 4},
            timeout=self.timeout,
        )
        if not response.ok:
            return []
        return [
            {
                "question_id": str(item["id"]),
                "item_id": str(item.get("item_id") or ""),
                "text": str(item.get("text") or "")[:2000],
            }
            for item in response.json().get("questions") or []
            if item.get("id")
        ]

    def window_open(self, conversation_id: str, now: datetime) -> bool:
        return True

    def send(
        self,
        conversation_id: str,
        text: str,
        *,
        idempotency_key: str,
        human_written: bool = False,
    ) -> dict[str, Any]:
        enabled = (
            env_flag("MERCADO_LIVRE_ANSWERS_ENABLED")
            if self.enabled is None
            else self.enabled
        )
        reasons = []
        if not enabled:
            reasons.append("MERCADO_LIVRE_ANSWERS_DISABLED")
        if self.token_provider is None:
            reasons.append("MERCADO_LIVRE_NOT_CONNECTED")
        is_pack = conversation_id.startswith("pack-")
        limit = self.limits["post_sale_max_text" if is_pack else "max_text"]
        if not text.strip() or len(text) > limit:
            reasons.append("INVALID_TEXT")
        if is_pack and not self.seller_id:
            reasons.append("MERCADO_LIVRE_SELLER_ID_MISSING")
        if reasons:
            return {"status": "BLOCKED", "reasons": reasons}
        if is_pack:
            pack_id = conversation_id.removeprefix("pack-")
            response = self.session.post(
                f"{ML_API}/messages/packs/{pack_id}/sellers/{self.seller_id}",
                headers=self._headers(),
                params={"tag": "post_sale"},
                json={"from": {"user_id": self.seller_id}, "text": text},
                timeout=self.timeout,
            )
        else:
            response = self.session.post(
                f"{ML_API}/answers",
                headers=self._headers(),
                json={
                    "question_id": int(conversation_id.removeprefix("q-")),
                    "text": text,
                },
                timeout=self.timeout,
            )
        if not response.ok:
            return {"status": "FAILED", "error": f"ML_HTTP_{response.status_code}"}
        return {"status": "SENT", "message_id": f"ml-{conversation_id}"}


# -- Shopee, TikTok Shop e Amazon --------------------------------------------------


class ManualPasteAdapter:
    """Contrato pronto, API não verificada: o fundador cola o rascunho no painel."""

    api_status = "API_CONTRACT_UNVERIFIED"
    limits: ClassVar[dict[str, Any]] = {"max_text": 1000}

    def __init__(self, channel: str) -> None:
        self.channel = channel

    def window_open(self, conversation_id: str, now: datetime) -> bool:
        return True

    def send(
        self,
        conversation_id: str,
        text: str,
        *,
        idempotency_key: str,
        human_written: bool = False,
    ) -> dict[str, Any]:
        return {"status": "BLOCKED", "reasons": ["API_CONTRACT_UNVERIFIED"]}


# -- alerta ao fundador ------------------------------------------------------------


class FounderAlertSender:
    """Template de utilidade ``alerta_fundador`` para ``FOUNDER_ALERT_WHATSAPP``.

    Sem envio liberado ou sem número, tenta o e-mail (``FOUNDER_ALERT_EMAIL`` e
    ``SMTP_*``); sem nada configurado, o alerta fica registrado em
    ``/os/conversations/alerts``.
    """

    def __init__(
        self,
        *,
        whatsapp_client: Any = None,
        founder_number: str | None = None,
        whatsapp_enabled: bool | None = None,
        email_sender: Callable[[str, str], None] | None = None,
    ) -> None:
        self.client = whatsapp_client
        self.number = (founder_number or "").strip()
        self.whatsapp_enabled = whatsapp_enabled
        self.email_sender = email_sender

    @classmethod
    def from_env(cls) -> FounderAlertSender:
        from .whatsapp import WhatsAppCloudClient

        return cls(
            whatsapp_client=WhatsAppCloudClient.from_env(),
            founder_number=os.getenv("FOUNDER_ALERT_WHATSAPP", ""),
            email_sender=smtp_sender_from_env(),
        )

    def __call__(self, alert: dict[str, Any]) -> str | None:
        enabled = (
            env_flag("WHATSAPP_SEND_ENABLED")
            if self.whatsapp_enabled is None
            else self.whatsapp_enabled
        )
        parameters = [alert["reason"], alert["summary"], alert["suggestion"]]
        if enabled and self.client is not None and self.number:
            try:
                send_whatsapp_template(
                    self.client, self.number, "alerta_fundador", parameters
                )
                return "WHATSAPP_TEMPLATE"
            except Exception as exc:  # noqa: BLE001 - cai para o e-mail
                alert = {**alert, "whatsapp_error": type(exc).__name__}
        if self.email_sender is not None:
            self.email_sender(
                f"Veratus: atendimento precisa de você ({alert['reason']})",
                "\n\n".join(
                    [
                        f"Canal: {alert['channel']}",
                        f"Motivo: {alert['reason']}",
                        alert["summary"],
                        f"Sugestão: {alert['suggestion']}",
                    ]
                ),
            )
            return "EMAIL"
        return None


def smtp_sender_from_env() -> Callable[[str, str], None] | None:
    host = os.getenv("SMTP_HOST", "").strip()
    to = os.getenv("FOUNDER_ALERT_EMAIL", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    if not (host and to and user and password):
        return None
    port = int(os.getenv("SMTP_PORT", "587") or "587")

    def send(subject: str, body: str) -> None:
        import smtplib
        from email.message import EmailMessage

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = user
        message["To"] = to
        message.set_content(body)
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(message)

    return send


def adapter_contract(adapter: Any) -> dict[str, Any]:
    """O que todo adaptador expõe (usado no teste de contrato e em /os)."""
    return {
        "channel": adapter.channel,
        "api_status": adapter.api_status,
        "limits": json.loads(json.dumps(adapter.limits)),
        "methods": sorted(
            name for name in ("window_open", "send") if callable(getattr(adapter, name))
        ),
    }
