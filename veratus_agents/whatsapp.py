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
_HINT = re.compile(r"^[a-z0-9-]{2,60}$")

# Mídia sem texto: o motor responde com texto fixo (intenção midia_recebida).
MEDIA_KINDS = ("audio", "image", "video", "document", "sticker", "location")
# Clique em botão ou lista que nós enviamos: o id vira a frase que o motor entende.
REPLY_IDS = {
    "quero_pedir": "Quero pedir",
    "ver_modelos": "Quero ver os modelos",
    "falar_responsavel": "Quero falar com o responsável",
}
# Campos da coexistência que não são mensagem nova (importação e sincronização).
IGNORED_FIELDS = ("history", "smb_app_state_sync")
# Saída do marketing: a mensagem inteira é a palavra (não "quero sair hoje").
OPT_OUT_WORDS = ("parar", "pare", "sair", "stop", "descadastrar")
# Só estes dois vão a cliente; ``alerta_fundador`` vai ao número do fundador.
CUSTOMER_TEMPLATES = ("pedido_confirmado", "pedido_enviado")
OPT_OUT_PHRASES = ("nao quero receber", "nao quero mais receber", "parar de receber")
OPT_OUT_CONFIRMATION = (
    "Pronto, você não vai mais receber novidades da Veratus por aqui. "
    "Se precisar de algo, é só escrever."
)
TEMPLATES_PATH = Path(__file__).resolve().parents[1] / "config/whatsapp_templates.json"
BUTTON_TITLE_MAX = 20


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
    product_hint: str | None = None


@dataclass(frozen=True)
class StatusUpdate:
    message_id: str
    status: str
    error_codes: tuple[str, ...] = ()


@dataclass
class Notification:
    """O que o webhook trouxe: mensagens, status, ecos do app e templates."""

    messages: list[InboundMessage] = field(default_factory=list)
    statuses: list[StatusUpdate] = field(default_factory=list)
    # Coexistência: resposta do fundador pelo app do celular (smb_message_echoes).
    echoes: list[InboundMessage] = field(default_factory=list)
    templates: list[dict[str, str]] = field(default_factory=list)
    ignored: int = 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _from_epoch(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return _now()


def _message_text(item: dict[str, Any], kind: str) -> tuple[str | None, str | None]:
    """Texto e produto de cada tipo; mídia fica sem texto (o motor responde fixo)."""
    if kind == "text":
        return (item.get("text") or {}).get("body"), None
    if kind == "button":  # botão de resposta rápida de um template
        return (item.get("button") or {}).get("text"), None
    if kind == "interactive":
        interactive = item.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        reply_id = str(reply.get("id") or "")
        hint = reply_id.removeprefix("produto:")
        return (
            REPLY_IDS.get(reply_id) or reply.get("title"),
            hint if reply_id.startswith("produto:") and _HINT.match(hint) else None,
        )
    if kind == "order":  # carrinho do catálogo: o primeiro item vira o produto
        items = (item.get("order") or {}).get("product_items") or [{}]
        hint = str(items[0].get("product_retailer_id") or "")
        if _HINT.match(hint):
            return f"Quero pedir {hint.replace('-', ' ')}", hint
    return None, None


def _inbound(item: dict[str, Any], wa_id_key: str) -> InboundMessage | None:
    message_id = str(item.get("id") or "")
    wa_id = str(item.get(wa_id_key) or "")
    if not message_id or not wa_id:
        return None
    kind = str(item.get("type") or "unknown")
    text, hint = _message_text(item, kind)
    referral = {
        key: str(val)[:500]
        for key, val in (item.get("referral") or {}).items()
        if key in REFERRAL_FIELDS and val
    }
    return InboundMessage(
        message_id=message_id[:200],
        wa_id=wa_id[:32],
        sent_at=_from_epoch(item.get("timestamp")),
        kind=kind[:40],
        text=str(text)[:MAX_TEXT_LENGTH] if text else None,
        referral=referral,
        product_hint=hint,
    )


def parse_notification(
    payload: dict[str, Any], *, phone_number_id: str | None = None
) -> Notification:
    """Mensagens, status, ecos e templates; o resto é ignorado.

    Com ``phone_number_id``, mensagens de outro número do mesmo app (o número de
    teste da Meta, por exemplo) não entram nas conversas reais.
    """
    found = Notification()
    if payload.get("object") != "whatsapp_business_account":
        return found
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            name = (change or {}).get("field")
            value = change.get("value") or {}
            if name in IGNORED_FIELDS:
                found.ignored += 1
                continue
            if name in ("message_template_status_update", "template_category_update"):
                template = str(value.get("message_template_name") or "")[:120]
                if template:
                    found.templates.append(
                        {
                            "name": template,
                            "language": str(
                                value.get("message_template_language") or "pt_BR"
                            )[:20],
                            "status": str(value.get("event") or "")[:40],
                            "category": str(value.get("new_category") or "")[:40],
                        }
                    )
                continue
            if name not in ("messages", "smb_message_echoes"):
                continue
            # Sem metadata (não acontece na Meta) a mensagem segue: a assinatura já
            # provou a origem; com outro número, fica de fora.
            received_pnid = str(
                (value.get("metadata") or {}).get("phone_number_id") or ""
            )
            if phone_number_id and received_pnid and received_pnid != phone_number_id:
                found.ignored += 1
                continue
            if name == "smb_message_echoes":
                # Forma do eco a conferir na hora de ligar (envio de teste pelo app).
                for item in value.get("message_echoes") or []:
                    echo = _inbound(item, "to")
                    if echo is not None:
                        found.echoes.append(echo)
                continue
            for item in value.get("messages") or []:
                message = _inbound(item, "from")
                if message is not None:
                    found.messages.append(message)
            for item in value.get("statuses") or []:
                if item.get("id") and item.get("status"):
                    codes = tuple(
                        str(error.get("code"))[:12]
                        for error in item.get("errors") or []
                        if isinstance(error, dict) and error.get("code") is not None
                    )
                    found.statuses.append(
                        StatusUpdate(
                            str(item["id"])[:200], str(item["status"])[:40], codes
                        )
                    )
    return found


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
    """Mesma lista e mesma regra do motor (PEDIDO_DE_ATENDENTE)."""
    from .conversations import asks_for_person

    return asks_for_person(text)


def is_opt_out(text: str | None) -> bool:
    """Pedido para não receber mais marketing ("parar", "não quero receber")."""
    clean = " ".join(re.sub(r"[^\w\s]", " ", normalize_text(text or "")).split())
    return clean in OPT_OUT_WORDS or any(phrase in clean for phrase in OPT_OUT_PHRASES)


def load_templates(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Registro de templates (``config/whatsapp_templates.json``), por nome."""
    data = json.loads((path or TEMPLATES_PATH).read_text(encoding="utf-8"))
    return {item["name"]: item for item in data.get("templates") or []}


def clean_template_param(value: Any) -> str:
    """A Meta recusa quebra de linha, tab e 4 ou mais espaços num parâmetro."""
    return " ".join(str(value or "").split())[:1000]


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
        return self.send_message(
            to, {"type": "text", "text": {"preview_url": False, "body": body}}
        )

    def send_message(self, to: str, content: dict[str, Any]) -> str:
        """Texto, template, interativo, imagem ou produto: o mesmo envio."""
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual"}
        response = self._post({**payload, "to": to, **content})
        try:
            return str(response.json()["messages"][0]["id"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise WhatsAppApiError("UNEXPECTED_RESPONSE", response.status_code) from exc

    def mark_read(self, message_id: str, *, typing: bool = True) -> None:
        """Confirmação de leitura e "digitando…" (some no envio ou em 25 s)."""
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        if typing:
            payload["typing_indicator"] = {"type": "text"}
        self._post(payload)

    def get(self, path: str, fields: str) -> dict[str, Any]:
        """Leitura da conta (rota de saúde); só o código de erro da Meta volta."""
        response = self.session.get(
            f"https://graph.facebook.com/{graph_api_version()}/{path}",
            headers={"Authorization": f"Bearer {self.access_token}"},
            params={"fields": fields} if fields else None,
            timeout=self.timeout,
        )
        self._raise_for(response)
        try:
            return dict(response.json())
        except (ValueError, TypeError) as exc:
            raise WhatsAppApiError("UNEXPECTED_RESPONSE", response.status_code) from exc

    def _post(self, payload: dict[str, Any]) -> Any:
        response = self.session.post(
            f"https://graph.facebook.com/{graph_api_version()}/"
            f"{self.phone_number_id}/messages",
            headers={"Authorization": f"Bearer {self.access_token}"},
            json=payload,
            timeout=self.timeout,
        )
        self._raise_for(response)
        return response

    @staticmethod
    def _raise_for(response: Any) -> None:
        if not response.ok:
            # Only Meta's numeric error code travels; never the body or token.
            try:
                code = str(response.json().get("error", {}).get("code", "unknown"))
            except (ValueError, AttributeError):
                code = "unknown"
            raise WhatsAppApiError(f"META_ERROR_{code}", response.status_code)


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
CREATE TABLE IF NOT EXISTS wa_optins (
    conversation_id TEXT PRIMARY KEY,
    granted_at TEXT,
    source TEXT,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS wa_templates (
    name TEXT NOT NULL,
    language TEXT NOT NULL,
    status TEXT NOT NULL,
    category TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (name, language)
);
"""

DraftRunner = Callable[..., dict[str, Any]]
RunLookup = Callable[[str], dict[str, Any] | None]
# (conversa, mensagem, texto) de cada pedido de pessoa que tirou a conversa do BOT.
HandoffCallback = Callable[[str, str, str], None]


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

    def receive(
        self,
        payload: dict[str, Any],
        *,
        on_handoff: HandoffCallback | None = None,
        on_founder_reply: Callable[[str], None] | None = None,
        on_send_failed: Callable[[str, str], None] | None = None,
        on_opt_out: Callable[[str, str], None] | None = None,
        phone_number_id: str | None = None,
    ) -> dict[str, int]:
        """Store new messages and statuses. Meta retries, so replays are no-ops.

        ``on_handoff`` é chamado uma vez por pedido de pessoa, depois de gravar:
        o motor registra a CHAMADA ao fundador (alerta e estado HUMAN).
        ``on_founder_reply`` (resposta pelo app, coexistência), ``on_send_failed``
        (status ``failed``) e ``on_opt_out`` também rodam uma vez, fora da transação.
        """
        if phone_number_id is None:
            phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        found = parse_notification(payload, phone_number_id=phone_number_id or None)
        counts = {
            "messages": 0,
            "duplicates": 0,
            "statuses": 0,
            "handoffs": 0,
            "echoes": 0,
            "ignored": found.ignored,
        }
        requested: list[tuple[str, str, str]] = []
        founder: list[str] = []
        failed: list[tuple[str, str]] = []
        opted_out: list[tuple[str, str]] = []
        now = _now().isoformat()
        with self._connect() as (db, mark):
            for message in found.messages:
                conversation_id = self._ensure_conversation(db, mark, message, now)
                conversation = self._conversation_row(db, mark, conversation_id)
                handoff = bool(message.text and wants_human(message.text))
                opt_out = is_opt_out(message.text)
                state = conversation["state"]
                if handoff and state == "BOT":
                    state = "HUMAN"
                draft_status = (
                    "NOT_TEXT"
                    if not message.text and message.kind not in MEDIA_KINDS
                    else "OPT_OUT"
                    if opt_out
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
                if opt_out:
                    self._revoke_opt_in(db, mark, conversation_id, now)
                    opted_out.append((conversation_id, message.message_id))
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
                    requested.append(
                        (conversation_id, message.message_id, message.text or "")
                    )
            for echo in found.echoes:
                conversation_id = self._echo(db, mark, echo, now)
                if conversation_id:
                    counts["echoes"] += 1
                    founder.append(conversation_id)
            for status in found.statuses:
                updated = self._status(db, mark, status)
                counts["statuses"] += updated
                if updated and status.status == "failed":
                    failed.append(self._failed(db, mark, status))
            for template in found.templates:
                self._template_status(db, mark, template, now)
        # Fora da transação: o motor grava no mesmo banco.
        for item in requested:
            if on_handoff is not None:
                on_handoff(*item)
        for conversation_id in dict.fromkeys(founder):
            if on_founder_reply is not None:
                on_founder_reply(conversation_id)
        for item in failed:
            if on_send_failed is not None and item[0]:
                on_send_failed(*item)
        for item in opted_out:
            if on_opt_out is not None:
                on_opt_out(*item)
        return counts

    def _ensure_conversation(
        self, db: Any, mark: str, message: InboundMessage, now: str, state: str = "BOT"
    ) -> str:
        conversation_id = self.conversation_id(message.wa_id)
        db.execute(
            self._sql(
                "INSERT INTO wa_conversations (conversation_id, "
                "wa_id_encrypted, wa_id_last4, state, utm_json, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, '{}', ?, ?) "
                "ON CONFLICT (conversation_id) DO NOTHING",
                mark,
            ),
            [
                conversation_id,
                self._cipher.encrypt(message.wa_id.encode()).decode("ascii"),
                message.wa_id[-4:],
                state,
                now,
                now,
            ],
        )
        return conversation_id

    def _echo(self, db: Any, mark: str, echo: InboundMessage, now: str) -> str | None:
        """Resposta do fundador pelo app: OUT de ``founder-app`` e conversa em HUMAN."""
        conversation_id = self._ensure_conversation(db, mark, echo, now, "HUMAN")
        inserted = db.execute(
            self._sql(
                "INSERT INTO wa_messages (message_id, conversation_id, direction, "
                "kind, body, sent_at, delivery_status, send_key, actor) "
                "VALUES (?, ?, 'OUT', ?, ?, ?, 'sent', ?, 'founder-app') "
                "ON CONFLICT (message_id) DO NOTHING",
                mark,
            ),
            [
                echo.message_id,
                conversation_id,
                echo.kind,
                echo.text,
                echo.sent_at.isoformat(),
                f"echo:{echo.message_id}",
            ],
        ).rowcount
        if not inserted:
            return None
        changed = db.execute(
            self._sql(
                "UPDATE wa_conversations SET state = 'HUMAN', updated_at = ? "
                "WHERE conversation_id = ? AND state = 'BOT'",
                mark,
            ),
            [now, conversation_id],
        ).rowcount
        if changed:
            self._audit(
                db,
                mark,
                conversation_id,
                "STATE_CHANGED",
                "founder-app",
                {"from": "BOT", "to": "HUMAN", "message_id": echo.message_id},
            )
        return conversation_id

    def _status(self, db: Any, mark: str, status: StatusUpdate) -> int:
        # "failed" grava uma vez: a Meta repete o webhook e a tarefa não duplica.
        guard = " AND COALESCE(delivery_status, '') <> 'failed'"
        return db.execute(
            self._sql(
                "UPDATE wa_messages SET delivery_status = ? "
                "WHERE message_id = ? AND direction = 'OUT'"
                + (guard if status.status == "failed" else ""),
                mark,
            ),
            [status.status, status.message_id],
        ).rowcount

    def _failed(self, db: Any, mark: str, status: StatusUpdate) -> tuple[str, str]:
        """Envio recusado depois de aceito (131047 fora da janela, 131026...)."""
        row = db.execute(
            self._sql(
                "SELECT conversation_id FROM wa_messages WHERE message_id = ?", mark
            ),
            [status.message_id],
        ).fetchone()
        conversation_id = row["conversation_id"] if row else ""
        codes = ",".join(status.error_codes) or "unknown"
        self._audit(
            db,
            mark,
            conversation_id or None,
            "DELIVERY_FAILED",
            "meta",
            {"message_id": status.message_id, "error_codes": list(status.error_codes)},
        )
        return conversation_id, codes

    def _template_status(
        self, db: Any, mark: str, template: dict[str, str], now: str
    ) -> None:
        """Aprovação, pausa, rejeição ou nova categoria vinda da Meta."""
        status = template["status"] or None
        category = template["category"] or None
        db.execute(
            self._sql(
                "INSERT INTO wa_templates (name, language, status, category, "
                "updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (name, language) DO UPDATE SET "
                "status = COALESCE(?, wa_templates.status), "
                "category = COALESCE(?, wa_templates.category), updated_at = ?",
                mark,
            ),
            [
                template["name"],
                template["language"],
                status or "UNKNOWN",
                category,
                now,
                status,
                category,
                now,
            ],
        )

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
                conversation["product_id"]
                or attribution["product_id"]
                or message.product_hint,
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
        """Legado (rota aposentada): rascunho do Sales Agent para mensagens novas.

        O motor (``ConversationEngine``) é quem consome as mensagens ``NEW``.

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
                    "AND m.body IS NOT NULL "
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

    def pending_inbound(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Mensagens novas de conversas em BOT, para o motor de atendimento."""
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql(
                    "SELECT m.message_id, m.conversation_id, m.body, m.kind, "
                    "c.product_id, c.utm_json FROM wa_messages m "
                    "JOIN wa_conversations c "
                    "ON c.conversation_id = m.conversation_id "
                    "WHERE m.direction = 'IN' AND m.draft_status = 'NEW' "
                    "AND c.state = 'BOT' ORDER BY m.sent_at LIMIT ?",
                    mark,
                ),
                [max(1, min(limit, 100))],
            ).fetchall()
        return [
            {**dict(row), "utm": json.loads(row["utm_json"] or "{}")} for row in rows
        ]

    def redact_inbound(self, message_id: str) -> None:
        """Nome, CEP e endereço ficam só cifrados no atendimento, não aqui."""
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "UPDATE wa_messages SET body = ? "
                    "WHERE message_id = ? AND direction = 'IN'",
                    mark,
                ),
                ["[dados do pedido guardados cifrados]", message_id],
            )

    def mark_inbound(self, message_id: str, status: str) -> None:
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "UPDATE wa_messages SET draft_status = ? "
                    "WHERE message_id = ? AND draft_status = 'NEW'",
                    mark,
                ),
                [status[:40], message_id],
            )

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

    # -- opt-in, templates e outros formatos ---------------------------------------

    def grant_opt_in(self, conversation_id: str, *, source: str) -> None:
        """Opt-in de marketing registrado (data e origem); renovar limpa a saída."""
        now, source = _now().isoformat(), source.strip()[:80]
        if not source:
            raise ValueError("source_required")
        with self._connect() as (db, mark):
            if self._conversation_row(db, mark, conversation_id) is None:
                raise KeyError(conversation_id)
            db.execute(
                self._sql(
                    "INSERT INTO wa_optins (conversation_id, granted_at, source, "
                    "revoked_at) VALUES (?, ?, ?, NULL) ON CONFLICT (conversation_id) "
                    "DO UPDATE SET granted_at = ?, source = ?, revoked_at = NULL",
                    mark,
                ),
                [conversation_id, now, source, now, source],
            )
            self._audit(
                db, mark, conversation_id, "OPT_IN", "customer", {"source": source}
            )

    def opt_in(self, conversation_id: str) -> dict[str, Any] | None:
        """Opt-in vigente, para ``marketing_allowed``; revogado volta ``None``."""
        with self._connect() as (db, mark):
            row = db.execute(
                self._sql("SELECT * FROM wa_optins WHERE conversation_id = ?", mark),
                [conversation_id],
            ).fetchone()
        return dict(row) if row is not None and not row["revoked_at"] else None

    def _revoke_opt_in(
        self, db: Any, mark: str, conversation_id: str, now: str
    ) -> None:
        db.execute(
            self._sql(
                "INSERT INTO wa_optins (conversation_id, revoked_at) VALUES (?, ?) "
                "ON CONFLICT (conversation_id) DO UPDATE SET revoked_at = ?",
                mark,
            ),
            [conversation_id, now, now],
        )
        self._audit(db, mark, conversation_id, "OPT_OUT", "customer", {})

    def template_reasons(
        self, name: str, registry: dict[str, dict[str, Any]] | None = None
    ) -> list[str]:
        """O que impede o template: registro, categoria e o último aviso da Meta."""
        registry = load_templates() if registry is None else registry
        item = registry.get(name)
        if item is None:
            return ["TEMPLATE_UNKNOWN"]
        reasons = []
        if item.get("status") != "APPROVED":
            reasons.append("TEMPLATE_NOT_APPROVED")
        if item.get("category") != "UTILITY":
            reasons.append("TEMPLATE_NOT_UTILITY")
        with self._connect() as (db, mark):
            row = db.execute(
                self._sql(
                    "SELECT status, category FROM wa_templates "
                    "WHERE name = ? AND language = ?",
                    mark,
                ),
                [name, item.get("language") or "pt_BR"],
            ).fetchone()
        if row is not None and row["status"] not in (
            "APPROVED",
            "REINSTATED",
            "UNKNOWN",
        ):
            reasons.append(f"TEMPLATE_{row['status']}"[:60])
        if row is not None and row["category"] not in (None, "UTILITY"):
            reasons.append("TEMPLATE_RECLASSIFIED")
        return reasons

    def send_template(
        self,
        conversation_id: str,
        name: str,
        params: list[Any],
        *,
        actor: str,
        client: WhatsAppCloudClient | None,
        enabled: bool,
        templates_enabled: bool,
        idempotency_key: str,
        registry: dict[str, dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Template de utilidade aprovado, o único envio fora da janela de 24 h."""
        registry = load_templates() if registry is None else registry
        item = registry.get(name) or {}
        values = [clean_template_param(value) for value in params]
        reasons = [] if templates_enabled else ["WHATSAPP_TEMPLATES_DISABLED"]
        if name not in CUSTOMER_TEMPLATES:
            reasons.append("TEMPLATE_NOT_ALLOWED")
        reasons += self.template_reasons(name, registry)
        if len(values) != len(item.get("params") or []) or not all(values):
            reasons.append("TEMPLATE_PARAMS_INVALID")
        body = str(item.get("body") or name)
        for index, value in enumerate(values, start=1):
            body = body.replace("{{" + str(index) + "}}", value)
        content = {
            "type": "template",
            "template": {
                "name": name,
                "language": {"code": item.get("language") or "pt_BR"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": v} for v in values],
                    }
                ],
            },
        }
        return self.deliver(
            conversation_id,
            content,
            body=body,
            kind="template",
            actor=actor,
            client=client,
            enabled=enabled,
            idempotency_key=idempotency_key,
            reasons=reasons,
            window=False,
            now=now,
        )

    def deliver(
        self,
        conversation_id: str,
        content: dict[str, Any],
        *,
        body: str,
        kind: str,
        actor: str,
        client: WhatsAppCloudClient | None,
        enabled: bool,
        idempotency_key: str,
        reasons: list[str] | None = None,
        window: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Envio de template, botões, lista, imagem ou produto, com as mesmas travas.

        ``body`` é o texto que fica no histórico e passa pela checagem de texto. A
        chave de idempotência é a mesma do texto (``human:``): um formato não
        repete o que o outro já mandou.
        """
        now = now or _now()
        send_key = f"human:{idempotency_key}"
        reasons = list(reasons or [])
        with self._connect() as (db, mark):
            conversation = self._conversation_row(db, mark, conversation_id)
            previous = db.execute(
                self._sql(
                    "SELECT message_id FROM wa_messages WHERE send_key = ?", mark
                ),
                [send_key],
            ).fetchone()
        if conversation is None:
            return {
                "status": "BLOCKED",
                "reasons": ["CONVERSATION_NOT_FOUND"],
                "external_message_sent": False,
            }
        if previous is not None:
            return {"status": "duplicate", "message_id": previous["message_id"]}
        reasons += [
            reason
            for reason in send_block_reasons(
                conversation,
                body,
                now=now,
                enabled=enabled,
                credentials_present=client is not None,
            )
            if window or reason != "CUSTOMER_SERVICE_WINDOW_CLOSED"
        ]
        detail = {"send_key": send_key, "kind": kind, "reasons": reasons}
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
            message_id = client.send_message(wa_id, content)
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
                    "kind, body, sent_at, delivery_status, send_key, actor) "
                    "VALUES (?, ?, 'OUT', ?, ?, ?, 'accepted', ?, ?)",
                    mark,
                ),
                [
                    message_id,
                    conversation_id,
                    kind,
                    body,
                    now.isoformat(),
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

    def claim_read_receipt(self, message_id: str) -> bool:
        """Uma confirmação de leitura por mensagem recebida, entre workers."""
        with self._connect() as (db, mark):
            return bool(
                db.execute(
                    self._sql(
                        "UPDATE wa_messages SET delivery_status = 'read_sent' "
                        "WHERE message_id = ? AND direction = 'IN' "
                        "AND delivery_status IS NULL",
                        mark,
                    ),
                    [message_id],
                ).rowcount
            )

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

    def record_audit(
        self, conversation_id: str, action: str, actor: str, detail: dict[str, Any]
    ) -> None:
        self._record_audit(conversation_id, action, actor, detail)

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


def templates_enabled() -> bool:
    return env_flag("WHATSAPP_TEMPLATES_ENABLED")


# -- mensagens interativas (botões, lista e botão de link) ------------------------

SALES_BUTTONS = (
    ("quero_pedir", "Quero pedir"),
    ("ver_modelos", "Ver outros"),
    ("falar_responsavel", "Chamar o responsável"),
)
COLLECTION_ROWS = (
    ("colecao:classicos", "Clássicos"),
    ("colecao:esportivos", "Esportivos"),
    ("colecao:marcantes", "Marcantes"),
)
REPLY_IDS.update(
    {
        "colecao:classicos": "Quero ver os modelos clássicos",
        "colecao:esportivos": "Quero ver os modelos esportivos",
        "colecao:marcantes": "Quero ver os modelos marcantes",
    }
)


def reply_buttons(body: str, buttons: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    """Até 3 botões de resposta, título de até 20 caracteres."""
    if not 1 <= len(buttons) <= 3 or any(
        not title or len(title) > BUTTON_TITLE_MAX for _, title in buttons
    ):
        raise ValueError("botoes_invalidos")
    return {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": key, "title": title}}
                    for key, title in buttons
                ]
            },
        },
    }


def list_message(
    body: str, button: str, rows: tuple[tuple[str, str], ...]
) -> dict[str, Any]:
    """Lista de até 10 linhas (título de até 24 caracteres)."""
    if not 1 <= len(rows) <= 10 or len(button) > BUTTON_TITLE_MAX:
        raise ValueError("lista_invalida")
    if any(not title or len(title) > 24 for _, title in rows):
        raise ValueError("lista_invalida")
    return {
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button,
                "sections": [
                    {
                        "title": "Coleção",
                        "rows": [{"id": key, "title": title} for key, title in rows],
                    }
                ],
            },
        },
    }


def cta_url(body: str, display_text: str, url: str) -> dict[str, Any]:
    """Botão que abre o link (o do Mercado Pago); a conferir na hora de ligar."""
    if len(display_text) > BUTTON_TITLE_MAX or not url.startswith("https://"):
        raise ValueError("botao_invalido")
    return {
        "type": "interactive",
        "interactive": {
            "type": "cta_url",
            "body": {"text": body},
            "action": {
                "name": "cta_url",
                "parameters": {"display_text": display_text, "url": url},
            },
        },
    }


def confirm_opt_out(
    channel: WhatsAppChannel,
    conversation_id: str,
    message_id: str,
    *,
    client: WhatsAppCloudClient | None,
) -> dict[str, Any]:
    """Confirma a saída na janela aberta; em SHADOW ou com o envio desligado, não sai."""
    from .conversations import channel_mode

    if channel_mode("whatsapp") == "SHADOW":
        return {"status": "SHADOW", "external_message_sent": False}
    return channel.send(
        conversation_id,
        actor="opt-out",
        run_lookup=lambda _run_id: None,
        client=client,
        enabled=send_enabled(),
        text=OPT_OUT_CONFIRMATION,
        idempotency_key=f"optout:{message_id}",
    )


# -- saúde da conta (só leitura) ------------------------------------------------

NUMBER_FIELDS = (
    "display_phone_number,verified_name,name_status,quality_rating,"
    "code_verification_status"
)


def account_health(
    client: WhatsAppCloudClient | None,
    waba_id: str | None,
    *,
    expected_number: str,
    registry: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Número, nome aprovado, qualidade, app assinado e templates, lidos na Meta.

    Sem token, número ou WABA não há chamada. Campos a conferir na hora de ligar.
    """
    missing = [
        name
        for name, value in (
            ("WHATSAPP_ACCESS_TOKEN/WHATSAPP_PHONE_NUMBER_ID", client),
            ("WHATSAPP_BUSINESS_ACCOUNT_ID", waba_id),
        )
        if not value
    ]
    if missing:
        return {"status": "NOT_CONFIGURED", "missing": missing, "external_call": False}
    registry = load_templates() if registry is None else registry
    try:
        number = client.get(client.phone_number_id, NUMBER_FIELDS)
        apps = client.get(f"{waba_id}/subscribed_apps", "")
        remote = client.get(
            f"{waba_id}/message_templates", "name,status,category,language"
        )
    except WhatsAppApiError as exc:
        return {"status": "ERROR", "error": exc.code, "external_call": True}
    found = {
        (item.get("name"), item.get("language")): item
        for item in remote.get("data") or []
        if isinstance(item, dict)
    }
    templates = {}
    for name, item in registry.items():
        meta = found.get((name, item.get("language"))) or {}
        templates[name] = {
            "registry": item.get("status"),
            "meta": meta.get("status"),
            "category": meta.get("category"),
        }
    checks = {
        "number_matches": re.sub(r"\D", "", str(number.get("display_phone_number")))
        == expected_number,
        "name_approved": number.get("name_status") == "APPROVED",
        "quality_ok": number.get("quality_rating") in ("GREEN", "UNKNOWN"),
        "app_subscribed": bool(apps.get("data")),
        "templates_approved": all(
            item["meta"] == "APPROVED" and item["category"] == "UTILITY"
            for item in templates.values()
        ),
    }
    return {
        "status": "OK" if all(checks.values()) else "ATTENTION",
        "checks": checks,
        "number": {key: number.get(key) for key in NUMBER_FIELDS.split(",")},
        "templates": templates,
        "external_call": True,
    }
