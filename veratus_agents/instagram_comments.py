"""Comentário com palavra-chave → resposta privada no Instagram.

Regra da Meta: uma única mensagem por comentário, em até 7 dias. A resposta é
endereçada ao ``comment_id``; por isso esta fila não guarda o ID nem o nome de
quem comentou. O link leva UTMs para a landing, que as transforma na
referência de visita enviada ao WhatsApp.

Triagem (``SOCIAL_COMMENTS_MODE``, nasce ``SHADOW``): todo comentário passa pelo
classificador do motor de conversas. Pergunta de preço ou disponibilidade ganha
rascunho de resposta pública e, em ``SUPERVISED``, a resposta privada a quem
comentou; reclamação vira tarefa no painel; spam ou dado pessoal vira rascunho
de ocultar. Resposta pública e ocultar só saem com a aprovação do fundador. O
texto do comentário nunca é guardado.
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
COMMENT_MODES = ("SHADOW", "SUPERVISED")
# Intenções do classificador que são pergunta sobre a peça ou a compra.
QUESTION_INTENTS = frozenset(
    {
        "preco",
        "disponibilidade",
        "frete",
        "prazo",
        "como_pedir",
        "catalogo",
        "produto",
        "link_pagamento",
        "fechamento",
        "fato_pendente",
    }
)
PUBLIC_REPLIES = {
    "instagram": "Oi! Te chamei no Direct com os detalhes.",
    "facebook": "Oi! Chama a gente no Messenger da Página que passamos os detalhes.",
}
_SPAM = re.compile(
    r"https?://|\bwww\.|\bbit\.ly\b|\bseguidores\b|\bganhe\b|\bsigam?\s+(?:o|a|meu|minha)\b|"
    r"\bconfira\s+(?:o\s+)?meu\s+perfil\b|\bpromo[çc][ãa]o\s+no\s+(?:meu\s+)?perfil\b",
    re.IGNORECASE,
)


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


def parse_facebook_comments(
    payload: dict[str, Any], page_id: str = ""
) -> list[CommentEvent]:
    """Comentário novo na Página (campo ``feed``); os da própria Página ficam fora."""
    events: list[CommentEvent] = []
    if payload.get("object") != "page":
        return events
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            value = (change or {}).get("value") or {}
            if (change or {}).get("field") != "feed" or value.get("item") != "comment":
                continue
            from_id = str((value.get("from") or {}).get("id") or "")
            comment_id = str(value.get("comment_id") or "")
            if value.get("verb") != "add" or not comment_id or from_id == page_id:
                continue
            events.append(
                CommentEvent(
                    comment_id=comment_id[:100],
                    media_id=str(value.get("post_id") or "")[:100],
                    text=str(value.get("message") or "")[:2200],
                    from_id=from_id,
                )
            )
    return events


def comments_mode() -> str:
    mode = os.getenv("SOCIAL_COMMENTS_MODE", "SHADOW").strip().upper()
    return mode if mode in COMMENT_MODES else "SHADOW"


# Resposta privada a quem perguntou no post (endereçada ao comment_id).
QUESTION_RULE = ReplyRule(
    rule_id="pergunta-post",
    keywords=frozenset(),
    message=(
        "Oi! Vi sua pergunta no post e trouxe a conversa para cá. "
        "Os detalhes e o atendimento estão neste link: {link}"
    ),
    utm_campaign="pergunta_post",
)


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


def load_keyword_replies(path: Path = RULES_PATH) -> dict[str, dict[str, str]]:
    """Textos de resposta por palavra-chave de post (QUERO, CATÁLOGO, ...)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data.get("keyword_replies") or {})


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
    def from_env(cls, token: str | None = None) -> InstagramClient | None:
        """``token`` is the renewed one from the token store, when available."""
        token = (token or os.getenv("INSTAGRAM_ACCESS_TOKEN", "")).strip()
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

    # Moderação (instagram_manage_comments): a conferir na hora de ligar.
    def reply_public(self, comment_id: str, text: str) -> str:
        return _graph_post(
            self.session,
            f"https://graph.instagram.com/{graph_api_version()}/{comment_id}/replies",
            {"message": text},
            {"Authorization": f"Bearer {self.access_token}"},
            self.timeout,
        )

    def hide(self, comment_id: str) -> str:
        return _graph_post(
            self.session,
            f"https://graph.instagram.com/{graph_api_version()}/{comment_id}",
            {"hide": "true"},
            {"Authorization": f"Bearer {self.access_token}"},
            self.timeout,
        )


class FacebookCommentClient:
    """Comentário na Página: responder em público e ocultar (pages_manage_engagement).

    Campos a conferir na hora de ligar.
    """

    def __init__(
        self, access_token: str, *, session: Any = None, timeout: int = 20
    ) -> None:
        self.access_token = access_token
        self.session = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> FacebookCommentClient | None:
        token = os.getenv("META_PAGE_ACCESS_TOKEN", "").strip()
        return cls(token) if token else None

    def reply_public(self, comment_id: str, text: str) -> str:
        return _graph_post(
            self.session,
            f"https://graph.facebook.com/{graph_api_version()}/{comment_id}/comments",
            {"message": text, "access_token": self.access_token},
            {},
            self.timeout,
        )

    def hide(self, comment_id: str) -> str:
        return _graph_post(
            self.session,
            f"https://graph.facebook.com/{graph_api_version()}/{comment_id}",
            {"is_hidden": "true", "access_token": self.access_token},
            {},
            self.timeout,
        )


def _graph_post(
    session: Any, url: str, data: dict[str, str], headers: dict[str, str], timeout: int
) -> str:
    response = session.post(url, data=data, headers=headers, timeout=timeout)
    if not response.ok:
        try:
            code = str(response.json().get("error", {}).get("code", "unknown"))
        except ValueError:
            code = "unknown"
        raise InstagramApiError(f"META_ERROR_{code}", response.status_code)
    try:
        return str(response.json().get("id") or response.json().get("success") or "")
    except ValueError as exc:
        raise InstagramApiError("UNEXPECTED_RESPONSE", response.status_code) from exc


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
CREATE TABLE IF NOT EXISTS social_comment_actions (
    comment_id TEXT NOT NULL,
    action TEXT NOT NULL,
    platform TEXT NOT NULL,
    media_id TEXT NOT NULL,
    category TEXT NOT NULL,
    draft TEXT NOT NULL,
    status TEXT NOT NULL,
    received_at TEXT NOT NULL,
    processed_at TEXT,
    error_code TEXT,
    PRIMARY KEY (comment_id, action)
);
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
        media_rules: dict[str, list[ReplyRule]] | None = None,
    ) -> dict[str, int]:
        """Queue matching comments once; other comments are not stored at all.

        ``media_rules`` are the keywords a published post asked for; they apply
        only to comments on that post and win over the global rules.
        """
        counts = {"queued": 0, "ignored": 0, "duplicates": 0}
        now = _now().isoformat()
        with self._connect() as (db, mark):
            for event in parse_comments(payload):
                specific = (media_rules or {}).get(event.media_id, [])
                rule = match_rule(event.text, [*specific, *rules])
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
        from .policy import hard_review_customer_reply

        now = now or _now()
        by_id = {QUESTION_RULE.rule_id: QUESTION_RULE}
        by_id.update({rule.rule_id: rule for rule in rules})
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
            if hard_review_customer_reply(text):  # o texto passa no gate antes de sair
                self._finish(row["comment_id"], "FAILED", now, "GATE_BLOCKED")
                result["failed"].append(row["comment_id"])
                continue
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

    def triage(
        self,
        events: list[CommentEvent],
        rules: list[ReplyRule],
        *,
        platform: str = "instagram",
        own_account_id: str = "",
        media_rules: dict[str, list[ReplyRule]] | None = None,
        mode: str | None = None,
    ) -> dict[str, int]:
        """Triagem de todo comentário; guarda só a ação, nunca o texto dele.

        Palavra-chave segue com a resposta privada de ``enqueue``. Resposta
        pública e ocultar ficam em ``DRAFT`` até o fundador aprovar.
        """
        from .chatwoot import redact
        from .conversations import classify

        mode = mode or comments_mode()
        counts = {"drafts": 0, "tasks": 0, "private_replies": 0}
        now = _now().isoformat()
        for event in events:
            if own_account_id and event.from_id == own_account_id:
                continue
            text = event.text or ""
            actions: list[tuple[str, str, str, str]] = []
            if _SPAM.search(text):
                self._add_action(event, platform, "HIDE", "SPAM", "", "DRAFT", now)
                counts["drafts"] += 1
                continue
            if redact(text) != text.strip():  # telefone, e-mail, CPF ou endereço
                actions.append(("HIDE", "DADO_PESSOAL", "", "DRAFT"))
            specific = (media_rules or {}).get(event.media_id, [])
            if match_rule(text, [*specific, *rules]) is None:
                found = classify(text, {})
                if (
                    found.trigger
                    or found.task == "RECLAMACAO"
                    or found.intent == "insatisfacao"
                ):
                    category = (
                        "ATENDENTE"
                        if found.trigger == "PEDIDO_DE_ATENDENTE"
                        else "RECLAMACAO"
                    )
                    summary = (
                        f"{category}: comentário {event.comment_id} no post "
                        f"{event.media_id or 'sem_post'} ({platform}). Responder pela "
                        "conta; o texto fica só na rede."
                    )
                    actions.append(("TASK", category, summary, "OPEN"))
                elif found.intent in QUESTION_INTENTS:
                    actions.append(
                        ("PUBLIC_REPLY", "PERGUNTA", PUBLIC_REPLIES[platform], "DRAFT")
                    )
                    if platform == "instagram" and mode == "SUPERVISED":
                        counts["private_replies"] += self._queue_question(event, now)
                    elif platform == "instagram":
                        actions.append(
                            (
                                "PRIVATE_REPLY",
                                "PERGUNTA",
                                QUESTION_RULE.message,
                                "SHADOW",
                            )
                        )
            for action, category, draft, status in actions:
                if self._add_action(
                    event, platform, action, category, draft, status, now
                ):
                    counts["tasks" if action == "TASK" else "drafts"] += 1
        return counts

    def _queue_question(self, event: CommentEvent, now: str) -> int:
        with self._connect() as (db, mark):
            return db.execute(
                self._sql(
                    "INSERT INTO ig_comment_replies (comment_id, media_id, rule_id, "
                    "status, attempts, received_at) VALUES (?, ?, ?, 'PENDING', 0, ?) "
                    "ON CONFLICT (comment_id) DO NOTHING",
                    mark,
                ),
                [event.comment_id, event.media_id, QUESTION_RULE.rule_id, now],
            ).rowcount

    def _add_action(
        self,
        event: CommentEvent,
        platform: str,
        action: str,
        category: str,
        draft: str,
        status: str,
        now: str,
    ) -> int:
        with self._connect() as (db, mark):
            return db.execute(
                self._sql(
                    "INSERT INTO social_comment_actions (comment_id, action, platform, "
                    "media_id, category, draft, status, received_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    mark,
                ),
                [
                    event.comment_id,
                    action,
                    platform,
                    event.media_id,
                    category,
                    draft,
                    status,
                    now,
                ],
            ).rowcount

    def list_actions(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        where, params = ("WHERE status = ? ", [status]) if status else ("", [])
        params.append(max(1, min(limit, 500)))
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql(
                    f"SELECT * FROM social_comment_actions {where}"
                    "ORDER BY received_at DESC LIMIT ?",
                    mark,
                ),
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def resolve_action(
        self,
        comment_id: str,
        action: str,
        *,
        approve: bool,
        client: Any,
        mode: str | None = None,
        text: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """O fundador aprova (ou rejeita) a resposta pública ou o ocultar."""
        from .policy import hard_review_customer_reply

        now = now or _now()
        with self._connect() as (db, mark):
            found = db.execute(
                self._sql(
                    "SELECT * FROM social_comment_actions WHERE comment_id = ? "
                    "AND action = ? AND status = 'DRAFT'",
                    mark,
                ),
                [comment_id, action],
            ).fetchone()
        if action not in ("PUBLIC_REPLY", "HIDE") or found is None:
            raise KeyError(f"{comment_id}:{action}")
        row = dict(found)
        if not approve:
            self._set_action(comment_id, action, "REJECTED", now)
            return {"status": "REJECTED"}
        draft = (text or row["draft"]).strip()[:1000]
        if action == "PUBLIC_REPLY":
            issues = hard_review_customer_reply(draft)
            if issues or not draft:
                return {"status": "BLOCKED", "reasons": issues or ["EMPTY_REPLY"]}
            if row["platform"] == "instagram" and not self._reply_sent(comment_id):
                # "Te chamei no Direct" só depois que a resposta privada saiu.
                return {"status": "BLOCKED", "reasons": ["PRIVATE_REPLY_NOT_SENT"]}
        if (mode or comments_mode()) != "SUPERVISED":
            return {"status": "SHADOW", "would": {"action": action, "text": draft}}
        if client is None:
            return {"status": "BLOCKED", "reasons": ["CREDENTIALS_MISSING"]}
        if not self._set_action(comment_id, action, "SENDING", now, only_from="DRAFT"):
            return {"status": "BUSY"}
        try:
            if action == "PUBLIC_REPLY":
                client.reply_public(comment_id, draft)
            else:
                client.hide(comment_id)
        except InstagramApiError as exc:
            self._set_action(comment_id, action, "FAILED", now, error_code=exc.code)
            return {"status": "FAILED", "error": exc.code}
        self._set_action(comment_id, action, "DONE", now)
        return {"status": "DONE", "external_write": True}

    def _reply_sent(self, comment_id: str) -> bool:
        with self._connect() as (db, mark):
            return (
                db.execute(
                    self._sql(
                        "SELECT 1 FROM ig_comment_replies WHERE comment_id = ? "
                        "AND status = 'SENT'",
                        mark,
                    ),
                    [comment_id],
                ).fetchone()
                is not None
            )

    def _set_action(
        self,
        comment_id: str,
        action: str,
        status: str,
        now: datetime,
        *,
        only_from: str | None = None,
        error_code: str | None = None,
    ) -> bool:
        guard, params = (" AND status = ?", [only_from]) if only_from else ("", [])
        with self._connect() as (db, mark):
            return bool(
                db.execute(
                    self._sql(
                        "UPDATE social_comment_actions SET status = ?, processed_at = ?, "
                        "error_code = ? WHERE comment_id = ? AND action = ?" + guard,
                        mark,
                    ),
                    [status, now.isoformat(), error_code, comment_id, action, *params],
                ).rowcount
            )

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
