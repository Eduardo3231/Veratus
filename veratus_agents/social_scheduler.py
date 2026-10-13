"""Agendador de redes sociais com aprovação semanal.

Fluxo::

    import (calendário JSON) → PENDING_APPROVAL → aprovação semanal (um
    ApprovalRequest por semana com os content_hash) → APPROVED → run-due
    (cron do Render a cada 5 min) → PUBLISHING → PUBLISHED | FAILED

- Item editado depois de aprovado volta para ``PENDING_APPROVAL``.
- Vídeo entra ``NEEDS_VIDEO``. Só TikTok fica ``NEEDS_MANUAL``, porque o app
  ainda não passou pela auditoria.
- Relógio sem ``REAL_PHOTO``, linha de iPhone e mídia em quarentena entram
  ``BLOCKED`` e nunca aparecem num plano de publicação.
- Sem ``SOCIAL_PUBLISH_ENABLED=true``, ``run-due`` só devolve a prévia, sem
  chamar a Meta.

CLI: ``python -m veratus_agents.social_scheduler import|request-approval|run-due|
refresh-blocks|insights|report|list``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .meta_webhooks import env_flag, normalize_text
from .sqlstore import SqlStore

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CALENDAR = BASE_DIR / "social" / "calendario-2026-10.json"
PUBLIC_BASE = "https://veratus.onrender.com"
SAO_PAULO = timezone(timedelta(hours=-3))
FORMATS = ("IMAGE", "CAROUSEL", "STORY", "REEL")
STATUSES = (
    "DRAFT",
    "PENDING_APPROVAL",
    "APPROVED",
    "PUBLISHING",
    "PUBLISHED",
    "FAILED",
    "NEEDS_MANUAL",
    "NEEDS_VIDEO",
    "BLOCKED",
)
META_CHANNELS = frozenset({"instagram", "facebook"})
MAX_ATTEMPTS = 3
CAROUSEL_LIMIT = 10
INSIGHT_CHECKPOINTS = (("24h", timedelta(hours=24)), ("7d", timedelta(days=7)))
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


def block_reasons(
    item: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> list[str]:
    """Por que um item não pode ir ao ar, olhando produto, linha e mídia."""
    from integrations.meta_content_publisher import quarantined_media_paths

    reasons = []
    text = normalize_text(
        " ".join([item["caption"], *item["keywords"], *item["products"], item["id"]])
    )
    if "iphone" in text or item.get("category") == "smartphone":
        reasons.append("LINHA_IPHONE_DEPOIS_DO_TESTE")
    for product_id in item["products"]:
        product = catalog.get(product_id)
        is_watch = product is not None and (
            (product.get("collection") or "watches") == "watches"
        )
        if (is_watch or item.get("category") in ("watch", "relogio")) and (
            product or {}
        ).get("image_status") != "REAL_PHOTO":
            reasons.append("NEEDS_REAL_PHOTO")
            break
    if not item["products"] and item.get("category") in ("watch", "relogio"):
        reasons.append("NEEDS_REAL_PHOTO")
    quarantined = quarantined_media_paths()
    from urllib.parse import urlsplit

    if any(urlsplit(url).path in quarantined for url in item["media"]):
        reasons.append("MEDIA_IN_QUARANTINE")
    return list(dict.fromkeys(reasons))


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
            "calendar_status": str(_first(raw, "status") or "").strip().upper(),
            "calendar_block_reason": str(
                _first(raw, "blocked_reason", "motivo_bloqueio", "bloqueio") or ""
            ).strip(),
        }
        items.append(item)
    return items


def initial_status(
    item: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> tuple[str, str | None]:
    reasons = block_reasons(item, catalog)
    if item["calendar_status"] == "BLOCKED":
        reasons.insert(0, item["calendar_block_reason"] or "BLOCKED_IN_CALENDAR")
    if reasons:
        return "BLOCKED", ",".join(dict.fromkeys(reasons))
    if item["format"] == "REEL" and not any(
        url.lower().endswith(".mp4") for url in item["media"]
    ):
        return "NEEDS_VIDEO", "vídeo ainda não produzido"
    if not (set(item["channels"]) & META_CHANNELS):
        return "NEEDS_MANUAL", "TIKTOK_UNVERIFIED"
    if not item["media"] or not item["caption"]:
        return "DRAFT", "faltam mídia ou legenda"
    return "PENDING_APPROVAL", None


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

    def get(self, item_id: str) -> dict[str, Any] | None:
        with self._connect() as (db, mark):
            row = db.execute(
                self._sql("SELECT * FROM scheduled_posts WHERE id = ?", mark), [item_id]
            ).fetchone()
        return self._decode(row) if row else None

    def items(
        self, *, status: str | None = None, week: str | None = None
    ) -> list[dict[str, Any]]:
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if week:
            where.append("week = ?")
            params.append(week)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql(
                    f"SELECT * FROM scheduled_posts {clause} ORDER BY scheduled_at",
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
                return "created"
            if existing["content_hash"] == digest and existing["status"] not in (
                "BLOCKED",
                "NEEDS_VIDEO",
            ):
                return "unchanged"
            if existing["status"] in ("PUBLISHING", "PUBLISHED"):
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
        return "updated"

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

    def insights(self) -> list[dict[str, Any]]:
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql("SELECT * FROM social_insights ORDER BY at", mark)
            ).fetchall()
        return [dict(row) for row in rows]


# -- importação, aprovação e bloqueios ------------------------------------------------


def import_calendar(
    store: SocialScheduleStore,
    path: str | Path = DEFAULT_CALENDAR,
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CalendarError(
            f"{path.name} não encontrado: o pacote do fundador ainda não chegou"
        ) from exc
    except ValueError as exc:
        raise CalendarError(f"{path.name}: JSON inválido") from exc
    catalog = catalog if catalog is not None else _catalog()
    counts: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for item in parse_calendar(data):
        status, reason = initial_status(item, catalog)
        outcome = store.upsert(item, status, reason)
        counts[outcome] = counts.get(outcome, 0) + 1
        statuses[status] = statuses.get(status, 0) + 1
    store.event(None, "CALENDAR_IMPORTED", {"file": path.name, **counts})
    return {"items": sum(statuses.values()), "by_status": statuses, **counts}


def edit_item(
    store: SocialScheduleStore, item_id: str, *, actor: str, **changes: Any
) -> dict[str, Any]:
    """Editar legenda, mídia, horário ou palavras-chave tira a aprovação."""
    item = store.get(item_id)
    if item is None:
        raise KeyError(item_id)
    editable = {"caption", "alt_text", "media", "keywords", "scheduled_at", "channels"}
    if set(changes) - editable:
        raise ValueError("campo_nao_editavel")
    updated = {**item, **changes, "calendar_status": "", "calendar_block_reason": ""}
    status, reason = initial_status(updated, _catalog())
    outcome = store.upsert(updated, status, reason)
    store.event(item_id, "ITEM_EDITED", {"actor": actor, "outcome": outcome})
    return store.get(item_id) or {}


def refresh_blocks(
    store: SocialScheduleStore, catalog: dict[str, dict[str, Any]] | None = None
) -> dict[str, list[str]]:
    """Relógio que ganhou REAL_PHOTO: o Creative Agent refaz a arte com a foto.

    Enquanto a arte nova não substituir a mídia (``edit_item(media=...)``), o
    item segue bloqueado com ``AGUARDA_ARTE_COM_FOTO``.
    """
    catalog = catalog if catalog is not None else _catalog()
    result: dict[str, list[str]] = {"awaiting_creative": [], "still_blocked": []}
    for item in store.items(status="BLOCKED"):
        reasons = block_reasons({**item, "category": item["category"]}, catalog)
        if "NEEDS_REAL_PHOTO" not in (item["block_reason"] or "") or reasons:
            result["still_blocked"].append(item["id"])
            continue
        store.set_status(item["id"], "BLOCKED", block_reason="AGUARDA_ARTE_COM_FOTO")
        store.event(
            item["id"],
            "CREATIVE_RERENDER_REQUESTED",
            {"agent": "creative-agent", "products": item["products"]},
        )
        result["awaiting_creative"].append(item["id"])
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
                "items": [
                    {
                        "id": item["id"],
                        "scheduled_at": item["scheduled_at"],
                        "format": item["format"],
                        "channels": item["channels"],
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
    def publish(self, item: dict[str, Any]) -> dict[str, Any]: ...


MediaChecker = Callable[[str], tuple[int, str]]


def http_media_check(url: str) -> tuple[int, str]:
    import requests

    response = requests.get(url, stream=True, timeout=30)
    try:
        return response.status_code, response.headers.get("content-type", "")
    finally:
        response.close()


def media_problems(item: dict[str, Any], check: MediaChecker) -> list[str]:
    from urllib.parse import urlsplit

    from integrations.meta_content_publisher import quarantined_media_paths

    quarantined = quarantined_media_paths()
    problems = []
    for url in item["media"]:
        if urlsplit(url).path in quarantined:
            problems.append(f"quarentena: {url}")
            continue
        status, content_type = check(url)
        if status != 200 or "image/jpeg" not in content_type:
            problems.append(f"{url}: {status} {content_type}")
    return problems


def run_due(
    store: SocialScheduleStore,
    *,
    publisher: Publisher | None,
    approval_status: Callable[[str], str | None],
    enabled: bool | None = None,
    media_check: MediaChecker = http_media_check,
    alert: Callable[[dict[str, Any]], Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _now()
    enabled = env_flag("SOCIAL_PUBLISH_ENABLED") if enabled is None else enabled
    synced = sync_approvals(store, approval_status)
    due = [
        item
        for item in store.items(status="APPROVED")
        if item["scheduled_at"] <= now.isoformat()
        and (not item["next_attempt_at"] or item["next_attempt_at"] <= now.isoformat())
        and item["status"] != "BLOCKED"
    ]
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
                *([] if publisher else ["META_CREDENTIALS_MISSING"]),
            ],
            "approvals": synced,
            "would_publish": preview,
            "external_write": False,
        }
    published, failed = [], []
    for item in due:
        if not store.claim(item["id"]):
            continue
        previous = store.publication(item["id"], item["content_hash"])
        if previous is not None:  # idempotência: mesmo id e mesmo conteúdo
            store.set_status(item["id"], "PUBLISHED")
            published.append(item["id"])
            continue
        problems = media_problems(item, media_check)
        if problems:
            store.set_status(
                item["id"], "BLOCKED", block_reason="; ".join(problems)[:500]
            )
            store.event(item["id"], "MEDIA_CHECK_FAILED", {"problems": problems})
            failed.append({"id": item["id"], "error": "MEDIA_CHECK_FAILED"})
            continue
        try:
            result = publisher.publish(item)
        except Exception as exc:  # noqa: BLE001 - uma falha não para o lote
            attempts = int(item["attempts"]) + 1
            error = type(exc).__name__
            if attempts >= MAX_ATTEMPTS:
                store.set_status(
                    item["id"], "FAILED", attempts=attempts, last_error=error
                )
                store.event(item["id"], "PUBLISH_FAILED", {"error": error})
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
                )
            failed.append({"id": item["id"], "error": error, "attempts": attempts})
            continue
        store.record_publication(item, result)
        permalink = (result.get("instagram") or {}).get("permalink") or (
            result.get("facebook") or {}
        ).get("permalink")
        store.set_status(
            item["id"],
            "PUBLISHED",
            published_json=json.dumps(result),
            permalink=permalink,
            published_at=now.isoformat(),
        )
        store.event(item["id"], "PUBLISHED", result)
        published.append(item["id"])
    return {
        "status": "RAN",
        "approvals": synced,
        "published": published,
        "failed": failed,
        "external_write": bool(published),
    }


class MetaSocialPublisher:
    """Instagram (imagem, carrossel e story) e Página do Facebook, com leitura de volta."""

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

    def publish(self, item: dict[str, Any]) -> dict[str, Any]:
        from integrations import meta_content_publisher as meta

        result: dict[str, Any] = {}
        payload = {
            "type": item["format"],
            "caption": item["caption"],
            "alt_text": item["alt_text"],
            "media_urls": item["media"],
        }
        if "instagram" in item["channels"]:
            container = meta.create_social_container(
                self.session, payload, self.ig_user_id, self.access_token
            )
            meta.wait_until_ready(self.session, container, self.access_token)
            media_id = meta.publish_container(
                self.session, container, self.ig_user_id, self.access_token
            )
            readback = meta.read_publication(self.session, media_id, self.access_token)
            result["instagram"] = {
                "id": media_id,
                "permalink": readback.get("permalink"),
            }
        if "facebook" in item["channels"] and self.page_id:
            if item["format"] == "STORY":
                result["facebook"] = {"status": "NEEDS_MANUAL"}
            else:
                post_id = meta.publish_facebook_page(
                    self.session, payload, self.page_id, self.access_token
                )
                result["facebook"] = {
                    "id": post_id,
                    "permalink": meta.read_facebook_permalink(
                        self.session, post_id, self.access_token
                    ),
                }
        if "tiktok" in item["channels"]:
            result["tiktok"] = TikTokDraftAdapter().upload_draft(item)
        return result


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


def collect_insights(
    store: SocialScheduleStore,
    fetch: Callable[[str], dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Lê alcance, salvamentos, comentários e compartilhamentos em +24 h e +7 dias."""
    now = now or _now()
    saved = {(row["item_id"], row["checkpoint"]) for row in store.insights()}
    collected = []
    for item in store.items(status="PUBLISHED"):
        media_id = (item["published"].get("instagram") or {}).get("id")
        if not media_id or not item["published_at"]:
            continue
        published_at = datetime.fromisoformat(item["published_at"])
        for checkpoint, delay in INSIGHT_CHECKPOINTS:
            if now - published_at < delay or (item["id"], checkpoint) in saved:
                continue
            metrics = fetch(media_id)
            store.save_insight(item["id"], checkpoint, media_id, metrics)
            collected.append({"id": item["id"], "checkpoint": checkpoint, **metrics})
    return collected


def graph_insights_fetcher(access_token: str, session: Any = None) -> Callable:
    import requests

    from .meta_webhooks import graph_api_version

    session = session or requests.Session()

    def fetch(media_id: str) -> dict[str, Any]:
        response = session.get(
            f"https://graph.facebook.com/{graph_api_version()}/{media_id}/insights",
            params={
                "metric": "reach,saved,comments,shares",
                "access_token": access_token,
            },
            timeout=30,
        )
        response.raise_for_status()
        return {
            entry["name"]: (entry.get("values") or [{}])[0].get("value")
            for entry in response.json().get("data") or []
        }

    return fetch


def weekly_report(store: SocialScheduleStore, week: str) -> dict[str, Any]:
    """Resumo da semana para o Social propor a seguinte."""
    items = store.items(week=week)
    insights = {(row["item_id"], row["checkpoint"]): row for row in store.insights()}
    rows = []
    for item in items:
        best = insights.get((item["id"], "7d")) or insights.get((item["id"], "24h"))
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


def insights_csv(store: SocialScheduleStore) -> str:
    """No formato de social/medicao.csv (para colar na tabela de medição)."""
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
    items = {item["id"]: item for item in store.items()}
    for row in store.insights():
        item = items.get(row["item_id"]) or {}
        writer.writerow(
            [
                row["at"][:10],
                "Instagram",
                item.get("format", ""),
                row["item_id"],
                row["reach"],
                "",
                "",
                "",
                row["saved"],
                row["shares"],
                row["comments"],
                "",
                "",
                "",
                "",
                f"checkpoint {row['checkpoint']}",
            ]
        )
    return output.getvalue()


# -- CLI --------------------------------------------------------------------------


def store_from_env() -> SocialScheduleStore:
    from .config import AgentSettings

    settings = AgentSettings.from_env()
    return SocialScheduleStore(settings.operations_db, settings.database_url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m veratus_agents.social_scheduler")
    sub = parser.add_subparsers(dest="command", required=True)
    importer = sub.add_parser("import", help="importa o calendário JSON")
    importer.add_argument("path", nargs="?", default=str(DEFAULT_CALENDAR))
    approval = sub.add_parser("request-approval", help="um pedido por semana")
    approval.add_argument("--week")
    sub.add_parser("run-due", help="publica os aprovados vencidos (ou mostra a prévia)")
    sub.add_parser("refresh-blocks", help="relógios que ganharam foto real")
    sub.add_parser("insights", help="lê métricas em +24 h e +7 dias")
    report = sub.add_parser("report", help="relatório semanal")
    report.add_argument("--week", required=True)
    listing = sub.add_parser("list", help="lista os itens")
    listing.add_argument("--status")
    args = parser.parse_args(argv)
    store = store_from_env()
    try:
        if args.command == "import":
            output: Any = import_calendar(store, args.path)
        elif args.command == "list":
            output = store.items(status=args.status)
        elif args.command == "refresh-blocks":
            output = refresh_blocks(store)
        elif args.command == "report":
            output = weekly_report(store, args.week)
        else:
            from .operations import CommandEngine, approval_status, runtime_persistence

            engine = CommandEngine(persistence_path=runtime_persistence())
            if args.command == "request-approval":
                output = request_weekly_approvals(store, engine, week=args.week)
            elif args.command == "run-due":
                output = run_due(
                    store,
                    publisher=MetaSocialPublisher.from_env(),
                    approval_status=lambda request_id: approval_status(
                        engine, request_id
                    ),
                )
            else:
                import os

                token = os.getenv("META_PAGE_ACCESS_TOKEN", "").strip()
                if not token:
                    print(json.dumps({"error": "META_PAGE_ACCESS_TOKEN ausente"}))
                    return 2
                output = collect_insights(store, graph_insights_fetcher(token))
    except CalendarError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
