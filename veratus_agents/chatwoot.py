"""Chatwoot: a caixa única do fundador, espelho das conversas do Harvey.

Toda conversa de WhatsApp, Instagram Direct, Messenger e perguntas do Mercado
Livre aparece numa só caixa "API channel" do Chatwoot (chatwoot/chatwoot, MIT),
com a etiqueta do canal. O Harvey segue respondendo e a conversa fica
``pending`` (a fila do robô). Na chamada ao fundador ela abre, ganha a etiqueta
``fundador`` e uma nota privada com o motivo. O que o fundador escreve no
Chatwoot volta pelo webhook e sai pelo adaptador do canal como texto de pessoa
(a janela de 24 h fica no adaptador); resolver a conversa devolve ao BOT.

Para o Chatwoot só vai texto sem telefone, e-mail, CPF, CNPJ, CEP e endereço;
os dados do pedido ficam cifrados no Veratus. O contato é "Cliente · <Canal>",
com um identificador que não leva ao cliente. Desligado por padrão
(``CHATWOOT_SYNC_ENABLED``): desligado, nenhuma chamada sai. Erro de rede vira
``FAILED`` no retorno e no log (sem conteúdo), nunca exceção.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .conversations import (
    _CEP,
    _CPF_CNPJ,
    _HANDOFF_SUMMARIES,
    TASK_REASONS,
    task_summary,
)
from .meta_webhooks import env_flag
from .sqlstore import SqlStore
from .workflow import _minimize_contact_data

LOGGER = logging.getLogger("veratus.chatwoot")
TIMEOUT_SECONDS = 10
ORDER_DATA_NOTE = "[dados do pedido recebidos e guardados cifrados no Veratus]"
EMPTY_NOTE = "[mensagem sem texto]"
FOUNDER_LABEL = "fundador"
CHANNEL_NAMES = {
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "facebook": "Messenger",
    "mercado_livre": "Mercado Livre",
}
# Mensagem nossa ainda sem id (POST em curso ou caído no meio): o eco do webhook
# é reconhecido pelo conteúdo durante este tempo.
PENDING_ECHO = timedelta(minutes=10)
SIGNATURE_TOLERANCE_SECONDS = 300
# Logradouro com número na mesma linha: some até o fim da linha.
_ADDRESS = re.compile(
    r"\b(?:rua|r\.|avenida|av\.?|travessa|alameda|estrada|rodovia|pra[cç]a|largo"
    r"|apto\.?|apartamento|bloco|condom[ií]nio)\s[^\n]*?\d[^\n]*",
    re.IGNORECASE,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS cw_conversations (
    conv_key TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    contact_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    cw_conversation_id TEXT UNIQUE,
    cw_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cw_posted (
    post_id TEXT PRIMARY KEY,
    cw_conversation_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    message_id TEXT UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cw_received (
    message_id TEXT PRIMARY KEY,
    conv_key TEXT NOT NULL,
    received_at TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def redact(text: str) -> str:
    """Texto para o Chatwoot: sem telefone, e-mail, CPF, CNPJ, CEP e endereço."""
    clean = _CPF_CNPJ.sub("[documento omitido]", text or "")
    clean = _CEP.sub("[CEP omitido]", clean)
    clean = _ADDRESS.sub("[endereço omitido]", clean)
    return _minimize_contact_data(clean).strip()


def _identifier(conv_key: str) -> str:
    """Id do contato no Chatwoot: derivado da conversa, sem volta ao cliente."""
    return "veratus-" + hashlib.sha256(conv_key.encode("utf-8")).hexdigest()[:24]


def _fingerprint(cw_conversation_id: str, content: str) -> str:
    digest = hashlib.sha256(f"{cw_conversation_id}\n{content}".encode())
    return digest.hexdigest()


def _label(channel: str) -> str:
    return channel.replace("_", "-")


def _reason_text(reason: str) -> str:
    """Motivo da chamada: o resumo do motor ou o texto dado, já sem dado pessoal."""
    if reason in _HANDOFF_SUMMARIES:
        return _HANDOFF_SUMMARIES[reason]
    if reason in TASK_REASONS:
        return task_summary(reason)
    return redact(reason) or "Conversa precisa de atenção."


def verify_webhook_token(request_token: str | None) -> bool:
    """Token do webhook (na URL cadastrada no Chatwoot); sem segredo, sempre False."""
    expected = os.getenv("CHATWOOT_WEBHOOK_TOKEN", "").strip()
    if not expected or not request_token:
        return False
    return hmac.compare_digest(expected.encode(), request_token.strip().encode())


def verify_webhook_signature(
    body: bytes,
    timestamp: str | None,
    signature: str | None,
    *,
    now: float | None = None,
) -> bool:
    """Assinatura das versões novas: ``X-Chatwoot-Signature`` = HMAC("ts.corpo")."""
    secret = os.getenv("CHATWOOT_WEBHOOK_SECRET", "").strip()
    if not secret or not signature or not (timestamp or "").isdigit():
        return False
    clock = time.time() if now is None else now
    if abs(clock - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    signed = timestamp.encode() + b"." + (body or b"")
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}".encode(), signature.encode())


class ChatwootError(RuntimeError):
    """Resposta de erro da API: guarda só o código HTTP, nunca o corpo."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"CHATWOOT_HTTP_{status_code}")
        self.status_code = status_code


class ChatwootClient:
    """As cinco chamadas da API de aplicação, com o cabeçalho ``api_access_token``."""

    def __init__(
        self,
        base_url: str,
        account_id: int | str,
        api_token: str,
        *,
        session: Any = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self.base = f"{base_url.rstrip('/')}/api/v1/accounts/{account_id}"
        self._token = api_token
        self.session = session
        self.timeout = timeout

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.session is None:
            self.session = requests.Session()
        response = self.session.post(
            self.base + path,
            json=body,
            headers={"api_access_token": self._token},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise ChatwootError(response.status_code)
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def create_contact(
        self, inbox_id: int, name: str, identifier: str
    ) -> dict[str, str]:
        """Contato na caixa: {contact_id, source_id} (a forma varia por versão)."""
        body = self._post(
            "/contacts",
            {"inbox_id": inbox_id, "name": name, "identifier": identifier},
        )
        payload = body.get("payload", body)
        contact = payload.get("contact", payload)
        inboxes = [payload.get("contact_inbox") or {}]
        inboxes += contact.get("contact_inboxes") or []
        source_id = next((i["source_id"] for i in inboxes if i.get("source_id")), None)
        if not source_id:
            raise ValueError("chatwoot_source_id_missing")
        return {"contact_id": str(contact["id"]), "source_id": str(source_id)}

    def create_conversation(
        self,
        source_id: str,
        inbox_id: int,
        contact_id: str,
        *,
        custom_attributes: dict[str, Any] | None = None,
        status: str = "pending",
    ) -> str:
        body = self._post(
            "/conversations",
            {
                "source_id": source_id,
                "inbox_id": inbox_id,
                "contact_id": int(contact_id) if contact_id.isdigit() else contact_id,
                "status": status,
                "custom_attributes": custom_attributes or {},
            },
        )
        return str(body["id"])

    def create_message(
        self,
        conversation_id: str,
        content: str,
        *,
        message_type: str,
        private: bool = False,
    ) -> str:
        body = self._post(
            f"/conversations/{conversation_id}/messages",
            {
                "content": content,
                "message_type": message_type,
                "private": private,
                "content_attributes": {"veratus_mirror": True},
            },
        )
        return str(body["id"])

    def toggle_status(self, conversation_id: str, status: str) -> None:
        self._post(
            f"/conversations/{conversation_id}/toggle_status", {"status": status}
        )

    def set_labels(self, conversation_id: str, labels: list[str]) -> None:
        """Substitui as etiquetas da conversa (a API troca a lista inteira)."""
        self._post(f"/conversations/{conversation_id}/labels", {"labels": labels})


class ChatwootStore(SqlStore):
    """Mapa conversa Veratus e conversa Chatwoot, e as mensagens que postamos."""

    SCHEMA = SCHEMA

    def __init__(self, store_path_or_url: str | Path) -> None:
        location = str(store_path_or_url)
        url = (
            location if location.startswith(("postgresql://", "postgres://")) else None
        )
        super().__init__(location, url)

    def _one(self, query: str, params: list[Any]) -> dict[str, Any] | None:
        with self._connect() as (db, mark):
            row = db.execute(self._sql(query, mark), params).fetchone()
        return dict(row) if row else None

    def _write(self, query: str, params: list[Any]) -> int:
        with self._connect() as (db, mark):
            return db.execute(self._sql(query, mark), params).rowcount

    def get(self, conv_key: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM cw_conversations WHERE conv_key = ?", [conv_key]
        )

    def by_chatwoot(self, cw_conversation_id: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM cw_conversations WHERE cw_conversation_id = ?",
            [cw_conversation_id],
        )

    def add_contact(
        self, conv_key: str, channel: str, contact_id: str, source_id: str
    ) -> None:
        now = _now().isoformat()
        self._write(
            "INSERT INTO cw_conversations (conv_key, channel, contact_id, source_id, "
            "cw_status, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?) "
            "ON CONFLICT (conv_key) DO NOTHING",
            [conv_key, channel, contact_id, source_id, now, now],
        )

    def set_conversation(self, conv_key: str, cw_conversation_id: str | None) -> None:
        self._write(
            "UPDATE cw_conversations SET cw_conversation_id = ?, cw_status = 'pending', "
            "updated_at = ? WHERE conv_key = ?",
            [cw_conversation_id, _now().isoformat(), conv_key],
        )

    def set_status(self, conv_key: str, status: str) -> None:
        self._write(
            "UPDATE cw_conversations SET cw_status = ?, updated_at = ? "
            "WHERE conv_key = ?",
            [status, _now().isoformat(), conv_key],
        )

    def posting(self, cw_conversation_id: str, fingerprint: str) -> str:
        """Anota a mensagem antes do POST: o eco que chegar antes do id é nosso."""
        post_id = uuid.uuid4().hex
        self._write(
            "INSERT INTO cw_posted (post_id, cw_conversation_id, fingerprint, "
            "created_at) VALUES (?, ?, ?, ?)",
            [post_id, cw_conversation_id, fingerprint, _now().isoformat()],
        )
        return post_id

    def posted(self, post_id: str, message_id: str) -> None:
        self._write(
            "UPDATE cw_posted SET message_id = ? WHERE post_id = ?",
            [message_id, post_id],
        )

    def drop(self, post_id: str) -> None:
        self._write("DELETE FROM cw_posted WHERE post_id = ?", [post_id])

    def ours(self, message_id: str, fingerprint: str) -> bool:
        since = (_now() - PENDING_ECHO).isoformat()
        return bool(
            self._one(
                "SELECT post_id FROM cw_posted WHERE message_id = ? OR "
                "(message_id IS NULL AND fingerprint = ? AND created_at >= ?)",
                [message_id, fingerprint, since],
            )
        )

    def claim_received(self, message_id: str, conv_key: str) -> bool:
        """Uma resposta do fundador sai uma vez, mesmo com o webhook repetido."""
        return bool(
            self._write(
                "INSERT INTO cw_received (message_id, conv_key, received_at) "
                "VALUES (?, ?, ?) ON CONFLICT (message_id) DO NOTHING",
                [message_id, conv_key, _now().isoformat()],
            )
        )


def _ignore(reason: str) -> dict[str, Any]:
    return {"action": "IGNORE", "reason": reason}


class ChatwootBridge:
    """Espelha as conversas no Chatwoot e traduz os eventos do webhook."""

    def __init__(
        self,
        store_path_or_url: str | Path,
        client: ChatwootClient | None,
        *,
        inbox_id: int | str | None,
        enabled: bool | None = None,
    ) -> None:
        self.store = ChatwootStore(store_path_or_url)
        self.client = client
        self.inbox_id = int(inbox_id) if str(inbox_id or "").isdigit() else None
        self.enabled = enabled

    @classmethod
    def from_env(
        cls, store_path_or_url: str | Path, *, session: Any = None
    ) -> ChatwootBridge:
        """Configuração do ambiente; faltando algo, o espelho diz NOT_CONFIGURED."""
        base = os.getenv("CHATWOOT_BASE_URL", "").strip()
        account = os.getenv("CHATWOOT_ACCOUNT_ID", "").strip()
        token = os.getenv("CHATWOOT_API_TOKEN", "").strip()
        client = None
        if base.startswith("https://") and account.isdigit() and token:
            client = ChatwootClient(base, account, token, session=session)
        inbox_id = os.getenv("CHATWOOT_INBOX_ID", "").strip() or None
        return cls(store_path_or_url, client, inbox_id=inbox_id)

    def _on(self) -> bool:
        if self.enabled is None:
            return env_flag("CHATWOOT_SYNC_ENABLED")
        return self.enabled

    def _run(self, op: str, action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        if not self._on():
            return {"status": "DISABLED"}
        if self.client is None or self.inbox_id is None:
            return {"status": "NOT_CONFIGURED"}
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - o espelho nunca derruba a conversa
            error = str(exc) if isinstance(exc, ChatwootError) else type(exc).__name__
            LOGGER.warning("chatwoot_failed op=%s error_type=%s", op, error)
            return {"status": "FAILED", "error_type": error}

    def _conversation(self, channel: str, conv_key: str) -> dict[str, Any]:
        """A conversa no Chatwoot, criada na primeira vez (contato, conversa, etiqueta)."""
        row = self.store.get(conv_key)
        if row is None:
            name = CHANNEL_NAMES.get(channel, channel.replace("_", " ").title())
            contact = self.client.create_contact(
                self.inbox_id, f"Cliente · {name}", _identifier(conv_key)
            )
            self.store.add_contact(
                conv_key, channel, contact["contact_id"], contact["source_id"]
            )
            row = self.store.get(conv_key)
        if not row["cw_conversation_id"]:
            cw_id = self.client.create_conversation(
                row["source_id"],
                self.inbox_id,
                row["contact_id"],
                custom_attributes={"veratus_channel": row["channel"]},
            )
            self.store.set_conversation(conv_key, cw_id)
            self.client.set_labels(cw_id, [_label(row["channel"])])
            row = self.store.get(conv_key)
        return row

    def _message(
        self,
        row: dict[str, Any],
        content: str,
        message_type: str,
        *,
        private: bool = False,
    ) -> dict[str, Any]:
        cw_id = row["cw_conversation_id"]
        content = content or EMPTY_NOTE
        post_id = self.store.posting(cw_id, _fingerprint(cw_id, content))
        try:
            message_id = self.client.create_message(
                cw_id, content, message_type=message_type, private=private
            )
        except ChatwootError as exc:
            self.store.drop(post_id)  # a API respondeu: a mensagem não existe
            if exc.status_code == 404:  # apagada no Chatwoot: recria na próxima
                self.store.set_conversation(row["conv_key"], None)
            raise
        self.store.posted(post_id, message_id)
        return {
            "status": "MIRRORED",
            "conversation_id": cw_id,
            "message_id": message_id,
        }

    def mirror_inbound(
        self, channel: str, conv_key: str, text: str, *, intent: str | None = None
    ) -> dict[str, Any]:
        """Mensagem do cliente; os dados do pedido entram só como aviso."""
        content = ORDER_DATA_NOTE if intent == "dados_pedido" else redact(text)

        def action() -> dict[str, Any]:
            row = self._conversation(channel, conv_key)
            result = self._message(row, content, "incoming")
            if row["cw_status"] == "resolved":
                # A mensagem nova reabre a conversa; ela volta à fila do robô.
                self.client.toggle_status(row["cw_conversation_id"], "pending")
                self.store.set_status(conv_key, "pending")
            return result

        return self._run("mirror_inbound", action)

    def mirror_reply(self, conv_key: str, text: str) -> dict[str, Any]:
        """Resposta do Harvey já enviada ao cliente (não a do fundador)."""
        channel = conv_key.split(":", 1)[0]
        return self._run(
            "mirror_reply",
            lambda: self._message(
                self._conversation(channel, conv_key), redact(text), "outgoing"
            ),
        )

    def note(self, conv_key: str, text: str) -> dict[str, Any]:
        """Nota privada para o fundador (rascunho em SHADOW, envio que não saiu)."""
        channel = conv_key.split(":", 1)[0]
        return self._run(
            "note",
            lambda: self._message(
                self._conversation(channel, conv_key),
                redact(text),
                "outgoing",
                private=True,
            ),
        )

    def escalate(self, conv_key: str, reason: str) -> dict[str, Any]:
        """Chamada ao fundador: nota privada, etiqueta ``fundador`` e conversa aberta."""
        channel = conv_key.split(":", 1)[0]
        note = (
            f"Chamada ao fundador. Motivo: {_reason_text(reason)}\n"
            "Responda por aqui; ao resolver, o Harvey volta à conversa."
        )

        def action() -> dict[str, Any]:
            row = self._conversation(channel, conv_key)
            cw_id = row["cw_conversation_id"]
            self._message(row, note, "outgoing", private=True)
            self.client.set_labels(cw_id, [_label(row["channel"]), FOUNDER_LABEL])
            self.client.toggle_status(cw_id, "open")
            self.store.set_status(conv_key, "open")
            return {"status": "ESCALATED", "conversation_id": cw_id}

        return self._run("escalate", action)

    def handle_webhook(self, payload: Any) -> dict[str, Any]:
        """SEND (resposta do fundador ao cliente), BOT (resolvida) ou IGNORE."""
        if not self._on():
            return _ignore("disabled")
        try:
            return self._webhook(payload)
        except Exception as exc:  # noqa: BLE001 - o webhook responde sem cair
            LOGGER.warning(
                "chatwoot_failed op=webhook error_type=%s", type(exc).__name__
            )
            return _ignore("error")

    def _webhook(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return _ignore("invalid")
        event = payload.get("event")
        if event == "conversation_status_changed":
            if payload.get("status") != "resolved":
                return _ignore("status")
            row = self.store.by_chatwoot(str(payload.get("id") or ""))
            if row is None:
                return _ignore("unknown_conversation")
            self.store.set_status(row["conv_key"], "resolved")
            return {"action": "BOT", "conv_key": row["conv_key"]}
        if event != "message_created":
            return _ignore("event")
        if payload.get("message_type") not in ("outgoing", 1):
            return _ignore("not_outgoing")
        if payload.get("private") is not False:
            return _ignore("private")
        if (payload.get("sender") or {}).get("type") != "user":
            return _ignore("not_founder")
        attributes = payload.get("content_attributes") or {}
        if "veratus_mirror" in (
            attributes if isinstance(attributes, dict | str) else {}
        ):
            return _ignore("mirror")
        message_id = str(payload.get("id") or "")
        cw_id = str((payload.get("conversation") or {}).get("id") or "")
        text = (payload.get("content") or "").strip()
        if not message_id or not cw_id:
            return _ignore("invalid")
        if not text:
            return _ignore("no_text")
        if self.store.ours(message_id, _fingerprint(cw_id, text)):
            return _ignore("mirror")
        row = self.store.by_chatwoot(cw_id)
        if row is None:
            return _ignore("unknown_conversation")
        if not self.store.claim_received(message_id, row["conv_key"]):
            return _ignore("duplicate")
        return {
            "action": "SEND",
            "conv_key": row["conv_key"],
            "text": text,
            "message_id": message_id,
        }
