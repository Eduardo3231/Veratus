"""Agendador de redes sociais com aprovação semanal.

Fluxo::

    import (calendário JSON) → PENDING_APPROVAL → aprovação semanal (um
    ApprovalRequest por semana com os content_hash) → APPROVED → run-due
    (cron do Render a cada 5 min) → PUBLISHING → PUBLISHED | PUBLISHED_PARTIAL |
    NEEDS_MANUAL | FAILED

- Item editado depois de aprovado volta para ``PENDING_APPROVAL``; item com
  algum canal já publicado não muda mais (``locked``).
- Legenda e texto alternativo passam pelo gate de afirmações
  (``CAPTION_CLAIM:<motivo>`` deixa o item ``BLOCKED``).
- Cada canal publicado é gravado na hora: a nova tentativa pula o que já saiu.
- Canal manual (Story do Facebook, Story com figurinha, TikTok) vira o evento
  ``CHANNEL_NEEDS_MANUAL``, tarefa no painel com mídia e legenda.
- Só publica mídia do manifesto ``social/midia-aprovada.json`` (sha256 conferido
  no que a URL devolve).
- Vídeo entra ``NEEDS_VIDEO``. Só TikTok fica ``NEEDS_MANUAL``, porque o app
  ainda não passou pela auditoria.
- Relógio sem ``REAL_PHOTO`` entra ``NEEDS_INFO`` (o fundador fornece a foto
  pelo importador). Quando a foto chega, ``refresh`` troca a arte-modelo pela
  foto real e o item vai para a aprovação semanal; nada sai sem ela.
- Linha de iPhone e mídia em quarentena entram ``BLOCKED`` e nunca aparecem
  num plano de publicação.
- Sem ``SOCIAL_PUBLISH_ENABLED=true``, ``run-due`` só devolve a prévia, sem
  chamar a Meta.

CLI: ``python -m veratus_agents.social_scheduler import|validate|add|
approve-media|request-approval|run-due|refresh|insights|report|list``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .meta_webhooks import env_flag, normalize_text
from .sqlstore import SqlStore

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CALENDAR = BASE_DIR / "social" / "calendario-2026-10.json"
# JPEG 1080 × 1350 de cada relógio, gerado pelo importador de fotos reais.
WATCH_PHOTO_DIR = BASE_DIR / "landing" / "social" / "posts" / "watches"
PUBLIC_BASE = "https://veratus.onrender.com"
SAO_PAULO = timezone(timedelta(hours=-3))
FORMATS = ("IMAGE", "CAROUSEL", "STORY", "REEL")
STATUSES = (
    "DRAFT",
    "PENDING_APPROVAL",
    "APPROVED",
    "PUBLISHING",
    "PUBLISHED",
    "PUBLISHED_PARTIAL",
    "FAILED",
    "NEEDS_MANUAL",
    "NEEDS_VIDEO",
    "NEEDS_INFO",
    "BLOCKED",
)
DONE_STATUSES = ("PUBLISHED", "PUBLISHED_PARTIAL")
META_CHANNELS = frozenset({"instagram", "facebook"})
MAX_ATTEMPTS = 3
CAROUSEL_LIMIT = 10
MAX_HASHTAGS = 5
INSIGHT_CHECKPOINTS = (("24h", timedelta(hours=24)), ("7d", timedelta(days=7)))
# O Story some em 24 h e leva as métricas junto: mede antes.
STORY_CHECKPOINTS = (("20h", timedelta(hours=20)),)
STORY_LIFETIME = timedelta(hours=24)
# Manifesto das mídias liberadas pelo fundador (caminho público → sha256).
APPROVED_MEDIA = BASE_DIR / "social" / "midia-aprovada.json"
SLOP_LEXICON = BASE_DIR / "social" / "instagram" / "slop-ptbr.json"
# QA da mídia (largura/altura). Feed de 4:5 a 1,91:1; Story e Reel em 9:16.
ASPECT_RANGES = {
    "IMAGE": (0.8, 1.91),
    "CAROUSEL": (0.8, 1.91),
    "STORY": (0.5625, 0.5625),
    "REEL": (0.5625, 0.5625),
}
MIN_WIDTH = 1080
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = (
    300 * 1024 * 1024
)  # limite do Reel na API: a conferir na hora de ligar
_FORMAT_ALIASES = {
    "image": "IMAGE",
    "imagem": "IMAGE",
    "feed": "IMAGE",
    "post": "IMAGE",
    "carousel": "CAROUSEL",
    "carrossel": "CAROUSEL",
    "story": "STORY",
    "stories": "STORY",
    "reel": "REEL",
    "reels": "REEL",
    "video": "REEL",
}


class CalendarError(ValueError):
    pass


def _first(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if item.get(name) not in (None, "", []):
            return item[name]
    return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [
            part.strip() for part in value.replace(";", ",").split(",") if part.strip()
        ]
    return [str(part).strip() for part in value if str(part).strip()]


def _parse_when(value: Any, where: str) -> datetime:
    text = str(value or "").strip().replace(" ", "T", 1)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CalendarError(f"{where}: data inválida") from exc
    if moment.tzinfo is None:
        moment = moment.replace(
            tzinfo=SAO_PAULO
        )  # o calendário é em horário de Brasília
    return moment.astimezone(timezone.utc)


def media_url(path: str) -> str:
    if path.startswith(("https://", "http://")):
        return path
    clean = path.lstrip("/").removeprefix("landing/")
    if "/" not in clean:
        clean = f"social/posts/2026-10/{clean}"
    return f"{PUBLIC_BASE}/{clean}"


def content_hash(item: dict[str, Any]) -> str:
    material = {
        key: item[key]
        for key in (
            "scheduled_at",
            "channels",
            "format",
            "media",
            "caption",
            "alt_text",
            "keywords",
        )
    }
    if item.get("sticker"):  # só entra quando existe: o hash antigo não muda
        material["sticker"] = item["sticker"]
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def iso_week(moment: str) -> str:
    local = datetime.fromisoformat(moment).astimezone(SAO_PAULO)
    year, week, _ = local.isocalendar()
    return f"{year}-W{week:02d}"


def _catalog() -> dict[str, dict[str, Any]]:
    from .catalog import load_catalog

    return {product["id"]: product for product in load_catalog()}


def _watch_products(
    item: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> list[str]:
    return [
        product_id
        for product_id in item["products"]
        if product_id in catalog
        and (catalog[product_id].get("collection") or "watches") == "watches"
    ]


def watch_photo_url(product_id: str) -> str:
    return f"{PUBLIC_BASE}/social/posts/watches/{product_id}.jpg"


def block_reasons(
    item: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> list[str]:
    """Bloqueios definitivos: linha de iPhone (Fase 7) e mídia em quarentena."""
    from urllib.parse import urlsplit

    from integrations.meta_content_publisher import quarantined_media_paths

    reasons = []
    text = normalize_text(
        " ".join([item["caption"], *item["keywords"], *item["products"], item["id"]])
    )
    if "iphone" in text or item.get("category") == "smartphone":
        reasons.append("LINHA_IPHONE_DEPOIS_DO_TESTE")
    quarantined = quarantined_media_paths()
    if any(urlsplit(url).path in quarantined for url in item["media"]):
        reasons.append("MEDIA_IN_QUARANTINE")
    return reasons


# O que a lista "Never" de social/instagram/voice.md pede e o gate não cobre.
_CAPTION_NEVER = re.compile(
    r"\b(?:produto\s+verificado|garantia\s+de\s+proced[êe]ncia|"
    r"selo\s+de\s+(?:qualidade|autenticidade)|banho)\b",
    re.IGNORECASE,
)
_HASHTAG = re.compile(r"(?<![\w#])#\w+")


def caption_issues(
    item: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> list[str]:
    """Gate de afirmações na legenda e no texto alternativo (vazio = passa)."""
    from .policy import hard_review_customer_reply

    collections: set[str | None] = {
        catalog[product_id].get("collection") or "watches"
        for product_id in item["products"]
        if product_id in catalog
    } or {None}
    text = "\n".join(part for part in (item["caption"], item["alt_text"]) if part)
    issues: list[str] = []
    for collection in sorted(collections, key=str):
        issues += hard_review_customer_reply(text, product_collection=collection)
    if _CAPTION_NEVER.search(text):
        issues.append("selo ou material sem comprovação")
    if len(_HASHTAG.findall(item["caption"])) > MAX_HASHTAGS:
        issues.append(f"mais de {MAX_HASHTAGS} hashtags")
    return [f"CAPTION_CLAIM:{issue}" for issue in dict.fromkeys(issues)]


def _slop_lexicon() -> dict[str, Any]:
    try:
        return json.loads(SLOP_LEXICON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def caption_lint(item: dict[str, Any]) -> list[str]:
    """Avisos de estilo (léxico pt-BR do ig-human e regras do ig-caption).

    Não bloqueiam: vão no pedido de aprovação para o fundador ver.
    """
    caption = item["caption"]
    text = normalize_text(caption)
    lexicon = _slop_lexicon()
    warnings = [
        f"SLOP:{entry['find']}"
        for entry in [*lexicon.get("words", []), *lexicon.get("phrases", [])]
        if re.search(rf"\b{re.escape(normalize_text(entry['find']))}\b", text)
    ]
    if any(char in caption for char in "—–"):
        warnings.append("TRAVESSAO")
    if len(caption.split("\n", 1)[0]) > 125:
        warnings.append("GANCHO_CORTADO_EM_125")
    return warnings


def pending_info(item: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> list[str]:
    """O que o fundador ainda fornece para o relógio ir ao ar (vazio = pronto)."""
    watches = _watch_products(item, catalog)
    if not watches:
        return (
            ["NEEDS_REAL_PHOTO"] if item.get("category") in ("watch", "relogio") else []
        )
    if any(
        catalog[product_id].get("image_status") != "REAL_PHOTO"
        for product_id in watches
    ):
        return ["NEEDS_REAL_PHOTO"]
    if item.get("template_media"):
        missing = [
            product_id
            for product_id in watches
            if not (WATCH_PHOTO_DIR / f"{product_id}.jpg").exists()
        ]
        if missing:
            # A foto entrou antes do importador gerar o JPEG: rodar de novo.
            return [f"WATCH_JPEG_MISSING:{','.join(missing)}"]
    return []


def parse_calendar(data: Any) -> list[dict[str, Any]]:
    """Aceita lista ou {"items"|"itens": [...]} com nomes em português ou inglês."""
    raw_items = data
    if isinstance(data, dict):
        raw_items = _first(data, "items", "itens", "posts")
    if not isinstance(raw_items, list) or not raw_items:
        raise CalendarError("calendário vazio ou fora do formato")
    items, seen = [], set()
    for index, raw in enumerate(raw_items):
        where = f"item {index + 1}"
        if not isinstance(raw, dict):
            raise CalendarError(f"{where}: precisa ser objeto")
        item_id = str(_first(raw, "id", "codigo", "slug") or "").strip()
        if not item_id or item_id in seen:
            raise CalendarError(f"{where}: id ausente ou repetido")
        seen.add(item_id)
        raw_format = normalize_text(str(_first(raw, "format", "formato", "tipo") or ""))
        fmt = _FORMAT_ALIASES.get(raw_format.strip(), raw_format.strip().upper())
        if fmt not in FORMATS:
            raise CalendarError(f"{item_id}: formato {raw_format!r} desconhecido")
        channels = [
            normalize_text(channel)
            for channel in _as_list(_first(raw, "channels", "canais", "canal"))
        ] or ["instagram"]
        media = [
            media_url(path)
            for path in _as_list(_first(raw, "media", "midias", "arquivos", "arquivo"))
        ]
        if fmt == "CAROUSEL" and not 2 <= len(media) <= CAROUSEL_LIMIT:
            raise CalendarError(f"{item_id}: carrossel precisa de 2 a 10 mídias")
        caption = str(_first(raw, "caption", "legenda") or "").strip()
        if len(caption) > 2200:
            raise CalendarError(f"{item_id}: legenda acima de 2.200 caracteres")
        # Quadro com figurinha (enquete, pergunta, link): a API não faz, vai à mão.
        sticker = str(_first(raw, "figurinha", "sticker") or "").strip()[:300]
        if sticker and fmt != "STORY":
            raise CalendarError(f"{item_id}: figurinha só em Story")
        item = {
            "id": item_id,
            "scheduled_at": _parse_when(
                _first(raw, "scheduled_at", "data_hora", "data", "quando"), item_id
            ).isoformat(),
            "channels": sorted(set(channels)),
            "format": fmt,
            "media": media,
            "caption": caption,
            "alt_text": str(_first(raw, "alt_text", "texto_alternativo") or "").strip(),
            "keywords": [
                word.upper()
                for word in _as_list(_first(raw, "keywords", "palavras_chave"))
            ],
            "products": _as_list(_first(raw, "products", "produtos", "produto")),
            "category": normalize_text(str(_first(raw, "category", "categoria") or "")),
            "sticker": sticker,
            "calendar_status": str(_first(raw, "status") or "").strip().upper(),
            "calendar_block_reason": str(
                _first(raw, "blocked_reason", "motivo_bloqueio", "bloqueio") or ""
            ).strip(),
        }
        items.append(item)
    return items


def plan_item(
    item: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], str, str | None]:
    """Status inicial do item e, para relógio com foto real, a mídia final.

    No calendário, o item de relógio marcado ``BLOCKED`` traz uma arte-modelo
    com espaço para a foto. Ele espera a foto real (``NEEDS_INFO``) e, quando
    ela chega, a arte-modelo é trocada pelo JPEG da foto real.
    """
    watches = _watch_products(item, catalog)
    template = bool(item.get("template_media")) or (
        item.get("calendar_status") == "BLOCKED" and bool(watches)
    )
    item = {**item, "template_media": template}
    hard = block_reasons(item, catalog) + caption_issues(item, catalog)
    calendar_reason = item.get("calendar_block_reason") or "BLOCKED_IN_CALENDAR"
    if hard:
        if item.get("calendar_status") == "BLOCKED":
            hard.insert(0, calendar_reason)
        return item, "BLOCKED", ",".join(dict.fromkeys(hard))
    if item.get("calendar_status") == "BLOCKED" and not watches:
        return item, "BLOCKED", calendar_reason
    pending = pending_info(item, catalog)
    if pending:
        return item, "NEEDS_INFO", ",".join(pending)
    if template:
        media = [watch_photo_url(product_id) for product_id in watches][:CAROUSEL_LIMIT]
        fmt = item["format"]
        if fmt == "CAROUSEL" and len(media) < 2:
            fmt = "IMAGE"
        item = {**item, "media": media, "format": fmt, "template_media": False}
    if item["format"] == "REEL" and not any(
        url.lower().endswith(".mp4") for url in item["media"]
    ):
        return item, "NEEDS_VIDEO", "vídeo ainda não produzido"
    if not (set(item["channels"]) & META_CHANNELS):
        return item, "NEEDS_MANUAL", "TIKTOK_UNVERIFIED"
    if not item["media"] or not item["caption"]:
        return item, "DRAFT", "faltam mídia ou legenda"
    return item, "PENDING_APPROVAL", None


SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_posts (
    id TEXT PRIMARY KEY,
    scheduled_at TEXT NOT NULL,
    week TEXT NOT NULL,
    channels_json TEXT NOT NULL,
    format TEXT NOT NULL,
    media_json TEXT NOT NULL,
    caption TEXT NOT NULL,
    alt_text TEXT NOT NULL,
    keywords_json TEXT NOT NULL,
    products_json TEXT NOT NULL,
    category TEXT NOT NULL,
    template_media INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    block_reason TEXT,
    content_hash TEXT NOT NULL,
    approval_id TEXT,
    published_json TEXT NOT NULL DEFAULT '{}',
    permalink TEXT,
    published_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS social_approvals (
    approval_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (approval_id, item_id)
);
CREATE TABLE IF NOT EXISTS social_publications (
    item_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (item_id, content_hash)
);
CREATE TABLE IF NOT EXISTS social_media_keywords (
    media_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    item_id TEXT NOT NULL,
    PRIMARY KEY (media_id, keyword)
);
CREATE TABLE IF NOT EXISTS social_insights (
    item_id TEXT NOT NULL,
    checkpoint TEXT NOT NULL,
    media_id TEXT NOT NULL,
    reach INTEGER,
    saved INTEGER,
    comments INTEGER,
    shares INTEGER,
    at TEXT NOT NULL,
    PRIMARY KEY (item_id, checkpoint)
);
CREATE TABLE IF NOT EXISTS social_post_extras (
    item_id TEXT PRIMARY KEY,
    sticker TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS social_insight_metrics (
    item_id TEXT NOT NULL,
    checkpoint TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    PRIMARY KEY (item_id, checkpoint)
);
CREATE TABLE IF NOT EXISTS social_account_metrics (
    day TEXT PRIMARY KEY,
    metrics_json TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS social_events (
    event_id TEXT PRIMARY KEY,
    item_id TEXT,
    event TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    at TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SocialScheduleStore(SqlStore):
    SCHEMA = SCHEMA

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        item = dict(row)
        for name in ("channels", "media", "keywords", "products", "published"):
            item[name] = json.loads(item.pop(f"{name}_json"))
        item["template_media"] = bool(item.get("template_media"))
        item["sticker"] = item.get("sticker") or ""
        return item

    def event(self, item_id: str | None, event: str, detail: dict[str, Any]) -> None:
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "INSERT INTO social_events (event_id, item_id, event, detail_json, at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    mark,
                ),
                [
                    f"soc_evt_{uuid4().hex}",
                    item_id,
                    event,
                    json.dumps(detail, sort_keys=True, default=str),
                    _now().isoformat(),
                ],
            )

    def events(self, event: str | None = None) -> list[dict[str, Any]]:
        where, params = ("WHERE event = ?", [event]) if event else ("", [])
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql(f"SELECT * FROM social_events {where} ORDER BY at", mark),
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    _SELECT = (
        "SELECT p.*, e.sticker FROM scheduled_posts p "
        "LEFT JOIN social_post_extras e ON e.item_id = p.id"
    )

    def get(self, item_id: str) -> dict[str, Any] | None:
        with self._connect() as (db, mark):
            row = db.execute(
                self._sql(f"{self._SELECT} WHERE p.id = ?", mark), [item_id]
            ).fetchone()
        return self._decode(row) if row else None

    def items(
        self, *, status: str | None = None, week: str | None = None
    ) -> list[dict[str, Any]]:
        where, params = [], []
        if status:
            where.append("p.status = ?")
            params.append(status)
        if week:
            where.append("p.week = ?")
            params.append(week)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql(
                    f"{self._SELECT} {clause} ORDER BY p.scheduled_at",
                    mark,
                ),
                params,
            ).fetchall()
        return [self._decode(row) for row in rows]

    def upsert(self, item: dict[str, Any], status: str, reason: str | None) -> str:
        """Cria o item ou atualiza o conteúdo; conteúdo novo perde a aprovação."""
        digest = content_hash(item)
        existing = self.get(item["id"])
        now = _now().isoformat()
        values = {
            "scheduled_at": item["scheduled_at"],
            "week": iso_week(item["scheduled_at"]),
            "channels_json": json.dumps(item["channels"]),
            "format": item["format"],
            "media_json": json.dumps(item["media"]),
            "caption": item["caption"],
            "alt_text": item["alt_text"],
            "keywords_json": json.dumps(item["keywords"]),
            "products_json": json.dumps(item["products"]),
            "category": item.get("category") or "",
            "template_media": int(bool(item.get("template_media"))),
            "content_hash": digest,
        }
        with self._connect() as (db, mark):
            if existing is None:
                columns = ["id", *values, "status", "block_reason", "updated_at"]
                db.execute(
                    self._sql(
                        f"INSERT INTO scheduled_posts ({', '.join(columns)}) "
                        f"VALUES ({', '.join('?' for _ in columns)})",
                        mark,
                    ),
                    [item["id"], *values.values(), status, reason, now],
                )
                self._save_extras(db, mark, item)
                return "created"
            if existing["content_hash"] == digest and existing["status"] not in (
                "BLOCKED",
                "NEEDS_VIDEO",
                "NEEDS_INFO",
            ):
                return "unchanged"
            # Canal já publicado (mesmo em falha parcial): o conteúdo não muda mais.
            if (
                existing["status"] in ("PUBLISHING", *DONE_STATUSES)
                or existing["published"]
            ):
                return "locked"
            assignments = ", ".join(f"{name} = ?" for name in values)
            db.execute(
                self._sql(
                    f"UPDATE scheduled_posts SET {assignments}, status = ?, "
                    "block_reason = ?, approval_id = NULL, updated_at = ? WHERE id = ?",
                    mark,
                ),
                [*values.values(), status, reason, now, item["id"]],
            )
            self._save_extras(db, mark, item)
        return "updated"

    def _save_extras(self, db: Any, mark: str, item: dict[str, Any]) -> None:
        db.execute(
            self._sql(
                "INSERT INTO social_post_extras (item_id, sticker) VALUES (?, ?) "
                "ON CONFLICT (item_id) DO UPDATE SET sticker = excluded.sticker",
                mark,
            ),
            [item["id"], item.get("sticker") or ""],
        )

    def set_status(self, item_id: str, status: str, **changes: Any) -> None:
        allowed = {
            "block_reason",
            "approval_id",
            "published_json",
            "permalink",
            "published_at",
            "attempts",
            "next_attempt_at",
            "last_error",
        }
        if set(changes) - allowed or status not in STATUSES:
            raise ValueError("mudança_invalida")
        columns = ", ".join(f"{name} = ?" for name in changes)
        sql = "UPDATE scheduled_posts SET status = ?, updated_at = ?"
        if columns:
            sql += f", {columns}"
        with self._connect() as (db, mark):
            db.execute(
                self._sql(sql + " WHERE id = ?", mark),
                [status, _now().isoformat(), *changes.values(), item_id],
            )

    def claim(self, item_id: str) -> bool:
        with self._connect() as (db, mark):
            return bool(
                db.execute(
                    self._sql(
                        "UPDATE scheduled_posts SET status = 'PUBLISHING', updated_at = ? "
                        "WHERE id = ? AND status = 'APPROVED'",
                        mark,
                    ),
                    [_now().isoformat(), item_id],
                ).rowcount
            )

    def link_approval(self, approval_id: str, items: list[dict[str, Any]]) -> None:
        with self._connect() as (db, mark):
            for item in items:
                db.execute(
                    self._sql(
                        "INSERT INTO social_approvals (approval_id, item_id, content_hash) "
                        "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                        mark,
                    ),
                    [approval_id, item["id"], item["content_hash"]],
                )
                db.execute(
                    self._sql(
                        "UPDATE scheduled_posts SET approval_id = ? WHERE id = ?", mark
                    ),
                    [approval_id, item["id"]],
                )

    def approved_hash(self, approval_id: str, item_id: str) -> str | None:
        with self._connect() as (db, mark):
            row = db.execute(
                self._sql(
                    "SELECT content_hash FROM social_approvals "
                    "WHERE approval_id = ? AND item_id = ?",
                    mark,
                ),
                [approval_id, item_id],
            ).fetchone()
        return row["content_hash"] if row else None

    def publication(self, item_id: str, digest: str) -> dict[str, Any] | None:
        with self._connect() as (db, mark):
            row = db.execute(
                self._sql(
                    "SELECT result_json FROM social_publications "
                    "WHERE item_id = ? AND content_hash = ?",
                    mark,
                ),
                [item_id, digest],
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def record_publication(self, item: dict[str, Any], result: dict[str, Any]) -> None:
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "INSERT INTO social_publications (item_id, content_hash, result_json, "
                    "at) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    mark,
                ),
                [
                    item["id"],
                    item["content_hash"],
                    json.dumps(result),
                    _now().isoformat(),
                ],
            )
            media_id = (result.get("instagram") or {}).get("id")
            for keyword in item["keywords"] if media_id else []:
                db.execute(
                    self._sql(
                        "INSERT INTO social_media_keywords (media_id, keyword, item_id) "
                        "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                        mark,
                    ),
                    [media_id, keyword, item["id"]],
                )

    def media_keywords(self) -> list[dict[str, Any]]:
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql("SELECT * FROM social_media_keywords", mark)
            ).fetchall()
        return [dict(row) for row in rows]

    def save_insight(
        self, item_id: str, checkpoint: str, media_id: str, metrics: dict[str, Any]
    ) -> None:
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "INSERT INTO social_insights (item_id, checkpoint, media_id, reach, "
                    "saved, comments, shares, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT DO NOTHING",
                    mark,
                ),
                [
                    item_id,
                    checkpoint,
                    media_id,
                    metrics.get("reach"),
                    metrics.get("saved"),
                    metrics.get("comments"),
                    metrics.get("shares"),
                    _now().isoformat(),
                ],
            )
            # Todas as métricas do formato (views, replies...) ficam em JSON.
            db.execute(
                self._sql(
                    "INSERT INTO social_insight_metrics (item_id, checkpoint, "
                    "metrics_json) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    mark,
                ),
                [item_id, checkpoint, json.dumps(metrics, sort_keys=True, default=str)],
            )

    def insights(self) -> list[dict[str, Any]]:
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql(
                    "SELECT i.*, m.metrics_json FROM social_insights i "
                    "LEFT JOIN social_insight_metrics m ON m.item_id = i.item_id "
                    "AND m.checkpoint = i.checkpoint ORDER BY i.at",
                    mark,
                )
            ).fetchall()
        result = []
        for row in rows:
            entry = dict(row)
            entry["metrics"] = json.loads(entry.pop("metrics_json") or "{}")
            result.append(entry)
        return result

    def save_account_metrics(self, day: str, metrics: dict[str, Any]) -> bool:
        with self._connect() as (db, mark):
            return bool(
                db.execute(
                    self._sql(
                        "INSERT INTO social_account_metrics (day, metrics_json, at) "
                        "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                        mark,
                    ),
                    [day, json.dumps(metrics, sort_keys=True), _now().isoformat()],
                ).rowcount
            )

    def account_metrics(self) -> list[dict[str, Any]]:
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql("SELECT * FROM social_account_metrics ORDER BY day", mark)
            ).fetchall()
        return [
            {"day": row["day"], **json.loads(row["metrics_json"]), "at": row["at"]}
            for row in rows
        ]


# -- importação, aprovação e bloqueios ------------------------------------------------


def _read_calendar(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CalendarError(
            f"{path.name} não encontrado: o pacote do fundador ainda não chegou"
        ) from exc
    except ValueError as exc:
        raise CalendarError(f"{path.name}: JSON inválido") from exc


def import_calendar(
    store: SocialScheduleStore,
    path: str | Path = DEFAULT_CALENDAR,
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    path = Path(path)
    data = _read_calendar(path)
    catalog = catalog if catalog is not None else _catalog()
    counts: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for item in parse_calendar(data):
        item, status, reason = plan_item(item, catalog)
        outcome = store.upsert(item, status, reason)
        counts[outcome] = counts.get(outcome, 0) + 1
        statuses[status] = statuses.get(status, 0) + 1
    store.event(None, "CALENDAR_IMPORTED", {"file": path.name, **counts})
    return {"items": sum(statuses.values()), "by_status": statuses, **counts}


def validate_calendar(
    path: str | Path,
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
    approved: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Confere o pacote antes do import: status previsto, motivo e avisos, sem gravar."""
    path = Path(path)
    catalog = catalog if catalog is not None else _catalog()
    approved = load_approved_media() if approved is None else approved
    from urllib.parse import urlsplit

    report = []
    for item in parse_calendar(_read_calendar(path)):
        planned, status, reason = plan_item(item, catalog)
        report.append(
            {
                "id": planned["id"],
                "status": status,
                "reason": reason,
                "lint": caption_lint(planned),
                "media_not_approved": [
                    url
                    for url in planned["media"]
                    if approved.get(urlsplit(url).path) is None
                ],
            }
        )
    statuses = Counter(entry["status"] for entry in report)
    return {"file": path.name, "by_status": dict(statuses), "items": report}


def add_items(
    store: SocialScheduleStore,
    data: Any,
    *,
    actor: str,
    catalog: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Rascunho de skill (ig-caption, ig-carousel...) vira item com gate e lint."""
    catalog = catalog if catalog is not None else _catalog()
    if isinstance(data, dict) and not _first(data, "items", "itens", "posts"):
        data = [data]
    added = []
    for item in parse_calendar(data):
        planned, status, reason = plan_item(item, catalog)
        outcome = store.upsert(planned, status, reason)
        store.event(planned["id"], "ITEM_ADDED", {"actor": actor, "outcome": outcome})
        added.append(
            {
                "id": planned["id"],
                "outcome": outcome,
                "status": (store.get(planned["id"]) or {}).get("status"),
                "reason": reason,
                "lint": caption_lint(planned),
            }
        )
    return added


def edit_item(
    store: SocialScheduleStore, item_id: str, *, actor: str, **changes: Any
) -> dict[str, Any]:
    """Editar legenda, mídia, horário ou palavras-chave tira a aprovação."""
    item = store.get(item_id)
    if item is None:
        raise KeyError(item_id)
    editable = {
        "caption",
        "alt_text",
        "media",
        "keywords",
        "scheduled_at",
        "channels",
        "sticker",
    }
    if set(changes) - editable:
        raise ValueError("campo_nao_editavel")
    changes = _normalize_changes(item_id, changes)
    updated = {**item, **changes, "calendar_status": "", "calendar_block_reason": ""}
    if updated.get("sticker") and updated["format"] != "STORY":
        raise ValueError("figurinha_so_em_story")
    if "media" in changes:
        updated["template_media"] = False  # mídia nova vem pronta do fundador
    updated, status, reason = plan_item(updated, _catalog())
    outcome = store.upsert(updated, status, reason)
    store.event(item_id, "ITEM_EDITED", {"actor": actor, "outcome": outcome})
    return store.get(item_id) or {}


def _normalize_changes(item_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Mesmas regras do calendário para o que chega pela rota de edição."""
    clean = dict(changes)
    if "scheduled_at" in clean:
        clean["scheduled_at"] = _parse_when(clean["scheduled_at"], item_id).isoformat()
    if "media" in clean:
        clean["media"] = [media_url(path) for path in _as_list(clean["media"])]
    if "keywords" in clean:
        clean["keywords"] = [word.upper() for word in _as_list(clean["keywords"])]
    if "channels" in clean:
        clean["channels"] = sorted(
            {normalize_text(channel) for channel in _as_list(clean["channels"])}
        ) or ["instagram"]
    for name in ("caption", "alt_text", "sticker"):
        if name in clean:
            clean[name] = str(clean[name] or "").strip()
    if len(clean.get("caption", "")) > 2200:
        raise ValueError("legenda_acima_de_2200")
    return clean


def reject_item(
    store: SocialScheduleStore, item_id: str, *, actor: str, reason: str = ""
) -> dict[str, Any]:
    """Rejeição por item: só ele volta para DRAFT; o resto da semana segue."""
    item = store.get(item_id)
    if item is None:
        raise KeyError(item_id)
    if item["status"] not in ("PENDING_APPROVAL", "APPROVED"):
        raise ValueError("item_nao_rejeitavel")
    store.set_status(
        item_id,
        "DRAFT",
        approval_id=None,
        block_reason=("REJECTED:" + reason.strip())[:500] if reason else "REJECTED",
    )
    store.event(item_id, "ITEM_REJECTED", {"actor": actor})
    return store.get(item_id) or {}


def refresh_pending(
    store: SocialScheduleStore, catalog: dict[str, dict[str, Any]] | None = None
) -> dict[str, list[str]]:
    """Itens de relógio que esperavam a foto real: com a foto, vão à aprovação.

    A arte-modelo vira o JPEG da foto real (``landing/social/posts/watches/``),
    o ``content_hash`` muda e o item entra em ``PENDING_APPROVAL``: o fundador
    aprova na semana antes de qualquer publicação.
    """
    catalog = catalog if catalog is not None else _catalog()
    result: dict[str, list[str]] = {"ready": [], "still_pending": []}
    for item in store.items(status="NEEDS_INFO"):
        planned, status, reason = plan_item(
            {**item, "calendar_status": "", "calendar_block_reason": ""}, catalog
        )
        if status == "NEEDS_INFO":
            if reason != item["block_reason"]:
                store.set_status(item["id"], "NEEDS_INFO", block_reason=reason)
            result["still_pending"].append(item["id"])
            continue
        store.upsert(planned, status, reason)
        store.event(
            item["id"],
            "WATCH_PHOTO_READY",
            {"status": status, "media": planned["media"]},
        )
        result["ready"].append(item["id"])
    return result


def request_weekly_approvals(
    store: SocialScheduleStore, engine: Any, *, week: str | None = None
) -> list[dict[str, Any]]:
    """Um ApprovalRequest por semana com os itens pendentes e seus content_hash."""
    from .operations import ApprovalType, request_approval

    pending = [
        item
        for item in store.items(status="PENDING_APPROVAL", week=week)
        if not item["approval_id"]
    ]
    weeks: dict[str, list[dict[str, Any]]] = {}
    for item in pending:
        weeks.setdefault(item["week"], []).append(item)
    created = []
    for name, items in sorted(weeks.items()):
        approval = request_approval(
            engine,
            "social-media-worker",
            ApprovalType.PUBLISH_APPROVAL,
            f"Calendário social da semana {name} ({len(items)} itens)",
            {
                "week": name,
                # O fundador aprova vendo legenda, mídia e o resultado do gate.
                "items": [
                    {
                        "id": item["id"],
                        "scheduled_at": item["scheduled_at"],
                        "format": item["format"],
                        "channels": item["channels"],
                        "caption": item["caption"],
                        "alt_text": item["alt_text"],
                        "media": item["media"],
                        "keywords": item["keywords"],
                        "sticker": item["sticker"],
                        "gate": "PASSED",
                        "lint": caption_lint(item),
                        "content_hash": item["content_hash"],
                    }
                    for item in items
                ],
                "external_write": False,
            },
        )
        store.link_approval(approval.request_id, items)
        store.event(
            None, "WEEK_APPROVAL_REQUESTED", {"week": name, "id": approval.request_id}
        )
        created.append(
            {"week": name, "approval_id": approval.request_id, "items": len(items)}
        )
    return created


def sync_approvals(
    store: SocialScheduleStore, status_of: Callable[[str], str | None]
) -> dict[str, int]:
    counts = {"approved": 0, "rejected": 0, "stale": 0}
    for item in store.items(status="PENDING_APPROVAL"):
        if not item["approval_id"]:
            continue
        status = status_of(item["approval_id"])
        if status == "APPROVED":
            if (
                store.approved_hash(item["approval_id"], item["id"])
                == item["content_hash"]
            ):
                store.set_status(item["id"], "APPROVED")
                counts["approved"] += 1
            else:
                store.set_status(item["id"], "PENDING_APPROVAL", approval_id=None)
                counts["stale"] += 1
        elif status == "REJECTED":
            store.set_status(item["id"], "DRAFT", approval_id=None)
            counts["rejected"] += 1
    return counts


# -- publicação -----------------------------------------------------------------------


class Publisher(Protocol):
    """Grava em ``item["published"]`` cada canal assim que ele sai."""

    def publish(self, item: dict[str, Any]) -> dict[str, Any]: ...


# (status HTTP, content-type) ou (status, content-type, sha256 do corpo).
MediaChecker = Callable[[str], tuple]


def http_media_check(url: str) -> tuple[int, str, str]:
    import requests

    response = requests.get(url, stream=True, timeout=30)
    try:
        digest, size = hashlib.sha256(), 0
        if response.status_code == 200:
            for chunk in response.iter_content(1 << 16):
                size += len(chunk)
                if size > MAX_VIDEO_BYTES:
                    return response.status_code, "too-large", ""
                digest.update(chunk)
        return (
            response.status_code,
            response.headers.get("content-type", ""),
            digest.hexdigest(),
        )
    finally:
        response.close()


def load_approved_media(path: Path | None = None) -> dict[str, str]:
    """Caminho público → sha256 aprovado (``social/midia-aprovada.json``)."""
    path = path or APPROVED_MEDIA
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        str(entry["path"]): str(entry["sha256"])
        for entry in data.get("midias") or []
        if entry.get("path") and entry.get("sha256")
    }


def _is_video(url: str) -> bool:
    from urllib.parse import urlsplit

    return urlsplit(url).path.lower().endswith(".mp4")


def format_problems(item: dict[str, Any]) -> list[str]:
    videos = [url for url in item["media"] if _is_video(url)]
    fmt = item["format"]
    if fmt == "REEL" and len(videos) != 1:
        return ["REEL_PRECISA_DE_UM_MP4"]
    if fmt == "STORY" and len(item["media"]) != 1:
        return ["STORY_PRECISA_DE_UMA_MIDIA"]
    if fmt in ("IMAGE", "CAROUSEL") and videos:
        return ["VIDEO_FORA_DO_REEL"]
    return []


def media_problems(
    item: dict[str, Any],
    check: MediaChecker,
    approved: Mapping[str, str] | None = None,
) -> list[str]:
    """Quarentena, manifesto aprovado, content-type por formato e sha256."""
    from urllib.parse import urlsplit

    from integrations.meta_content_publisher import quarantined_media_paths

    approved = load_approved_media() if approved is None else approved
    quarantined = quarantined_media_paths()
    problems = format_problems(item)
    for url in item["media"]:
        path = urlsplit(url).path
        if path in quarantined:
            problems.append(f"quarentena: {url}")
            continue
        expected = approved.get(path)
        if expected is None:
            problems.append(f"MEDIA_NOT_APPROVED: {path}")
            continue
        status, content_type, *rest = check(url)
        wanted = "video/mp4" if _is_video(url) else "image/jpeg"
        if status != 200 or wanted not in content_type:
            problems.append(f"{url}: {status} {content_type}")
        elif rest and rest[0] != expected:
            problems.append(f"MEDIA_HASH_MISMATCH: {path}")
    return problems


def media_qa(path: Path, fmt: str) -> dict[str, Any]:
    """QA local antes de aprovar a mídia: dimensões, proporção, tamanho e EXIF."""
    data = path.read_bytes()
    info: dict[str, Any] = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "problems": [],
    }
    if path.suffix.lower() == ".mp4":
        if fmt not in ("REEL", "STORY"):
            info["problems"].append("VIDEO_FORA_DO_REEL")
        if len(data) > MAX_VIDEO_BYTES:
            info["problems"].append("VIDEO_ACIMA_DO_LIMITE")
        return info  # duração e resolução do vídeo: conferir no app antes
    try:
        from PIL import Image
    except ImportError:
        info["problems"].append("PILLOW_AUSENTE")
        return info
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        if image.format != "JPEG":
            info["problems"].append("NAO_E_JPEG")
        if image.getexif():
            info["problems"].append("EXIF_PRESENTE")  # inclui GPS; tirar no import
    info.update(width=width, height=height)
    low, high = ASPECT_RANGES[fmt]
    if not low - 0.01 <= width / height <= high + 0.01:
        info["problems"].append(f"PROPORCAO_{width}x{height}")
    if width < MIN_WIDTH:
        info["problems"].append("RESOLUCAO_ABAIXO_DE_1080")
    if len(data) > MAX_IMAGE_BYTES:
        info["problems"].append("IMAGEM_ACIMA_DE_8MB")
    return info


def approve_media(
    files: list[str | Path], *, fmt: str, actor: str, manifest: Path | None = None
) -> dict[str, Any]:
    """O fundador libera a mídia: passa no QA e entra no manifesto com o sha256."""
    manifest = manifest or APPROVED_MEDIA
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"midias": []}
    entries = {entry["path"]: entry for entry in data.get("midias") or []}
    landing = BASE_DIR / "landing"
    approved, refused = [], []
    for name in files:
        path = Path(name).resolve()
        try:
            public = "/" + path.relative_to(landing).as_posix()
        except ValueError:
            refused.append({"file": str(name), "problems": ["FORA_DE_LANDING"]})
            continue
        qa = media_qa(path, fmt)
        if qa["problems"]:
            refused.append({"file": public, "problems": qa["problems"]})
            continue
        entries[public] = {
            "path": public,
            "sha256": qa["sha256"],
            "formato": fmt,
            "aprovado_por": actor,
            "aprovado_em": _now().date().isoformat(),
        }
        approved.append(public)
    if approved:
        data["midias"] = sorted(entries.values(), key=lambda entry: entry["path"])
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return {"approved": approved, "refused": refused}


def run_due(
    store: SocialScheduleStore,
    *,
    publisher: Publisher | None,
    approval_status: Callable[[str], str | None],
    enabled: bool | None = None,
    media_check: MediaChecker = http_media_check,
    alert: Callable[[dict[str, Any]], Any] | None = None,
    task: Callable[[dict[str, Any]], Any] | None = None,
    approved_media: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _now()
    enabled = env_flag("SOCIAL_PUBLISH_ENABLED") if enabled is None else enabled
    refresh_pending(store)  # relógio que ganhou foto real vai à aprovação
    synced = sync_approvals(store, approval_status)
    due, skipped = [], []
    unready = getattr(publisher, "unready_channels", None)
    for item in store.items(status="APPROVED"):
        if item["scheduled_at"] > now.isoformat() or (
            item["next_attempt_at"] and item["next_attempt_at"] > now.isoformat()
        ):
            continue
        # Canal pedido e não configurado: o item espera, não sai pela metade.
        reasons = unready(item) if callable(unready) else []
        if reasons:
            reason = ",".join(reasons)
            if item["block_reason"] != reason:
                store.set_status(item["id"], "APPROVED", block_reason=reason)
                store.event(item["id"], "CHANNEL_NOT_CONFIGURED", {"reasons": reasons})
            skipped.append({"id": item["id"], "reasons": reasons})
            continue
        due.append(item)
    preview = [
        {
            "id": item["id"],
            "scheduled_at": item["scheduled_at"],
            "format": item["format"],
            "channels": item["channels"],
            "media": item["media"],
            "content_hash": item["content_hash"],
        }
        for item in due
    ]
    if not enabled or publisher is None:
        return {
            "status": "PREVIEW",
            "reasons": [
                *([] if enabled else ["SOCIAL_PUBLISH_DISABLED"]),
                *([] if publisher or not enabled else ["META_CREDENTIALS_MISSING"]),
            ],
            "approvals": synced,
            "would_publish": preview,
            "skipped": skipped,
            "external_write": False,
        }
    published, failed, manual = [], [], []
    for item in due:
        if not store.claim(item["id"]):
            continue
        previous = store.publication(item["id"], item["content_hash"])
        if previous is not None:  # idempotência: mesmo id e mesmo conteúdo
            store.set_status(item["id"], _final_status(previous))
            published.append(item["id"])
            continue
        problems = media_problems(item, media_check, approved_media)
        if problems:
            store.set_status(
                item["id"], "BLOCKED", block_reason="; ".join(problems)[:500]
            )
            store.event(item["id"], "MEDIA_CHECK_FAILED", {"problems": problems})
            failed.append({"id": item["id"], "error": "MEDIA_CHECK_FAILED"})
            continue
        try:
            result = {**item["published"], **publisher.publish(item)}
        except Exception as exc:  # noqa: BLE001 - uma falha não para o lote
            attempts = int(item["attempts"]) + 1
            error = type(exc).__name__
            # O que já saiu fica gravado: a nova tentativa não repete o canal.
            partial = {
                "published_json": json.dumps(item["published"]),
                "permalink": _permalink(item["published"]),
            }
            if attempts >= MAX_ATTEMPTS:
                store.set_status(
                    item["id"], "FAILED", attempts=attempts, last_error=error, **partial
                )
                store.event(
                    item["id"],
                    "PUBLISH_FAILED",
                    {"error": error, "done": sorted(item["published"])},
                )
                if alert:
                    alert({"item_id": item["id"], "error": error, "attempts": attempts})
            else:
                retry = now + timedelta(minutes=5 * 2**attempts)
                store.set_status(
                    item["id"],
                    "APPROVED",
                    attempts=attempts,
                    last_error=error,
                    next_attempt_at=retry.isoformat(),
                    **partial,
                )
            failed.append({"id": item["id"], "error": error, "attempts": attempts})
            continue
        store.record_publication(item, result)
        status = _final_status(result)
        store.set_status(
            item["id"],
            status,
            published_json=json.dumps(result),
            permalink=_permalink(result),
            published_at=now.isoformat(),
            block_reason=None,
        )
        store.event(item["id"], "PUBLISHED", result)
        for channel, outcome in sorted(result.items()):
            if outcome.get("status") != "NEEDS_MANUAL":
                continue
            # Tarefa no painel: o fundador posta à mão com esta mídia e legenda.
            detail = {
                "channel": channel,
                "reason": outcome.get("reason") or "MANUAL",
                "format": item["format"],
                "media": item["media"],
                "caption": item["caption"],
                "sticker": item["sticker"],
                "delivery": "PAINEL",
            }
            store.event(item["id"], "CHANNEL_NEEDS_MANUAL", detail)
            if task:
                task({"item_id": item["id"], **detail})
            manual.append({"id": item["id"], "channel": channel})
        published.append(item["id"])
    return {
        "status": "RAN",
        "approvals": synced,
        "published": published,
        "failed": failed,
        "skipped": skipped,
        "manual": manual,
        "external_write": bool(published),
    }


def _permalink(result: dict[str, Any]) -> str | None:
    return (result.get("instagram") or {}).get("permalink") or (
        result.get("facebook") or {}
    ).get("permalink")


def _final_status(result: dict[str, Any]) -> str:
    manual = any(r.get("status") == "NEEDS_MANUAL" for r in result.values())
    sent = any(r.get("id") for r in result.values())
    if not manual:
        return "PUBLISHED"
    return "PUBLISHED_PARTIAL" if sent else "NEEDS_MANUAL"


class MetaSocialPublisher:
    """Instagram (imagem, carrossel, story e reel) e Página do Facebook.

    Cada canal fica em ``item["published"]`` assim que sai (antes da leitura de
    volta): numa nova tentativa, o canal com id não é publicado de novo.
    """

    def __init__(
        self,
        session: Any,
        *,
        access_token: str,
        ig_user_id: str,
        page_id: str | None = None,
    ) -> None:
        self.session = session
        self.access_token = access_token
        self.ig_user_id = ig_user_id
        self.page_id = page_id

    @classmethod
    def from_env(cls) -> MetaSocialPublisher | None:
        import os

        import requests

        token = os.getenv("META_PAGE_ACCESS_TOKEN", "").strip()
        ig_user_id = os.getenv("META_IG_BUSINESS_ACCOUNT_ID", "").strip()
        if not token or not ig_user_id:
            return None
        return cls(
            requests.Session(),
            access_token=token,
            ig_user_id=ig_user_id,
            page_id=os.getenv("META_PAGE_ID", "").strip() or None,
        )

    def unready_channels(self, item: dict[str, Any]) -> list[str]:
        if "facebook" in item["channels"] and not self.page_id:
            return ["FACEBOOK_PAGE_ID_MISSING"]
        return []

    def publish(self, item: dict[str, Any]) -> dict[str, Any]:
        from integrations import meta_content_publisher as meta

        done = item.get("published")
        done = done if isinstance(done, dict) else {}
        videos = [url for url in item["media"] if _is_video(url)]
        payload = {
            "type": item["format"],
            "caption": item["caption"],
            "alt_text": item["alt_text"],
            "media_urls": item["media"],
            "video_url": videos[0] if videos else None,
            "cover_url": next(
                (url for url in item["media"] if not _is_video(url)), None
            ),
        }
        wait = {"attempts": 60, "interval": 5} if videos else {}
        ig = done.get("instagram") or {}
        if "instagram" in item["channels"] and not ig:
            if item["format"] == "STORY" and item.get("sticker"):
                done["instagram"] = {
                    "status": "NEEDS_MANUAL",
                    "reason": "STORY_STICKER",
                }
            else:
                container = meta.create_social_container(
                    self.session, payload, self.ig_user_id, self.access_token
                )
                meta.wait_until_ready(
                    self.session, container, self.access_token, **wait
                )
                media_id = meta.publish_container(
                    self.session, container, self.ig_user_id, self.access_token
                )
                done["instagram"] = {"id": media_id}  # gravado antes da leitura
        ig = done.get("instagram") or {}
        if ig.get("id") and not ig.get("permalink"):
            readback = meta.read_publication(self.session, ig["id"], self.access_token)
            ig["permalink"] = readback.get("permalink")
        fb = done.get("facebook") or {}
        if "facebook" in item["channels"] and self.page_id and not fb:
            if item["format"] == "STORY":
                done["facebook"] = {"status": "NEEDS_MANUAL", "reason": "FB_STORY"}
            elif videos:
                done["facebook"] = {
                    "id": meta.publish_facebook_video(
                        self.session, payload, self.page_id, self.access_token
                    )
                }
            else:
                done["facebook"] = {
                    "id": meta.publish_facebook_page(
                        self.session, payload, self.page_id, self.access_token
                    )
                }
        fb = done.get("facebook") or {}
        if fb.get("id") and not fb.get("permalink"):
            fb["permalink"] = meta.read_facebook_permalink(
                self.session, fb["id"], self.access_token
            )
        if "tiktok" in item["channels"] and "tiktok" not in done:
            done["tiktok"] = TikTokDraftAdapter().upload_draft(item)
        return done


class TikTokDraftAdapter:
    """Upload em rascunho no TikTok: ``UNVERIFIED`` até a auditoria do app."""

    api_status = "UNVERIFIED"

    def upload_draft(self, item: dict[str, Any]) -> dict[str, Any]:
        return {"status": "NEEDS_MANUAL", "reason": "TIKTOK_APP_AUDIT_PENDING"}


# -- palavras-chave por post --------------------------------------------------------


def media_reply_rules(
    store: SocialScheduleStore, keyword_replies: dict[str, dict[str, str]]
) -> dict[str, list[Any]]:
    """Regras de resposta privada por mídia publicada (id da mídia → regras)."""
    from .instagram_comments import ReplyRule

    rules: dict[str, list[Any]] = {}
    for row in store.media_keywords():
        reply = keyword_replies.get(row["keyword"])
        if not reply:
            continue
        rules.setdefault(row["media_id"], []).append(
            ReplyRule(
                rule_id=f"kw-{normalize_text(row['keyword'])}-{row['media_id']}",
                keywords=frozenset({normalize_text(row["keyword"])}),
                message=reply["message"],
                utm_campaign=f"post_{row['item_id']}_{normalize_text(row['keyword'])}"[
                    :80
                ],
            )
        )
    return rules


# -- métricas ---------------------------------------------------------------------


# Métricas por formato (Graph API): a conferir na hora de ligar.
IG_METRICS = {
    "IMAGE": "reach,saved,comments,shares,views",
    "CAROUSEL": "reach,saved,comments,shares,views",
    "REEL": "reach,saved,comments,shares,views",
    "STORY": "reach,replies,shares,views",
}
FB_POST_METRICS = "post_impressions_unique,post_clicks"
ACCOUNT_METRICS = "reach,profile_views,website_clicks"
InsightFetcher = Callable[[str, str], dict[str, Any]]


def insight_checkpoints(fmt: str) -> tuple[tuple[str, timedelta], ...]:
    return STORY_CHECKPOINTS if fmt == "STORY" else INSIGHT_CHECKPOINTS


def due_insights(
    store: SocialScheduleStore, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Leituras vencidas (prévia sem rede): item, canal e checkpoint."""
    now = now or _now()
    saved = {(row["item_id"], row["checkpoint"]) for row in store.insights()}
    due = []
    for item in [
        *store.items(status="PUBLISHED"),
        *store.items(status="PUBLISHED_PARTIAL"),
    ]:
        if not item["published_at"]:
            continue
        age = now - datetime.fromisoformat(item["published_at"])
        for channel, prefix in (("instagram", ""), ("facebook", "fb:")):
            media_id = (item["published"].get(channel) or {}).get("id")
            if not media_id:
                continue
            for checkpoint, delay in insight_checkpoints(item["format"]):
                key = prefix + checkpoint
                if age >= delay and (item["id"], key) not in saved:
                    due.append(
                        {
                            "id": item["id"],
                            "channel": channel,
                            "checkpoint": key,
                            "media_id": media_id,
                            "format": item["format"],
                            "expired": item["format"] == "STORY"
                            and age > STORY_LIFETIME,
                        }
                    )
    return due


def collect_insights(
    store: SocialScheduleStore,
    fetch: InsightFetcher,
    *,
    fetch_facebook: InsightFetcher | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Lê as métricas vencidas; um erro num item não para a coleta."""
    collected = []
    for entry in due_insights(store, now=now):
        fetcher = fetch if entry["channel"] == "instagram" else fetch_facebook
        if fetcher is None:
            continue
        if entry["expired"]:  # o Story saiu do ar antes da leitura: não volta
            store.save_insight(entry["id"], entry["checkpoint"], entry["media_id"], {})
            store.event(
                entry["id"], "INSIGHT_EXPIRED", {"checkpoint": entry["checkpoint"]}
            )
            continue
        try:
            metrics = fetcher(entry["media_id"], entry["format"])
        except Exception as exc:  # noqa: BLE001 - segue para o próximo item
            store.event(
                entry["id"],
                "INSIGHT_FAILED",
                {"checkpoint": entry["checkpoint"], "error": type(exc).__name__},
            )
            continue
        store.save_insight(entry["id"], entry["checkpoint"], entry["media_id"], metrics)
        collected.append(
            {"id": entry["id"], "checkpoint": entry["checkpoint"], **metrics}
        )
    return collected


def _graph_values(response: Any) -> dict[str, Any]:
    response.raise_for_status()
    values = {}
    for entry in response.json().get("data") or []:
        total = entry.get("total_value") or {}
        values[entry["name"]] = (
            total.get("value")
            if "value" in total
            else (entry.get("values") or [{}])[0].get("value")
        )
    return values


def graph_insights_fetcher(access_token: str, session: Any = None) -> InsightFetcher:
    import requests

    from .meta_webhooks import graph_api_version

    session = session or requests.Session()

    def fetch(media_id: str, fmt: str = "IMAGE") -> dict[str, Any]:
        response = session.get(
            f"https://graph.facebook.com/{graph_api_version()}/{media_id}/insights",
            params={
                "metric": IG_METRICS.get(fmt, IG_METRICS["IMAGE"]),
                "access_token": access_token,
            },
            timeout=30,
        )
        return _graph_values(response)

    return fetch


def graph_facebook_insights_fetcher(
    access_token: str, session: Any = None
) -> InsightFetcher:
    """Post da Página: alcance e cliques (nomes das métricas a conferir na hora de ligar)."""
    import requests

    from .meta_webhooks import graph_api_version

    session = session or requests.Session()

    def fetch(post_id: str, fmt: str = "IMAGE") -> dict[str, Any]:
        response = session.get(
            f"https://graph.facebook.com/{graph_api_version()}/{post_id}/insights",
            params={"metric": FB_POST_METRICS, "access_token": access_token},
            timeout=30,
        )
        values = _graph_values(response)
        return {
            "reach": values.get("post_impressions_unique"),
            "clicks": values.get("post_clicks"),
        }

    return fetch


def collect_account_metrics(
    store: SocialScheduleStore,
    fetch_account: Callable[[], dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Seguidores, visitas ao perfil e cliques no site: uma leitura por dia."""
    day = (now or _now()).astimezone(SAO_PAULO).date().isoformat()
    if any(row["day"] == day for row in store.account_metrics()):
        return None
    try:
        metrics = fetch_account()
    except Exception as exc:  # noqa: BLE001
        store.event(None, "ACCOUNT_INSIGHT_FAILED", {"error": type(exc).__name__})
        return None
    store.save_account_metrics(day, metrics)
    return {"day": day, **metrics}


def graph_account_fetcher(
    access_token: str, ig_user_id: str, session: Any = None
) -> Callable[[], dict[str, Any]]:
    import requests

    from .meta_webhooks import graph_api_version

    session = session or requests.Session()
    base = f"https://graph.facebook.com/{graph_api_version()}/{ig_user_id}"

    def fetch() -> dict[str, Any]:
        profile = session.get(
            base,
            params={"fields": "followers_count", "access_token": access_token},
            timeout=30,
        )
        profile.raise_for_status()
        # metric_type=total_value e period=day: a conferir na hora de ligar.
        values = _graph_values(
            session.get(
                f"{base}/insights",
                params={
                    "metric": ACCOUNT_METRICS,
                    "period": "day",
                    "metric_type": "total_value",
                    "access_token": access_token,
                },
                timeout=30,
            )
        )
        return {"followers": profile.json().get("followers_count"), **values}

    return fetch


def run_insights(
    store: SocialScheduleStore,
    *,
    enabled: bool | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Com SOCIAL_INSIGHTS_ENABLED desligada, só a prévia (nenhuma chamada à Meta)."""
    import os

    enabled = env_flag("SOCIAL_INSIGHTS_ENABLED") if enabled is None else enabled
    token = os.getenv("META_PAGE_ACCESS_TOKEN", "").strip()
    if not enabled or not token:
        return {
            "status": "PREVIEW",
            "reasons": [
                *([] if enabled else ["SOCIAL_INSIGHTS_DISABLED"]),
                *([] if token or not enabled else ["META_PAGE_ACCESS_TOKEN_MISSING"]),
            ],
            "would_read": due_insights(store, now=now),
            "external_read": False,
        }
    import requests

    session = requests.Session()
    ig_user_id = os.getenv("META_IG_BUSINESS_ACCOUNT_ID", "").strip()
    return {
        "status": "RAN",
        "collected": collect_insights(
            store,
            graph_insights_fetcher(token, session),
            fetch_facebook=graph_facebook_insights_fetcher(token, session),
            now=now,
        ),
        "account": (
            collect_account_metrics(
                store, graph_account_fetcher(token, ig_user_id, session), now=now
            )
            if ig_user_id
            else None
        ),
        "external_read": True,
    }


def attribution(
    store: SocialScheduleStore,
    orders: list[dict[str, Any]] | None = None,
    conversations: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Conversas e vendas por post, pelo ``utm_campaign`` ``post_<id>_<palavra>``.

    ``orders`` vem do Orders Ledger (só o utm é lido); ``conversations`` é a
    lista de ``utm_campaign`` das conversas que chegaram pelo link do post.
    """
    ids = sorted((item["id"] for item in store.items()), key=len, reverse=True)
    result: dict[str, dict[str, int]] = {}

    def owner(campaign: str | None) -> str | None:
        campaign = campaign or ""
        return next((i for i in ids if campaign.startswith(f"post_{i}_")), None)

    for order in orders or []:
        item_id = owner(order.get("utm_campaign"))
        if item_id and order.get("status") in ("PAID", "SHIPPED", "DELIVERED"):
            result.setdefault(item_id, {"conversas": 0, "vendas": 0})["vendas"] += 1
    for campaign in conversations or []:
        item_id = owner(campaign)
        if item_id:
            result.setdefault(item_id, {"conversas": 0, "vendas": 0})["conversas"] += 1
    return result


def performance_records(
    store: SocialScheduleStore, orders: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Última leitura de cada post como PerformanceRecord (spend 0) para o metrics_cli."""
    from .schemas import PerformanceRecord

    items = {item["id"]: item for item in store.items()}
    credited = attribution(store, orders)
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in store.insights():  # em ordem de leitura: a última vence
        channel = "facebook" if row["checkpoint"].startswith("fb:") else "instagram"
        if row["reach"] is not None or row["metrics"]:
            latest[(row["item_id"], channel)] = row
    records = []
    for (item_id, channel), row in sorted(latest.items()):
        metrics = row["metrics"] or {}
        sales = credited.get(item_id, {}) if channel == "instagram" else {}
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "-", item_id)
        record = PerformanceRecord(
            event_id=f"social:{safe_id}:{row['checkpoint'].replace('fb:', 'fb-')}",
            occurred_on=row["at"][:10],
            channel=f"{channel}_organico",
            campaign=f"post_{item_id}"[:140],
            creative=(items.get(item_id) or {}).get("format") or "sem_criativo",
            impressions=int(metrics.get("views") or row["reach"] or 0),
            clicks=int(metrics.get("clicks") or 0),
            conversations=sales.get("conversas", 0),
            sales=sales.get("vendas", 0),
            evidence=f"Graph API insights {row['checkpoint']} ({row['media_id']})",
        )
        records.append(record.model_dump(mode="json"))
    return records


def weekly_report(store: SocialScheduleStore, week: str) -> dict[str, Any]:
    """Resumo da semana para o Social propor a seguinte."""
    items = store.items(week=week)
    insights = {(row["item_id"], row["checkpoint"]): row for row in store.insights()}
    rows = []
    for item in items:
        best = (
            insights.get((item["id"], "7d"))
            or insights.get((item["id"], "24h"))
            or insights.get((item["id"], "20h"))
        )
        rows.append(
            {
                "id": item["id"],
                "format": item["format"],
                "status": item["status"],
                "reach": best and best["reach"],
                "saved": best and best["saved"],
                "shares": best and best["shares"],
                "comments": best and best["comments"],
            }
        )
    measured = [row for row in rows if row["reach"] is not None]
    ranked = sorted(
        measured,
        key=lambda row: (row["saved"] or 0) + (row["shares"] or 0),
        reverse=True,
    )
    by_format: dict[str, int] = {}
    for row in measured:
        by_format[row["format"]] = by_format.get(row["format"], 0) + (row["reach"] or 0)
    return {
        "week": week,
        "items": rows,
        "top": [row["id"] for row in ranked[:3]],
        "reach_by_format": by_format,
        "suggestion": (
            "Repetir o formato e o tema dos itens do topo; trocar os de menor "
            "alcance. A proposta da semana seguinte volta como itens "
            "PENDING_APPROVAL."
        ),
    }


def insights_csv(
    store: SocialScheduleStore,
    *,
    week: str | None = None,
    orders: list[dict[str, Any]] | None = None,
    conversations: list[str] | None = None,
) -> str:
    """No formato de social/medicao.csv (para o ig-audit e a tabela de medição)."""
    header = [
        "data",
        "canal",
        "formato",
        "conteudo",
        "alcance",
        "reproducoes",
        "retencao_3s",
        "tempo_medio",
        "salvamentos",
        "compartilhamentos",
        "comentarios",
        "visitas_perfil",
        "cliques_link",
        "conversas_whatsapp",
        "vendas_atribuidas",
        "observacoes",
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    items = {item["id"]: item for item in store.items(week=week)}
    credited = attribution(store, orders, conversations)
    for row in store.insights():
        item = items.get(row["item_id"])
        if item is None:
            continue
        metrics = row["metrics"] or {}
        facebook = row["checkpoint"].startswith("fb:")
        sales = {} if facebook else credited.get(row["item_id"], {})
        writer.writerow(
            [
                row["at"][:10],
                "Facebook" if facebook else "Instagram",
                item["format"],
                row["item_id"],
                row["reach"],
                metrics.get("views", ""),
                "",
                "",
                row["saved"],
                row["shares"],
                row["comments"],
                "",
                metrics.get("clicks", ""),
                sales.get("conversas", ""),
                sales.get("vendas", ""),
                f"checkpoint {row['checkpoint']}",
            ]
        )
    return output.getvalue()


def audit_snapshot(
    store: SocialScheduleStore, *, now: datetime | None = None
) -> dict[str, Any]:
    """O que o social-media-worker mostra no painel ao vivo (sem rede)."""
    now_iso = (now or _now()).isoformat()
    items = store.items()
    blocked = Counter(
        reason.split(":", 1)[0]
        for item in items
        if item["status"] in ("BLOCKED", "NEEDS_INFO", "DRAFT") and item["block_reason"]
        for reason in item["block_reason"].split(",")
    )
    manual = store.events("CHANNEL_NEEDS_MANUAL")
    insights = store.insights()
    last = insights[-1] if insights else None
    return {
        "by_status": dict(Counter(item["status"] for item in items)),
        "next_up": [
            {
                "id": item["id"],
                "scheduled_at": item["scheduled_at"],
                "format": item["format"],
                "channels": item["channels"],
            }
            for item in items
            if item["status"] == "APPROVED" and item["scheduled_at"] >= now_iso
        ][:5],
        "block_reasons": dict(blocked.most_common(8)),
        "pending_approvals": {
            "requested": len(
                {
                    item["approval_id"]
                    for item in items
                    if item["status"] == "PENDING_APPROVAL" and item["approval_id"]
                }
            ),
            "not_requested": sum(
                1
                for item in items
                if item["status"] == "PENDING_APPROVAL" and not item["approval_id"]
            ),
        },
        "manual_tasks": [
            {
                "item_id": event["item_id"],
                "channel": json.loads(event["detail_json"]).get("channel"),
                "at": event["at"],
            }
            for event in manual[-5:]
        ],
        "last_insight": last
        and {
            "item_id": last["item_id"],
            "checkpoint": last["checkpoint"],
            "reach": last["reach"],
            "at": last["at"],
        },
    }


def audit_snapshot_from_env() -> dict[str, Any]:
    """Para a auditoria das operações: nunca derruba o relatório."""
    try:
        from .config import AgentSettings

        settings = AgentSettings.from_env()
        if not settings.database_url and not settings.operations_db.exists():
            return {"schedule": "SEM_CALENDARIO"}
        from .instagram_comments import CommentReplyQueue

        store = SocialScheduleStore(settings.operations_db, settings.database_url)
        comments = CommentReplyQueue(settings.operations_db, settings.database_url)
        return {
            "schedule": "OK",
            **audit_snapshot(store),
            "comment_tasks_open": len(comments.list_actions(status="OPEN")),
            "comment_drafts": len(comments.list_actions(status="DRAFT")),
        }
    except Exception as exc:  # noqa: BLE001 - a auditoria segue sem o resumo
        return {"schedule": "INDISPONIVEL", "error": type(exc).__name__}


def run_due_from_env(
    store: SocialScheduleStore, engine: Any, *, enabled: bool | None = None
) -> dict[str, Any]:
    """CLI e rota admin: com SOCIAL_PUBLISH_ENABLED desligada, só a prévia."""
    from .operations import approval_status

    enabled = env_flag("SOCIAL_PUBLISH_ENABLED") if enabled is None else enabled
    return run_due(
        store,
        publisher=MetaSocialPublisher.from_env() if enabled else None,
        approval_status=lambda request_id: approval_status(engine, request_id),
        enabled=enabled,
    )


# -- CLI --------------------------------------------------------------------------


def store_from_env() -> SocialScheduleStore:
    from .config import AgentSettings

    settings = AgentSettings.from_env()
    return SocialScheduleStore(settings.operations_db, settings.database_url)


def _orders_for_attribution() -> list[dict[str, Any]]:
    """Só o utm e o status de cada pedido (o resto do pedido não sai daqui)."""
    try:
        from .config import AgentSettings
        from .orders import make_order_ledger

        settings = AgentSettings.from_env()
        ledger = make_order_ledger(settings.operations_db, settings.database_url)
        return [
            {"utm_campaign": row.get("utm_campaign"), "status": row.get("status")}
            for row in ledger.list_orders(limit=1000)
        ]
    except Exception:  # noqa: BLE001 - sem o ledger, a coluna fica vazia
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m veratus_agents.social_scheduler")
    sub = parser.add_subparsers(dest="command", required=True)
    importer = sub.add_parser("import", help="importa o calendário JSON")
    importer.add_argument("path", nargs="?", default=str(DEFAULT_CALENDAR))
    validator = sub.add_parser("validate", help="confere o pacote sem gravar")
    validator.add_argument("path")
    adder = sub.add_parser("add", help="rascunho de skill vira item (gate e lint)")
    adder.add_argument("--from", dest="source", required=True)
    adder.add_argument("--actor", default="fundador")
    media = sub.add_parser("approve-media", help="QA e manifesto de mídia aprovada")
    media.add_argument("files", nargs="+")
    media.add_argument("--format", required=True, choices=FORMATS)
    media.add_argument("--actor", default="fundador")
    approval = sub.add_parser("request-approval", help="um pedido por semana")
    approval.add_argument("--week")
    sub.add_parser("run-due", help="publica os aprovados vencidos (ou mostra a prévia)")
    sub.add_parser("refresh", help="relógios que ganharam foto real")
    sub.add_parser("insights", help="métricas por formato e da conta (ou a prévia)")
    report = sub.add_parser("report", help="relatório semanal")
    report.add_argument("--week", required=True)
    report.add_argument("--csv", action="store_true", help="CSV para o ig-audit")
    listing = sub.add_parser("list", help="lista os itens")
    listing.add_argument("--status")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            output: Any = validate_calendar(args.path)
        elif args.command == "approve-media":
            output = approve_media(args.files, fmt=args.format, actor=args.actor)
        else:
            store = store_from_env()
            if args.command == "import":
                output = import_calendar(store, args.path)
            elif args.command == "add":
                output = add_items(
                    store, _read_calendar(Path(args.source)), actor=args.actor
                )
            elif args.command == "list":
                output = store.items(status=args.status)
            elif args.command == "refresh":
                output = refresh_pending(store)
            elif args.command == "report" and args.csv:
                print(
                    insights_csv(
                        store, week=args.week, orders=_orders_for_attribution()
                    ),
                    end="",
                )
                return 0
            elif args.command == "report":
                output = weekly_report(store, args.week)
            elif args.command == "insights":
                output = run_insights(store)
            else:
                from .operations import CommandEngine, runtime_persistence

                engine = CommandEngine(persistence_path=runtime_persistence())
                if args.command == "request-approval":
                    output = request_weekly_approvals(store, engine, week=args.week)
                else:
                    output = run_due_from_env(store, engine)
    except CalendarError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
