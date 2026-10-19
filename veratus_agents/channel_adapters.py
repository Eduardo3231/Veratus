"""Adaptadores de conversa: mesma interface (receber, responder, janela, limites).

- WhatsApp: o canal atual (``whatsapp.py``), com janela de 24 h, templates de
  utilidade (``pedido_confirmado``, ``pedido_enviado``, ``alerta_fundador``) e
  marketing só com opt-in registrado.
- Instagram Direct: campo ``messages`` do webhook já existente, janela de 24 h;
  a tag ``HUMAN_AGENT`` só em mensagem escrita por pessoa.
- Facebook Messenger (canal principal de venda no Facebook): mensagens à Página,
  vindas do anúncio, da loja ou do botão "Enviar mensagem"; mesma caixa cifrada,
  mesma janela de 24 h e a mesma regra da tag ``HUMAN_AGENT``.
- Mercado Livre: perguntas pré-venda e mensagens pós-venda.
- Shopee, TikTok Shop e Amazon: ``API_CONTRACT_UNVERIFIED``; o rascunho vai
  para a fila e o fundador cola no painel do canal.

Nenhum envio acontece com as chaves desligadas; o motivo volta como ``BLOCKED``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

import requests
from cryptography.fernet import Fernet

from .meta_webhooks import env_flag, graph_api_version
from .sqlstore import SqlStore

logger = logging.getLogger(__name__)

SERVICE_WINDOW = timedelta(hours=24)
HUMAN_AGENT_WINDOW = timedelta(days=7)
UTILITY_TEMPLATES = ("pedido_confirmado", "pedido_enviado", "alerta_fundador")
REDACTED_BODY = "[dados do pedido guardados cifrados]"


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
        order_lookup: Callable[[str], str | None] | None = None,
    ) -> None:
        self.wa = whatsapp_channel
        self.client = client
        self.enabled = enabled
        # conversation_id → pedido vinculado no motor (template só com pedido).
        self.order_lookup = order_lookup

    def _send_enabled(self) -> bool:
        from .whatsapp import send_enabled

        return send_enabled() if self.enabled is None else self.enabled

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
        actor = "founder" if human_written else "sales-agent"
        link = _MERCADO_PAGO_LINK.search(text or "")
        if link and env_flag("WHATSAPP_INTERACTIVE_ENABLED") and len(text) <= 1024:
            # O link de pagamento vai também como botão; desligado, só texto.
            from .whatsapp import cta_url

            return self._deliver(
                conversation_id,
                cta_url(text, "Abrir o pagamento", link.group(0)),
                text,
                "interactive",
                idempotency_key,
                actor=actor,
            )
        return self.wa.send(
            conversation_id,
            actor=actor,
            run_lookup=lambda _run_id: None,
            client=self.client,
            enabled=self._send_enabled(),
            text=text,
            idempotency_key=idempotency_key,
        )

    def send_template(
        self,
        conversation_id: str,
        name: str,
        *,
        idempotency_key: str,
        params: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Fora da janela de 24 h: template aprovado, só para conversa com pedido.

        Sem ``params``, o único parâmetro é o pedido vinculado (``pedido_confirmado``).
        """
        from .whatsapp import templates_enabled

        order_id = self.order_lookup(conversation_id) if self.order_lookup else None
        if not order_id:
            return {
                "status": "BLOCKED",
                "reasons": ["ORDER_NOT_LINKED"],
                "external_message_sent": False,
            }
        return self.wa.send_template(
            conversation_id,
            name,
            [order_id] if params is None else list(params),
            actor="sales-agent",
            client=self.client,
            enabled=self._send_enabled(),
            templates_enabled=templates_enabled(),
            idempotency_key=idempotency_key,
        )

    def send_buttons(
        self, conversation_id: str, body: str, *, idempotency_key: str
    ) -> dict[str, Any]:
        """Botões de venda (Quero pedir, Ver outros, Chamar o responsável)."""
        from .whatsapp import SALES_BUTTONS, reply_buttons

        titles = [title for _, title in SALES_BUTTONS]
        return self._interactive(
            conversation_id,
            body,
            titles,
            lambda: reply_buttons(body, SALES_BUTTONS),
            idempotency_key,
        )

    def send_collections(
        self, conversation_id: str, body: str, *, idempotency_key: str
    ) -> dict[str, Any]:
        """Lista das linhas da coleção (Clássicos, Esportivos, Marcantes)."""
        from .whatsapp import COLLECTION_ROWS, list_message

        titles = [title for _, title in COLLECTION_ROWS]
        return self._interactive(
            conversation_id,
            body,
            titles,
            lambda: list_message(body, "Ver coleção", COLLECTION_ROWS),
            idempotency_key,
        )

    def send_image(
        self, conversation_id: str, asset: str, caption: str, *, idempotency_key: str
    ) -> dict[str, Any]:
        """Foto de ``landing/assets`` fora da quarentena, legenda pelo gate."""
        from .meta_catalog import _image_link, site_base_url

        base = site_base_url()
        link = _image_link([asset], base) if base else None
        reasons = [] if env_flag("WHATSAPP_MEDIA_ENABLED") else ["MEDIA_DISABLED"]
        if not link:
            reasons.append("IMAGE_NOT_RELEASED")
        reasons += _gate_reasons([caption])
        content = {"type": "image", "image": {"link": link or "", "caption": caption}}
        return self._deliver(
            conversation_id, content, caption, "image", idempotency_key, reasons=reasons
        )

    def send_products(
        self,
        conversation_id: str,
        retailer_ids: list[str],
        body: str,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Produto ou lista de produtos do catálogo, só com peça elegível no feed.

        Forma de ``product_list`` a conferir na hora de ligar o catálogo ao número.
        """
        from .meta_catalog import feed_rows

        catalog_id = os.getenv("META_CATALOG_ID", "").strip()
        ready = {row["id"] for row in feed_rows() if row.get("ready")}
        reasons = [] if env_flag("WHATSAPP_MEDIA_ENABLED") else ["MEDIA_DISABLED"]
        if not catalog_id:
            reasons.append("CATALOG_NOT_CONFIGURED")
        if not retailer_ids or len(retailer_ids) > 30:
            reasons.append("PRODUCTS_INVALID")
        if any(item not in ready for item in retailer_ids):
            reasons.append("PRODUCT_NOT_IN_FEED")
        reasons += _gate_reasons([body])
        if len(retailer_ids) == 1:
            action: dict[str, Any] = {
                "catalog_id": catalog_id,
                "product_retailer_id": retailer_ids[0],
            }
            interactive = {"type": "product", "body": {"text": body}}
        else:
            action = {
                "catalog_id": catalog_id,
                "sections": [
                    {
                        "title": "Relógios",
                        "product_items": [
                            {"product_retailer_id": item} for item in retailer_ids
                        ],
                    }
                ],
            }
            interactive = {
                "type": "product_list",
                "header": {"type": "text", "text": "Veratus"},
                "body": {"text": body},
            }
        content = {
            "type": "interactive",
            "interactive": {**interactive, "action": action},
        }
        return self._deliver(
            conversation_id,
            content,
            body,
            "interactive",
            idempotency_key,
            reasons=reasons,
        )

    def marketing_allowed(self, conversation_id: str) -> bool:
        """Marketing só com o opt-in guardado e sem pedido de saída."""
        return marketing_allowed(self.wa.opt_in(conversation_id))

    def _interactive(
        self,
        conversation_id: str,
        body: str,
        titles: list[str],
        build: Callable[[], dict[str, Any]],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not env_flag("WHATSAPP_INTERACTIVE_ENABLED"):
            # Desligado: o mesmo texto, sem os botões.
            return self.send(conversation_id, body, idempotency_key=idempotency_key)
        return self._deliver(
            conversation_id,
            build(),
            body,
            "interactive",
            idempotency_key,
            reasons=_gate_reasons([body, *titles]),
        )

    def _deliver(
        self,
        conversation_id: str,
        content: dict[str, Any],
        body: str,
        kind: str,
        idempotency_key: str,
        *,
        actor: str = "sales-agent",
        reasons: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.wa.deliver(
            conversation_id,
            content,
            body=body,
            kind=kind,
            actor=actor,
            client=self.client,
            enabled=self._send_enabled(),
            idempotency_key=idempotency_key,
            reasons=reasons,
        )

    def pending(self, limit: int = 20) -> list[Any]:
        from .conversations import InboundText
        from .whatsapp import MEDIA_KINDS

        rows = self.wa.pending_inbound(limit=limit)
        self._read_receipts(rows)
        return [
            InboundText(
                channel="whatsapp",
                conversation_id=row["conversation_id"],
                message_id=row["message_id"],
                text=row["body"] or "",
                product_hint=row["product_id"],
                origin={"utm": row.get("utm") or {}},
                kind=row["kind"] if row.get("kind") in MEDIA_KINDS else "text",
            )
            for row in rows
        ]

    def _read_receipts(self, rows: list[dict[str, Any]]) -> None:
        """Recibo de leitura e "digitando…" ao pegar a mensagem, só nos níveis
        que respondem sozinhos (AUTO_SAFE e AUTO; nunca em SHADOW)."""
        from .conversations import channel_mode

        if (
            self.client is None
            or not rows
            or not env_flag("WHATSAPP_READ_RECEIPTS_ENABLED")
            or not self._send_enabled()
            or channel_mode("whatsapp") not in ("AUTO_SAFE", "AUTO")
        ):
            return
        for row in rows:
            if self.wa.claim_read_receipt(row["message_id"]):
                try:
                    self.client.mark_read(row["message_id"], typing=True)
                except Exception as exc:  # noqa: BLE001 - recibo não para o atendimento
                    self.wa.record_audit(
                        row["conversation_id"],
                        "READ_RECEIPT_FAILED",
                        "sales-agent",
                        {"error": type(exc).__name__},
                    )

    def mark(self, message_id: str, status: str) -> None:
        self.wa.mark_inbound(message_id, status)

    def redact(self, message_id: str) -> None:
        self.wa.redact_inbound(message_id)


_MERCADO_PAGO_LINK = re.compile(r"https://[\w.-]*mercadopago\.com(?:\.br)?/\S+")


def _gate_reasons(texts: list[str]) -> list[str]:
    """Títulos, legendas e corpos passam pela mesma checagem do texto ao cliente."""
    from .policy import hard_review_customer_reply

    issues = []
    for text in texts:
        issues += hard_review_customer_reply(text, channel="whatsapp")
    return [f"GATE: {issue}" for issue in dict.fromkeys(issues)]


def send_whatsapp_template(
    client: Any,
    to: str,
    template: str,
    parameters: list[str],
    *,
    language: str = "pt_BR",
) -> str:
    """Template ``alerta_fundador`` para o número do fundador.

    Template a cliente vai só por ``WhatsAppAdapter.send_template`` (conversa
    existente, pedido vinculado e histórico). Marketing exige opt-in e passa
    por ``marketing_allowed``.
    """
    from .whatsapp import CUSTOMER_TEMPLATES, clean_template_param, load_templates

    if template not in UTILITY_TEMPLATES or template in CUSTOMER_TEMPLATES:
        raise ValueError("template_nao_permitido")
    expected = load_templates().get(template, {}).get("params") or []
    if len(parameters) != len(expected):
        raise ValueError("parametros_do_template")
    return client.send_message(
        to,
        {
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": language},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": clean_template_param(value)}
                            for value in parameters
                        ],
                    }
                ],
            },
        },
    )


def marketing_allowed(opt_in: dict[str, Any] | None) -> bool:
    """Marketing só para quem deu opt-in registrado (com data e origem)."""
    return bool(
        opt_in
        and opt_in.get("granted_at")
        and opt_in.get("source")
        and not opt_in.get("revoked_at")
    )


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
CREATE TABLE IF NOT EXISTS dm_origins (
    conv_key TEXT PRIMARY KEY,
    origin TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dm_products (
    conv_key TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    message_id TEXT,
    at TEXT NOT NULL
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

    _PREFIXES: ClassVar[dict[str, str]] = {"facebook": "fb_"}

    def conversation_id(self, channel: str, contact: str) -> str:
        digest = hmac.new(self._salt, f"{channel}:{contact}".encode(), hashlib.sha256)
        return self._PREFIXES.get(channel, "ig_") + digest.hexdigest()[:32]

    def receive(
        self,
        channel: str,
        contact: str,
        message_id: str | None,
        text: str | None,
        sent_at: datetime,
        origin: str | None = None,
        product_id: str | None = None,
    ) -> tuple[str, bool]:
        """Guarda a mensagem. Sem ``message_id`` (referral sem texto), guarda só a
        origem e a peça, e a janela de 24 h não abre."""
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
            inserted = 0
            if message_id:
                inserted = db.execute(
                    self._sql(
                        "INSERT INTO dm_messages (message_id, conv_key, direction, "
                        "body, sent_at, status) VALUES (?, ?, 'IN', ?, ?, 'NEW') "
                        "ON CONFLICT (message_id) DO NOTHING",
                        mark,
                    ),
                    [message_id, key, text, sent_at.isoformat()],
                ).rowcount
            if product_id:
                # A peça mais recente vale e fica presa à mensagem que a trouxe
                # (ou, num referral sem texto, à próxima mensagem).
                db.execute(
                    self._sql(
                        "INSERT INTO dm_products (conv_key, product_id, message_id, "
                        "at) VALUES (?, ?, ?, ?) ON CONFLICT (conv_key) DO UPDATE "
                        "SET product_id = excluded.product_id, "
                        "message_id = excluded.message_id, at = excluded.at",
                        mark,
                    ),
                    [key, product_id[:80], message_id or None, now],
                )
            elif inserted:
                db.execute(
                    self._sql(
                        "UPDATE dm_products SET message_id = ? "
                        "WHERE conv_key = ? AND message_id IS NULL",
                        mark,
                    ),
                    [message_id, key],
                )
            if origin:
                # A primeira origem vale (a conversa que começou num Story).
                db.execute(
                    self._sql(
                        "INSERT INTO dm_origins (conv_key, origin, at) "
                        "VALUES (?, ?, ?) ON CONFLICT (conv_key) DO NOTHING",
                        mark,
                    ),
                    [key, origin[:40], now],
                )
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

    def origin(self, channel: str, conversation_id: str) -> str | None:
        with self._connect() as (db, mark):
            row = db.execute(
                self._sql("SELECT origin FROM dm_origins WHERE conv_key = ?", mark),
                [f"{channel}:{conversation_id}"],
            ).fetchone()
        return row["origin"] if row else None

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
                    "SELECT m.message_id, m.conv_key, m.body, p.product_id "
                    "FROM dm_messages m LEFT JOIN dm_products p "
                    "ON p.conv_key = m.conv_key AND p.message_id = m.message_id "
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

    def redact(self, message_id: str) -> None:
        with self._connect() as (db, mark):
            db.execute(
                self._sql("UPDATE dm_messages SET body = ? WHERE message_id = ?", mark),
                [REDACTED_BODY, message_id],
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
            # Resposta a um Story: o kit de perfil leva essa conversa ao WhatsApp.
            story = ((message.get("reply_to") or {}).get("story")) or None
            messages.append(
                {
                    "message_id": str(message["mid"])[:200],
                    "contact": sender[:64],
                    "text": str(message.get("text") or "")[:2000] or None,
                    "sent_at": sent_at,
                    "origin": "stories" if story else None,
                }
            )
    return messages


def _messenger_referral(*sources: dict[str, Any]) -> tuple[str | None, str | None]:
    """Peça e origem do botão "Mensagem" da loja (``product``) ou do anúncio
    click-to-Messenger (``source: ADS``). Formato a conferir na hora de ligar."""
    referral = next((s["referral"] for s in sources if s.get("referral")), None)
    if not isinstance(referral, dict):
        return None, None
    product = referral.get("product")
    product = product if isinstance(product, dict) else {}
    ads = referral.get("ads_context_data")
    ads = ads if isinstance(ads, dict) else {}
    # retailer_id é o id do feed (meta_catalog), o mesmo id do catálogo.
    product_id = (
        product.get("retailer_id") or product.get("id") or ads.get("product_id")
    )
    if str(referral.get("source") or "").upper() == "ADS" or referral.get("ad_id"):
        origin = "ads"
    else:
        origin = "shop" if product else None
    return (str(product_id)[:80] if product_id else None), origin


def _sent_at(item: dict[str, Any]) -> datetime:
    try:
        return datetime.fromtimestamp(int(item.get("timestamp")) / 1000, timezone.utc)
    except (TypeError, ValueError):
        return _now()


def parse_messenger_messages(
    payload: dict[str, Any], page_id: str = ""
) -> list[dict[str, Any]]:
    """Mensagens à Página (``object: page``); ecos da Página ficam fora.

    ``referral`` (loja ou anúncio) traz a peça (``product_id``) e a origem;
    "Começar" e os ice breakers chegam como ``postback``, com o texto do botão.
    Referral sem mensagem só marca a conversa (``message_id`` vazio).
    """
    if payload.get("object") != "page":
        return []
    messages = []
    for entry in payload.get("entry") or []:
        for item in (entry or {}).get("messaging") or []:
            item = item or {}
            message = item.get("message") or {}
            postback = item.get("postback") or {}
            sender = str((item.get("sender") or {}).get("id") or "")
            if not sender or message.get("is_echo") or sender == page_id:
                continue
            product_id, origin = _messenger_referral(item, message, postback)
            if message.get("mid"):
                message_id, text = str(message["mid"]), message.get("text")
            elif postback:
                seed = f"{sender}:{item.get('timestamp')}:{postback.get('payload')}"
                digest = hashlib.sha256(seed.encode()).hexdigest()[:32]
                message_id = str(postback.get("mid") or f"pb_{digest}")
                text = postback.get("title") or postback.get("payload")
            elif product_id or origin:
                message_id, text = None, None
            else:
                continue
            messages.append(
                {
                    "message_id": message_id[:200] if message_id else None,
                    "contact": sender[:64],
                    "text": str(text or "")[:2000] or None,
                    "sent_at": _sent_at(item),
                    "origin": origin,
                    "product_id": product_id,
                }
            )
    return messages


class InstagramDirectAdapter:
    channel = "instagram"
    api_status = "VERIFIED_GRAPH_API"
    send_flag = "INSTAGRAM_MESSAGING_ENABLED"
    reason_prefix = "INSTAGRAM"
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

    @staticmethod
    def _parse(payload: dict[str, Any], own_account_id: str) -> list[dict[str, Any]]:
        return parse_instagram_messages(payload, own_account_id)

    def _endpoint(self) -> str:
        return (
            f"https://graph.instagram.com/{graph_api_version()}/"
            f"{self.ig_user_id}/messages"
        )

    @staticmethod
    def _body(contact: str, text: str, human_tag: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "recipient": {"id": contact},
            "message": {"text": text},
        }
        if human_tag:
            body.update(messaging_type="MESSAGE_TAG", tag="HUMAN_AGENT")
        return body

    def receive(self, payload: dict[str, Any], own_account_id: str = "") -> int:
        count = 0
        for item in self._parse(payload, own_account_id):
            _, inserted = self.inbox.receive(
                self.channel,
                item["contact"],
                item["message_id"],
                item["text"],
                item["sent_at"],
                origin=item.get("origin"),
                product_id=item.get("product_id"),
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
        enabled = env_flag(self.send_flag) if self.enabled is None else self.enabled
        now = _now()
        last = self._last_inbound(conversation_id)
        reasons = []
        if not enabled:
            reasons.append(f"{self.reason_prefix}_MESSAGING_DISABLED")
        if not self.access_token or not self.ig_user_id:
            reasons.append(f"{self.reason_prefix}_CREDENTIALS_MISSING")
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
        response = self.session.post(
            self._endpoint(),
            headers={"Authorization": f"Bearer {self.access_token}"},
            json=self._body(contact["contact"], text, not in_window and human_window),
            timeout=self.timeout,
        )
        if not response.ok:
            return {
                "status": "FAILED",
                "error": f"META_ERROR_{_api_error_code(response)}",
            }
        message_id = str(
            response.json().get("message_id") or f"{self.channel}-{uuid4().hex}"
        )
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

        items = []
        for row in self.inbox.pending(self.channel, limit):
            if not row["body"]:
                continue
            conversation_id = row["conv_key"].split(":", 1)[1]
            medium = self.inbox.origin(self.channel, conversation_id)
            items.append(
                InboundText(
                    channel=self.channel,
                    conversation_id=conversation_id,
                    message_id=row["message_id"],
                    text=row["body"],
                    # Peça do referral (loja ou anúncio), só na mensagem que a trouxe.
                    product_hint=row.get("product_id"),
                    origin={"source": self.channel, "medium": medium} if medium else {},
                )
            )
        return items

    def mark(self, message_id: str, status: str) -> None:
        self.inbox.mark(message_id, status)

    def redact(self, message_id: str) -> None:
        self.inbox.redact(message_id)


class FacebookMessengerAdapter(InstagramDirectAdapter):
    """Messenger da Página: responde só a quem escreveu, na janela de 24 h.

    Venda direta, como no Instagram: link do Mercado Pago e condições do site. A
    Página é ``META_PAGE_ID`` e o token é ``META_PAGE_ACCESS_TOKEN``.
    """

    channel = "facebook"
    send_flag = "FACEBOOK_MESSAGING_ENABLED"
    reason_prefix = "FACEBOOK"
    limits: ClassVar[dict[str, Any]] = {
        "max_text": 2000,
        "window_hours": 24,
        "human_agent_days": 7,
    }

    def __init__(
        self,
        inbox: DirectInbox,
        *,
        access_token: str | None,
        page_id: str | None,
        session: Any = None,
        enabled: bool | None = None,
        timeout: int = 20,
    ) -> None:
        super().__init__(
            inbox,
            access_token=access_token,
            ig_user_id=page_id,
            session=session,
            enabled=enabled,
            timeout=timeout,
        )

    @staticmethod
    def _parse(payload: dict[str, Any], own_account_id: str) -> list[dict[str, Any]]:
        return parse_messenger_messages(payload, own_account_id)

    def _endpoint(self) -> str:
        return (
            f"https://graph.facebook.com/{graph_api_version()}/"
            f"{self.ig_user_id}/messages"
        )

    @staticmethod
    def _body(contact: str, text: str, human_tag: bool) -> dict[str, Any]:
        body = InstagramDirectAdapter._body(contact, text, human_tag)
        body.setdefault("messaging_type", "RESPONSE")
        return body


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
        token_refresher: Callable[[], Any] | None = None,
    ) -> None:
        self.token_provider = token_provider
        self.seller_id = seller_id
        self.session = session or requests.Session()
        self.enabled = enabled
        self.timeout = timeout
        self.token_refresher = token_refresher

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token_provider()}"}

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        """GET de leitura; no 401, renova o token uma vez e repete."""
        for attempt in range(2):
            response = self.session.get(
                f"{ML_API}{path}",
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )
            if response.status_code != 401 or attempt or not self.token_refresher:
                return response
            try:
                self.token_refresher()
            except Exception as exc:  # noqa: BLE001 - sem renovação, vale o 401
                logger.warning(
                    "mercado_livre_refresh_failed error_type=%s", type(exc).__name__
                )
                return response
        return response

    def item_sku(self, item_id: str) -> str | None:
        """SELLER_SKU do anúncio (``GET /items/{id}``, só leitura)."""
        if self.token_provider is None or not re.fullmatch(
            r"[A-Z]{3}\d{1,15}", item_id
        ):
            return None
        response = self._get(f"/items/{item_id}", {"include_attributes": "all"})
        if not response.ok:
            logger.warning("mercado_livre_item_failed status=%s", response.status_code)
            return None
        data = response.json() or {}
        for attribute in data.get("attributes") or []:
            if attribute.get("id") == "SELLER_SKU" and attribute.get("value_name"):
                return str(attribute["value_name"])[:80]
        return str(data.get("seller_custom_field") or "")[:80] or None

    def list_unanswered_questions(self, limit: int = 50) -> list[dict[str, Any]]:
        if self.token_provider is None:
            return []
        response = self._get(
            "/my/received_questions/search",
            {"status": "UNANSWERED", "limit": limit, "api_version": 4},
        )
        if not response.ok:
            # Sem log, as perguntas paravam de chegar sem ninguém saber.
            logger.error(
                "mercado_livre_questions_failed status=%s", response.status_code
            )
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

    Chave própria (``FOUNDER_ALERT_WHATSAPP_ENABLED``), independente do envio a
    clientes: em SHADOW o fundador também recebe. O número comercial da Veratus
    não manda mensagem para si mesmo. Sem chave ou sem número, tenta o e-mail
    (``FOUNDER_ALERT_EMAIL`` e ``SMTP_*``); sem nada configurado, o alerta fica
    registrado em ``/os/conversations/alerts``.
    """

    def __init__(
        self,
        *,
        whatsapp_client: Any = None,
        founder_number: str | None = None,
        whatsapp_enabled: bool | None = None,
        email_sender: Callable[[str, str], None] | None = None,
    ) -> None:
        from .harvey import WHATSAPP_NUMBER

        self.client = whatsapp_client
        number = re.sub(r"\D", "", founder_number or "")
        self.number = "" if number == WHATSAPP_NUMBER else number
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
            env_flag("FOUNDER_ALERT_WHATSAPP_ENABLED")
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
