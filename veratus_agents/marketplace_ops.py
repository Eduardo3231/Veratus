from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Any

from .marketplace_adapters import Marketplace
from .product_master import ProductStatus


class ChannelState(StrEnum):
    DISABLED = "DISABLED"
    CONFIGURED = "CONFIGURED"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"


class ListingState(StrEnum):
    NOT_CREATED = "NOT_CREATED"
    DRAFT = "DRAFT"
    READY = "READY"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    PAUSED = "PAUSED"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    UPDATE_PENDING = "UPDATE_PENDING"
    SYNC_CONFLICT = "SYNC_CONFLICT"
    FAILED = "FAILED"
    REMOVED = "REMOVED"
    BLOCKED = "BLOCKED"


class CredentialStatus(StrEnum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    INVALID = "INVALID"
    NOT_TESTED = "NOT_TESTED"
    HEALTHY = "HEALTHY"


class CategoryMappingStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    CATEGORY_DISCOVERY_BLOCKED_BY_CREDENTIALS = (
        "CATEGORY_DISCOVERY_BLOCKED_BY_CREDENTIALS"
    )
    API_CONTRACT_UNVERIFIED = "API_CONTRACT_UNVERIFIED"


class Readiness(StrEnum):
    NOT_READY = "NOT_READY"
    PARTIAL = "PARTIAL"
    READY_FOR_DRAFT = "READY_FOR_DRAFT"
    READY_FOR_PUBLISH = "READY_FOR_PUBLISH"


class MarketplaceValidationError(ValueError):
    def __init__(
        self,
        channel: str,
        sku: str,
        missing_fields: list[str],
        invalid_fields: list[str] | None = None,
    ):
        self.channel = channel
        self.sku = sku
        self.missing_fields = missing_fields
        self.invalid_fields = invalid_fields or []
        super().__init__(f"{channel}:{sku}: campos ausentes ou inválidos")


@dataclass(frozen=True)
class MarketplaceChannel:
    id: str
    name: str
    state: ChannelState
    enabled: bool
    connected: bool
    publish_enabled: bool
    capabilities: tuple[str, ...]
    required_credentials: tuple[str, ...]


@dataclass(frozen=True)
class MarketplaceAccount:
    channel: str
    account_id: str | None
    store_id: str | None
    region: str
    currency: str
    enabled: bool
    credential_reference: str | None
    connected: bool
    last_health_check: str | None = None
    credential_status: CredentialStatus = CredentialStatus.MISSING
    client_status: str = "API_CONTRACT_UNVERIFIED"


@dataclass(frozen=True)
class MarketplaceListing:
    id: str
    sku: str
    channel: str
    state: ListingState
    external_listing_id: str | None
    external_product_id: str | None
    local_version: int
    remote_version: int | None
    last_sync_at: str | None
    last_publish_at: str | None
    last_error: str | None


@dataclass(frozen=True)
class SyncConflict:
    type: str
    sku: str
    channel: str
    local_value: str
    remote_value: str
    detected_at: str
    recommendation: str


CHANNELS = {
    Marketplace.MERCADO_LIVRE.value: MarketplaceChannel(
        "mercado-livre",
        "Mercado Livre",
        ChannelState.DISABLED,
        False,
        False,
        False,
        ("draft", "read"),
        ("MERCADO_LIVRE_ACCESS_TOKEN",),
    ),
    Marketplace.SHOPEE.value: MarketplaceChannel(
        "shopee",
        "Shopee",
        ChannelState.DISABLED,
        False,
        False,
        False,
        ("draft", "read"),
        ("SHOPEE_ACCESS_TOKEN",),
    ),
    Marketplace.TIKTOK_SHOP.value: MarketplaceChannel(
        "tiktok-shop",
        "TikTok Shop",
        ChannelState.DISABLED,
        False,
        False,
        False,
        ("draft", "read"),
        ("TIKTOK_SHOP_ACCESS_TOKEN",),
    ),
    Marketplace.META.value: MarketplaceChannel(
        "meta",
        "Meta",
        ChannelState.DISABLED,
        False,
        False,
        False,
        ("catalog", "draft", "read"),
        ("META_ACCESS_TOKEN",),
    ),
    Marketplace.AMAZON.value: MarketplaceChannel(
        "amazon",
        "Amazon",
        ChannelState.DISABLED,
        False,
        False,
        False,
        ("draft", "read"),
        ("AMAZON_SP_REFRESH_TOKEN",),
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def channel_readiness(
    channel: MarketplaceChannel | dict[str, Any] | None,
    product: dict[str, Any],
    publish_approval: bool = False,
) -> Readiness:
    if channel is None or product.get("status") != ProductStatus.APPROVED.value:
        return Readiness.NOT_READY
    missing = [
        field
        for field in ("price_brl", "availability", "images")
        if not product.get(field)
    ]
    if missing:
        return Readiness.PARTIAL
    enabled = (
        channel.enabled
        if isinstance(channel, MarketplaceChannel)
        else channel.get("enabled", False)
    )
    connected = (
        channel.connected
        if isinstance(channel, MarketplaceChannel)
        else channel.get("connected", False)
    )
    publish_enabled = (
        channel.publish_enabled
        if isinstance(channel, MarketplaceChannel)
        else channel.get("publish_enabled", False)
    )
    if not enabled:
        return Readiness.NOT_READY
    if not connected:
        return Readiness.READY_FOR_DRAFT
    return (
        Readiness.READY_FOR_PUBLISH
        if publish_enabled and publish_approval
        else Readiness.READY_FOR_DRAFT
    )


class MarketplaceStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.locks: dict[str, Lock] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS marketplace_channels (channel TEXT PRIMARY KEY, data_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS marketplace_accounts (channel TEXT PRIMARY KEY, data_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS marketplace_listings (id TEXT PRIMARY KEY, sku TEXT NOT NULL, channel TEXT NOT NULL, data_json TEXT NOT NULL, UNIQUE(sku, channel));
            CREATE TABLE IF NOT EXISTS marketplace_overrides (sku TEXT NOT NULL, channel TEXT NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(sku, channel, action));
            CREATE TABLE IF NOT EXISTS marketplace_idempotency (key TEXT PRIMARY KEY, result_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS marketplace_events (event_id TEXT PRIMARY KEY, action TEXT NOT NULL, target TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS marketplace_approvals (request_id TEXT PRIMARY KEY, data_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS marketplace_category_mappings (channel TEXT NOT NULL, internal_category TEXT NOT NULL, external_category TEXT, status TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(channel, internal_category));
            CREATE TABLE IF NOT EXISTS marketplace_sync_conflicts (conflict_id TEXT PRIMARY KEY, type TEXT NOT NULL, sku TEXT NOT NULL, channel TEXT NOT NULL, data_json TEXT NOT NULL, resolved_at TEXT);
            CREATE TABLE IF NOT EXISTS marketplace_connection_checks (channel TEXT PRIMARY KEY, data_json TEXT NOT NULL, checked_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS marketplace_category_details (channel TEXT NOT NULL, internal_category TEXT NOT NULL, data_json TEXT NOT NULL, discovered_at TEXT NOT NULL, PRIMARY KEY(channel, internal_category));
            CREATE TABLE IF NOT EXISTS marketplace_attribute_mappings (channel TEXT NOT NULL, external_category_id TEXT NOT NULL, attribute_id TEXT NOT NULL, data_json TEXT NOT NULL, discovered_at TEXT NOT NULL, PRIMARY KEY(channel, external_category_id, attribute_id));
            CREATE TABLE IF NOT EXISTS marketplace_publish_plans (plan_id TEXT PRIMARY KEY, data_json TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
            for channel, definition in CHANNELS.items():
                db.execute(
                    "INSERT OR IGNORE INTO marketplace_channels VALUES (?, ?)",
                    (
                        channel,
                        json.dumps(
                            asdict(definition), default=lambda value: value.value
                        ),
                    ),
                )
            db.commit()

    def channels(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT data_json FROM marketplace_channels ORDER BY channel"
            ).fetchall()
        result = []
        for row in rows:
            item = json.loads(row[0])
            prefix = item["id"].upper().replace("-", "_")
            item["enabled"] = os.getenv(f"{prefix}_ENABLED", "false").lower() == "true"
            item["publish_enabled"] = (
                os.getenv(f"{prefix}_PUBLISH_ENABLED", "false").lower() == "true"
            )
            item["state"] = (
                ChannelState.CONFIGURED.value
                if item["enabled"]
                else ChannelState.DISABLED.value
            )
            result.append(item)
        return result

    def channel(self, channel: str) -> dict[str, Any] | None:
        return next((item for item in self.channels() if item["id"] == channel), None)

    def account(self, channel: str) -> MarketplaceAccount:
        raw = os.getenv(f"{channel.upper().replace('-', '_')}_ACCOUNT_ID")
        store = os.getenv(f"{channel.upper().replace('-', '_')}_STORE_ID")
        credential = (
            f"{channel.upper().replace('-', '_')}_ACCESS_TOKEN"
            if os.getenv(f"{channel.upper().replace('-', '_')}_ACCESS_TOKEN")
            else None
        )
        credential_status = (
            CredentialStatus.NOT_TESTED if credential else CredentialStatus.MISSING
        )
        # Identifiers are intentionally reduced to presence markers in this public
        # operational view. Their values stay in the environment/secret store.
        return MarketplaceAccount(
            channel,
            "PRESENT" if raw else None,
            "PRESENT" if store else None,
            os.getenv("VERATUS_REGION", "BR"),
            "BRL",
            bool(raw),
            credential,
            False,
            None,
            credential_status,
            "API_CONTRACT_UNVERIFIED",
        )

    def upsert_category_mapping(
        self,
        channel: str,
        internal_category: str,
        *,
        external_category: str | None = None,
        status: CategoryMappingStatus | None = None,
    ) -> dict[str, Any]:
        if status is None:
            status = (
                CategoryMappingStatus.DISCOVERED
                if external_category
                else CategoryMappingStatus.CATEGORY_DISCOVERY_BLOCKED_BY_CREDENTIALS
            )
        data = {
            "channel": channel,
            "internal_category": internal_category,
            "external_category": external_category,
            "status": status.value,
            "updated_at": _now(),
        }
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO marketplace_category_mappings VALUES (?, ?, ?, ?, ?)",
                (
                    channel,
                    internal_category,
                    external_category,
                    status.value,
                    data["updated_at"],
                ),
            )
            db.commit()
        return data

    def category_mappings(self, channel: str | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT channel, internal_category, external_category, status, updated_at FROM marketplace_category_mappings"
            + (" WHERE channel=?" if channel else "")
        )
        with sqlite3.connect(self.path) as db:
            rows = db.execute(query, (channel,) if channel else ()).fetchall()
        return [
            {
                "channel": row[0],
                "internal_category": row[1],
                "external_category": row[2],
                "status": row[3],
                "updated_at": row[4],
            }
            for row in rows
        ]

    def save_connection_check(
        self, channel: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        safe = dict(data)
        checked_at = _now()
        safe["checked_at"] = checked_at
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO marketplace_connection_checks VALUES (?, ?, ?)",
                (channel, json.dumps(safe), checked_at),
            )
            db.commit()
        self.event(
            "READ_ONLY_CONNECTION_CHECK", channel, {"status": safe.get("readiness")}
        )
        return safe

    def connection_checks(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT data_json FROM marketplace_connection_checks ORDER BY channel"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_category_details(
        self, channel: str, internal_category: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        details = dict(data)
        discovered_at = _now()
        details.update(
            {
                "channel": channel,
                "internal_category": internal_category,
                "discovered_at": discovered_at,
            }
        )
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO marketplace_category_details VALUES (?, ?, ?, ?)",
                (channel, internal_category, json.dumps(details), discovered_at),
            )
            db.commit()
        return details

    def category_details(self, channel: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT data_json FROM marketplace_category_details" + (
            " WHERE channel=?" if channel else ""
        )
        with sqlite3.connect(self.path) as db:
            rows = db.execute(query, (channel,) if channel else ()).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_attributes(
        self, channel: str, external_category_id: str, attributes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        discovered_at = _now()
        with sqlite3.connect(self.path) as db:
            for attribute in attributes:
                attribute_id = str(attribute.get("id") or "")
                if not attribute_id:
                    continue
                data = {
                    **attribute,
                    "channel": channel,
                    "external_category_id": external_category_id,
                    "discovered_at": discovered_at,
                }
                db.execute(
                    "INSERT OR REPLACE INTO marketplace_attribute_mappings VALUES (?, ?, ?, ?, ?)",
                    (
                        channel,
                        external_category_id,
                        attribute_id,
                        json.dumps(data),
                        discovered_at,
                    ),
                )
            db.commit()
        return self.attributes(channel, external_category_id)

    def attributes(
        self, channel: str | None = None, external_category_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[str] = []
        if channel:
            clauses.append("channel=?")
            params.append(channel)
        if external_category_id:
            clauses.append("external_category_id=?")
            params.append(external_category_id)
        query = "SELECT data_json FROM marketplace_attribute_mappings"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with sqlite3.connect(self.path) as db:
            rows = db.execute(query, params).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_publish_plan(self, data: dict[str, Any]) -> dict[str, Any]:
        plan = dict(data)
        plan_id = str(plan.get("plan_id") or f"plan_{os.urandom(8).hex()}")
        plan["plan_id"] = plan_id
        plan["created_at"] = _now()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO marketplace_publish_plans VALUES (?, ?, ?)",
                (plan_id, json.dumps(plan), plan["created_at"]),
            )
            db.commit()
        return plan

    def publish_plans(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT data_json FROM marketplace_publish_plans ORDER BY created_at"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def upsert_listing(
        self,
        sku: str,
        channel: str,
        state: ListingState,
        *,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        listing_id = f"listing_{channel}_{sku}"
        data = {
            "id": listing_id,
            "sku": sku,
            "channel": channel,
            "state": state.value,
            "external_listing_id": None,
            "external_product_id": None,
            "local_version": 1,
            "remote_version": None,
            "last_sync_at": None,
            "last_publish_at": None,
            "last_error": error,
            "payload": payload or {},
        }
        with sqlite3.connect(self.path) as db:
            previous = db.execute(
                "SELECT data_json FROM marketplace_listings WHERE sku=? AND channel=?",
                (sku, channel),
            ).fetchone()
            if previous:
                old = json.loads(previous[0])
                data.update(old)
                data["state"] = state.value
                data["local_version"] = old.get("local_version", 0) + 1
                data["last_error"] = error
                data["payload"] = (
                    payload if payload is not None else old.get("payload", {})
                )
            db.execute(
                "INSERT OR REPLACE INTO marketplace_listings VALUES (?, ?, ?, ?)",
                (listing_id, sku, channel, json.dumps(data)),
            )
            db.commit()
        return data

    def set_remote_ids(
        self, channel: str, sku: str, remote_ids: list[Any]
    ) -> dict[str, Any] | None:
        """Ids do anúncio no canal (leitura de volta) no listing do SKU.

        Anúncio que só existe no canal ganha um listing ``NOT_CREATED``.
        """
        ids = [str(item) for item in remote_ids if item]
        current = self.listing(channel, sku)
        if current is None:
            if not ids:
                return None
            current = self.upsert_listing(sku, channel, ListingState.NOT_CREATED)
        data = {
            **current,
            "external_listing_id": ids[0] if ids else None,
            "remote_ids": ids,
            "last_sync_at": _now(),
        }
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE marketplace_listings SET data_json=? WHERE sku=? AND channel=?",
                (json.dumps(data), sku, channel),
            )
            db.commit()
        return data

    def listings(self, channel: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT data_json FROM marketplace_listings" + (
            " WHERE channel=?" if channel else ""
        )
        with sqlite3.connect(self.path) as db:
            rows = db.execute(query, (channel,) if channel else ()).fetchall()
        return [json.loads(row[0]) for row in rows]

    def listing(self, channel: str, sku: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.listings(channel) if item["sku"] == sku), None
        )

    def override(self, sku: str, channel: str, action: str, reason: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO marketplace_overrides VALUES (?, ?, ?, ?, ?)",
                (sku, channel, action, reason, _now()),
            )
            db.commit()

    def is_overridden(self, sku: str, channel: str, action: str) -> bool:
        with sqlite3.connect(self.path) as db:
            return (
                db.execute(
                    "SELECT 1 FROM marketplace_overrides WHERE sku=? AND channel=? AND action=?",
                    (sku, channel, action),
                ).fetchone()
                is not None
            )

    def remember(self, key: str, result: dict[str, Any]) -> dict[str, Any]:
        with sqlite3.connect(self.path) as db:
            existing = db.execute(
                "SELECT result_json FROM marketplace_idempotency WHERE key=?", (key,)
            ).fetchone()
            if existing:
                return json.loads(existing[0])
            db.execute(
                "INSERT INTO marketplace_idempotency VALUES (?, ?)",
                (key, json.dumps(result)),
            )
            db.commit()
        return result

    def recalled(self, key: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as db:
            existing = db.execute(
                "SELECT result_json FROM marketplace_idempotency WHERE key=?", (key,)
            ).fetchone()
        return json.loads(existing[0]) if existing else None

    def event(self, action: str, target: str, data: dict[str, Any]) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO marketplace_events VALUES (?, ?, ?, ?, ?)",
                (
                    f"event_{os.urandom(8).hex()}",
                    action,
                    target,
                    json.dumps(data),
                    _now(),
                ),
            )
            db.commit()

    def audit(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT event_id, action, target, data_json, created_at FROM marketplace_events ORDER BY created_at"
            ).fetchall()
        return [
            {
                "event_id": row[0],
                "action": row[1],
                "target": row[2],
                "data": json.loads(row[3]),
                "created_at": row[4],
            }
            for row in rows
        ]

    def record_sync_conflict(self, conflict: SyncConflict) -> dict[str, Any]:
        data = asdict(conflict)
        conflict_id = f"{conflict.type}:{conflict.channel}:{conflict.sku}"
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO marketplace_sync_conflicts VALUES (?, ?, ?, ?, ?, NULL)",
                (
                    conflict_id,
                    conflict.type,
                    conflict.sku,
                    conflict.channel,
                    json.dumps(data),
                ),
            )
            db.commit()
        self.event(
            "SYNC_CONFLICT_DETECTED",
            conflict.sku,
            {"channel": conflict.channel, "type": conflict.type},
        )
        return {"conflict_id": conflict_id, **data}

    def sync_conflicts(self, *, unresolved_only: bool = True) -> list[dict[str, Any]]:
        query = (
            "SELECT conflict_id, data_json, resolved_at FROM marketplace_sync_conflicts"
            + (" WHERE resolved_at IS NULL" if unresolved_only else "")
        )
        with sqlite3.connect(self.path) as db:
            rows = db.execute(query).fetchall()
        return [
            {"conflict_id": row[0], **json.loads(row[1]), "resolved_at": row[2]}
            for row in rows
        ]

    def resolve_sync_conflicts(self, channel: str, sku: str, types: list[str]) -> int:
        """Conflito que a leitura de volta não mostra mais fica resolvido."""
        marks = ",".join("?" for _ in types) or "NULL"
        with sqlite3.connect(self.path) as db:
            count = db.execute(
                "UPDATE marketplace_sync_conflicts SET resolved_at=? WHERE channel=? "
                f"AND sku=? AND resolved_at IS NULL AND type IN ({marks})",
                (_now(), channel, sku, *types),
            ).rowcount
            db.commit()
        return count

    def record_readback(
        self,
        channel: str,
        sku: str,
        *,
        state: ListingState,
        remote: dict[str, Any],
        published: bool = False,
    ) -> dict[str, Any]:
        """Leitura de volta depois de uma escrita no canal (``readback_listing``)."""
        current = self.listing(channel, sku) or self.upsert_listing(sku, channel, state)
        data = readback_listing(
            current, state=state, remote=remote, published=published
        )
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE marketplace_listings SET data_json=? WHERE sku=? AND channel=?",
                (json.dumps(data), sku, channel),
            )
            db.commit()
        return data

    def request_approval(
        self, channel: str, sku: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        request = {
            "request_id": f"publish_{channel}_{sku}",
            "type": "PUBLISH_APPROVAL",
            "channel": channel,
            "sku": sku,
            "payload_preview": payload,
            "status": "PENDING",
            "requested_by": "distribution-supervisor",
            "created_at": _now(),
        }
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO marketplace_approvals VALUES (?, ?)",
                (request["request_id"], json.dumps(request)),
            )
            row = db.execute(
                "SELECT data_json FROM marketplace_approvals WHERE request_id=?",
                (request["request_id"],),
            ).fetchone()
            db.commit()
        return json.loads(row[0])

    def resolve_approval(self, request_id: str, status: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT data_json FROM marketplace_approvals WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if not row:
                return None
            data = json.loads(row[0])
            data["status"] = status
            data["resolved_at"] = _now()
            db.execute(
                "UPDATE marketplace_approvals SET data_json=? WHERE request_id=?",
                (json.dumps(data), request_id),
            )
            db.commit()
        self.event("APPROVAL_RESOLVED", request_id, {"status": status})
        return data

    def approvals(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT data_json FROM marketplace_approvals ORDER BY request_id"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]


class ExternalWriteGuard:
    def __init__(self, store: MarketplaceStore):
        self.store = store

    def check(
        self,
        channel: str,
        product: dict[str, Any],
        *,
        approval: ApprovalRequestLike | None = None,
        agent: str | None = None,
        publish_enabled: bool = False,
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        definition = self.store.channel(channel)
        if os.getenv("MARKETPLACE_MODE", "LOCAL").upper() != "LIVE":
            reasons.append("MARKETPLACE_MODE_LOCAL")
        if (
            not publish_enabled
            or os.getenv("PUBLISH_ENABLED", "false").lower() != "true"
        ):
            reasons.append("GLOBAL_PUBLISH_DISABLED")
        if not definition or not definition.get("enabled"):
            reasons.append("CHANNEL_DISABLED")
        if not definition or not definition.get("publish_enabled"):
            reasons.append("CHANNEL_PUBLISH_DISABLED")
        if product.get("status") != ProductStatus.APPROVED.value:
            reasons.append("PRODUCT_NOT_APPROVED")
        try:
            from .operations import AGENT_REGISTRY, OperationalRuntime

            # Sem agente, vale o agente do canal; id fora do registro não publica.
            agent = agent or OperationalRuntime.CHANNEL_AGENTS.get(channel, "")
            registered = AGENT_REGISTRY.get(agent)
            if registered is None or "PUBLISH" not in registered.permissions:
                reasons.append("AGENT_PUBLISH_PERMISSION_REQUIRED")
        except ImportError:
            reasons.append("AGENT_REGISTRY_UNAVAILABLE")
        if approval is None or getattr(approval, "status", None) != "APPROVED":
            reasons.append("PUBLISH_APPROVAL_REQUIRED")
        if self.store.is_overridden(product.get("sku", ""), channel, "PUBLISH"):
            reasons.append("CEO_OVERRIDE_BLOCK")
        from .commercial_config import watch_channel_blockers

        reasons += [
            f"WATCH_INFO_PENDING:{code}"
            for code in watch_channel_blockers(product, channel)
        ]
        return not reasons, reasons


class ApprovalRequestLike:
    status: str


def readback_listing(
    current: dict[str, Any],
    *,
    state: ListingState,
    remote: dict[str, Any],
    published: bool = False,
) -> dict[str, Any]:
    """Listing depois da escrita confirmada pelo GET do canal.

    Grava o id remoto, o status lido e ``remote_version``, que conta as escritas
    confirmadas (o Mercado Livre não expõe versão do anúncio); ``last_publish_at``
    só na criação.
    """
    item_id = str(remote["item_id"])
    now = _now()
    others = [ref for ref in current.get("remote_ids") or [] if ref != item_id]
    data = {
        **current,
        "state": state.value,
        "external_listing_id": item_id,
        "remote_ids": [item_id, *others],
        "remote_version": int(current.get("remote_version") or 0) + 1,
        "remote_status": remote.get("status"),
        "last_sync_at": now,
        "last_error": None,
    }
    if published:
        data["last_publish_at"] = now
    return data


def validate_category(channel: str, product: dict[str, Any]) -> None:
    if not product.get("category"):
        raise MarketplaceValidationError(
            channel, product.get("sku", ""), ["category_mapping"]
        )


def prepare_listing(
    store: MarketplaceStore,
    product: dict[str, Any],
    channel: Marketplace,
    payload: dict[str, Any],
) -> dict[str, Any]:
    validate_category(channel.value, product)
    if store.is_overridden(product["sku"], channel.value, "PUBLISH"):
        result = store.upsert_listing(
            product["sku"],
            channel.value,
            ListingState.BLOCKED,
            payload=payload,
            error="BLOCKED_BY_CEO",
        )
        store.event(
            "DRAFT_BLOCKED_BY_CEO",
            product["sku"],
            {"channel": channel.value, "external_write": False},
        )
        return result
    from .commercial_config import is_watch, watch_channel_blockers

    if is_watch(product):
        from .watch_sheet import default_sheet, model_id

        sheet = default_sheet()
        if sheet is not None:
            # O anúncio já sai com o que o fundador confirmou na ficha.
            payload = {
                **payload,
                "ficha": sheet.listing_attributes(model_id(product)),
            }
    # Relógio sobe: o rascunho é preparado já, e o que falta (foto real,
    # envio do Brasil, campos da ficha) fica listado até o fundador preencher.
    # Publicar continua bloqueado pelo ExternalWriteGuard enquanto faltar algo.
    if pending := watch_channel_blockers(product, channel.value):
        result = store.upsert_listing(
            product["sku"],
            channel.value,
            ListingState.DRAFT,
            payload={**payload, "pending_info": pending},
            error="PENDENTE:" + ",".join(pending),
        )
        store.event(
            "DRAFT_PENDING_INFO",
            product["sku"],
            {"channel": channel.value, "pending": pending, "external_write": False},
        )
        return result
    key = f"{channel.value}:{product['sku']}:CREATE_DRAFT:{product.get('updated_at', '1')}"
    existing = store.listing(channel.value, product["sku"])
    remembered = store.recalled(key)
    if remembered:
        return remembered
    result = store.remember(
        key,
        store.upsert_listing(
            product["sku"], channel.value, ListingState.DRAFT, payload=payload
        ),
    )
    if existing is None:
        store.event(
            "DRAFT_CREATED",
            product["sku"],
            {"channel": channel.value, "external_write": False},
        )
    return result


def make_marketplace_store(
    path: str | Path, database_url: str | None = None
) -> MarketplaceStore:
    if database_url:
        from .postgres_marketplace import PostgresMarketplaceStore

        return PostgresMarketplaceStore(database_url)  # type: ignore[return-value]
    return MarketplaceStore(path)


# Sem custo: ele fica no Product Master (banco), nunca no código.
FOUNDER_CONFIRMED_DEFAULTS: dict[str, Any] = {
    "price_brl": "289.90",
    "material": "stainless_steel",
    "category": "accessories",
    "subcategory": "watches",
    "inventory_mode": "ON_DEMAND",
    "channel_stock_cap": 20,
    "handling_time_business_days": 2,
    "package_weight_g": 350,
    "package_length_cm": 18,
    "package_width_cm": 14,
    "package_height_cm": 10,
}

ESTIMATED_PRODUCT_DIMENSIONS: dict[str, Any] = {
    "watch_case_diameter_cm": 4.1,
    "watch_case_thickness_cm": 1.25,
    "watch_lug_to_lug_cm": 4.8,
    "watch_bracelet_width_cm": 2.0,
    "watch_open_length_cm": 21.0,
    "product_length_cm": 4.8,
    "product_width_cm": 4.1,
    "product_height_cm": 1.25,
}


def apply_founder_confirmed_defaults(
    product: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fill missing shared facts while preserving every SKU-specific confirmed value."""
    merged = dict(product)
    conflicts: list[dict[str, Any]] = []
    sources = dict(merged.get("field_sources", {}))
    for field, value in FOUNDER_CONFIRMED_DEFAULTS.items():
        current = merged.get(field)
        if current not in (None, ""):
            if current != value and sources.get(field) in {
                "FOUNDER_CONFIRMED",
                "PHYSICALLY_VERIFIED",
            }:
                conflicts.append(
                    {
                        "type": "DATA_CONFLICT",
                        "field": field,
                        "existing": current,
                        "shared_default": value,
                    }
                )
            continue
        merged[field] = value
        sources[field] = "FOUNDER_CONFIRMED"
    for field, value in ESTIMATED_PRODUCT_DIMENSIONS.items():
        if merged.get(field) in (None, ""):
            merged[field] = value
            sources[field] = "ESTIMATED"
    merged["dimensions_status"] = "ESTIMATED"
    merged["physical_verification_status"] = "NOT_PHYSICALLY_VERIFIED"
    merged["supplier_visibility"] = "PRIVATE"
    merged["field_sources"] = sources
    return merged, conflicts


# -- Mercado Livre: sincronia de preço e estoque e escrita do anúncio --------------

MERCADO_LIVRE = Marketplace.MERCADO_LIVRE.value
# Diferença entre o Product Master e o anúncio lido de volta.
SYNC_FIELDS = {"price": "PRICE_MISMATCH", "available_quantity": "STOCK_MISMATCH"}
# Estado que a sincronia não troca por UPDATE_PENDING (só registra o conflito).
_KEEP_STATE = {"UPDATE_PENDING", "PAUSED", "REMOVED", "BLOCKED", "PUBLISHING"}
# Nunca no texto do anúncio: custo, fornecedor, margem.
_PRIVATE_WORDS = re.compile(
    r" (?:fornecedor\w*|supplier\w*|custo\w*|cost|margem|margin) "
)
# Uma escrita por vez neste processo: dois cliques não criam dois anúncios.
_WRITE_LOCK = Lock()
# Do catálogo público para o anúncio: preço, texto e o que ainda não foi verificado.
_PUBLIC_FIELDS = (
    "collection",
    "sale_price",
    "price_source",
    "name",
    "description",
    "unverified_attributes",
)


def public_listing_view(product: dict[str, Any]) -> dict[str, Any]:
    """O produto como o cliente vê: preço, texto e ``unverified_attributes`` da
    entrada pública do catálogo (joias de revenda incluídas) sobre o Product
    Master, que segue dono do SKU, do status e do teto do canal."""
    from .listing_content import find_catalog_entry

    entry = find_catalog_entry(product) or {}
    return {**product, **{key: entry[key] for key in _PUBLIC_FIELDS if key in entry}}


def listing_price(product: dict[str, Any]) -> str | None:
    """Preço público do anúncio. Peça que não é relógio e não tem a entrada
    pública (coleção e preço com fonte) fica sem preço: o preço dos relógios
    nunca vale para joia."""
    from .catalog import public_price
    from .commercial_config import is_watch

    if not product.get("collection") and not is_watch(product):
        return None
    return public_price(product)


def confirmed_channel_stock(product: dict[str, Any]) -> int | None:
    """Estoque que o anúncio pode mostrar, nunca acima do ``channel_stock_cap``.

    Relógio: o estoque CONFIRMADO na ficha do modelo. Peça sob encomenda
    (ON_DEMAND, como as joias de revenda): o teto do canal, que nasce 0. Sem
    nenhum dos dois, ``None``: a sincronia não mexe no estoque remoto.
    """
    from .commercial_config import is_watch

    cap = product.get("channel_stock_cap")
    cap = None if cap in (None, "") else max(0, int(cap))
    if not is_watch(product):
        return cap if product.get("inventory_mode") == "ON_DEMAND" else None
    from .watch_sheet import default_sheet, model_id

    sheet = default_sheet()
    stock = sheet.confirmed("estoque", model_id(product)) if sheet else None
    if stock is None:
        return None
    stock = max(0, int(stock))
    return stock if cap is None else min(stock, cap)


def _words(text: str) -> str:
    from .meta_webhooks import normalize_text

    return " " + " ".join(re.findall(r"[a-z0-9]+", normalize_text(text))) + " "


def listing_copy_issues(
    product: dict[str, Any], title: str, description: str
) -> list[str]:
    """Título e descrição só com o produto público.

    Barra custo, fornecedor, margem, ``supplier_ref`` e cada item que o produto
    marca em ``unverified_attributes`` (material, medidas da ficha de origem...):
    o texto que cita um deles não sobe.
    """
    issues = []
    if not title.strip() or not description.strip():
        issues.append("COPY_MISSING")
    if len(title) > 60:
        issues.append("TITLE_TOO_LONG")
    text = _words(f"{title} {description}")
    private = [
        _words(str(product[key]))
        for key in ("supplier_ref", "supplier_name", "cost_brl", "cost")
        if product.get(key) not in (None, "")
    ]
    if _PRIVATE_WORDS.search(text) or any(
        value.strip() and value in text for value in private
    ):
        issues.append("COPY_PRIVATE_DATA")
    unverified = [
        _words(str(item)) for item in product.get("unverified_attributes") or []
    ]
    if any(term.strip() and term in text for term in unverified):
        issues.append("COPY_UNVERIFIED_ATTRIBUTE")
    return issues


def _same(field: str, local: Any, remote: Any) -> bool:
    if field != "price":
        return str(local) == str(remote)
    try:
        cents = Decimal("0.01")
        return Decimal(str(local)).quantize(cents) == Decimal(str(remote)).quantize(
            cents
        )
    except (InvalidOperation, ValueError):
        return False


def plan_mercado_livre_item(
    store: MarketplaceStore,
    listing: dict[str, Any],
    product: dict[str, Any],
    remote: dict[str, Any],
    *,
    stock_of: Callable[[dict[str, Any]], int | None] | None = None,
) -> dict[str, Any]:
    """Compara o anúncio lido de volta com o preço público e o estoque confirmado.

    Diferença vira SyncConflict PRICE_MISMATCH/STOCK_MISMATCH e o listing fica
    UPDATE_PENDING; o que voltou a bater é resolvido. Estoque não confirmado fica
    fora da conta. Dry-run: só o store local muda, o canal não.
    """
    sku = listing["sku"]
    view = public_listing_view(product)
    local = {
        "price": listing_price(view),
        "available_quantity": (stock_of or confirmed_channel_stock)(view),
    }
    changes = {
        field: {"local": str(local[field]), "remote": str(remote.get(field))}
        for field in SYNC_FIELDS
        if local[field] is not None
        and not _same(field, local[field], remote.get(field))
    }
    for field, change in changes.items():
        store.record_sync_conflict(
            SyncConflict(
                SYNC_FIELDS[field],
                sku,
                MERCADO_LIVRE,
                change["local"],
                change["remote"],
                _now(),
                "aplicar pelo publish, com a aprovação do fundador",
            )
        )
    settled = [
        kind
        for field, kind in SYNC_FIELDS.items()
        if field not in changes and local[field] is not None
    ]
    if settled:
        store.resolve_sync_conflicts(MERCADO_LIVRE, sku, settled)
    state = listing["state"]
    if changes and state not in _KEEP_STATE:
        state = store.upsert_listing(sku, MERCADO_LIVRE, ListingState.UPDATE_PENDING)[
            "state"
        ]
    return {
        "sku": sku,
        "item_id": remote.get("item_id"),
        "status": "UPDATE_PENDING" if changes else "IN_SYNC",
        "changes": changes,
        "stock_confirmed": local["available_quantity"] is not None,
        "state": state,
    }


def _blocked(*reasons: str) -> dict[str, Any]:
    return {"status": "BLOCKED", "reasons": list(reasons), "external_write": False}


def mercado_livre_guard_reasons(
    store: MarketplaceStore, product: dict[str, Any]
) -> list[str]:
    """O ExternalWriteGuard do publish para o Mercado Livre (aprovação do
    fundador, papel com PUBLISH, canal e flags ligados, ficha); vazio = liberado."""
    from types import SimpleNamespace

    request_id = f"publish_{MERCADO_LIVRE}_{product.get('sku') or product.get('id')}"
    approval = next(
        (item for item in store.approvals() if item["request_id"] == request_id),
        None,
    )
    _, reasons = ExternalWriteGuard(store).check(
        MERCADO_LIVRE,
        product,
        approval=SimpleNamespace(status=approval["status"]) if approval else None,
        publish_enabled=os.getenv("PUBLISH_ENABLED", "false").lower() == "true",
    )
    return reasons


def _write_blockers(store: MarketplaceStore, writer: Any, product: dict) -> list[str]:
    """Guard e flags do cliente também aqui: quem chama a escrita sem passar pela
    rota é barrado do mesmo jeito, antes de qualquer rede."""
    reasons = [*mercado_livre_guard_reasons(store, product), *writer.blockers()]
    return list(dict.fromkeys(reasons))


def _ml_category(writer: Any, current: str, title: str) -> str:
    """Categoria confirmada do rascunho (MLB...) ou, sem ela, a do preditor do
    Mercado Livre (``domain_discovery``). A conferir na hora de ligar."""
    if current.startswith("MLB") and current[3:].isdigit():
        return current
    for item in writer.reader.discover_categories(title):
        category = str(item.get("external_category_id") or "")
        if category.startswith("MLB") and category[3:].isdigit():
            return category
    return ""


def write_mercado_livre_listing(
    store: MarketplaceStore,
    writer: Any,
    product: dict[str, Any],
    *,
    base_url: str,
    stock_of: Callable[[dict[str, Any]], int | None] | None = None,
) -> dict[str, Any]:
    """Cria ou atualiza o anúncio no Mercado Livre e grava a leitura de volta.

    Só depois do ExternalWriteGuard aprovado (rota de publish) e com as flags de
    escrita: sem elas, nada sai. Sem id remoto, cria (``POST /items``); com id,
    aplica o preço público, o estoque confirmado (nunca acima do teto do canal) e
    a descrição. Vale o ``GET /items/{id}``: id, versão remota e data no listing.
    """
    from .marketplace_clients import MarketplaceApiError, mercado_livre_item_body

    if reasons := _write_blockers(store, writer, product):
        return _blocked(*reasons)
    sku = str(product.get("sku") or product.get("id") or "")
    listing = store.listing(MERCADO_LIVRE, sku)
    view = public_listing_view(product)
    price = listing_price(view)
    stock = (stock_of or confirmed_channel_stock)(view)
    if listing is None:
        return _blocked("DRAFT_MISSING")
    if price is None:
        return _blocked("PUBLIC_PRICE_MISSING")
    payload = listing.get("payload") or {}
    # O texto do rascunho; se ele não passa, o do produto público, ou nada sobe.
    draft = (str(payload.get("title") or ""), str(payload.get("description") or ""))
    public = (str(view.get("name") or ""), str(view.get("description") or ""))
    issues = listing_copy_issues(view, *draft)
    if issues and listing_copy_issues(view, *public):
        return _blocked(*issues)
    title, description = public if issues else draft
    item_id = listing.get("external_listing_id")
    with _WRITE_LOCK:
        if item_id:
            writer.update_item(item_id, price=Decimal(price), available_quantity=stock)
            remote = writer.set_description(item_id, description)
        else:
            # Queda no meio do POST anterior: o anúncio pode existir. O fundador
            # confere no painel (ou refaz o rascunho) antes de criar de novo.
            if listing["state"] == ListingState.PUBLISHING.value:
                return _blocked("PUBLISH_IN_DOUBT")
            if not stock:
                return _blocked(
                    "STOCK_NOT_CONFIRMED" if stock is None else "STOCK_ZERO"
                )
            body, missing = mercado_livre_item_body(
                {
                    **payload,
                    "title": title,
                    "description": description,
                    "category_id": _ml_category(
                        writer, str(payload.get("category_id") or ""), title
                    ),
                    "images": payload.get("images") or product.get("images"),
                },
                sku=sku,
                price=price,
                quantity=stock,
                base_url=base_url,
            )
            if missing:
                return _blocked(*missing)
            store.upsert_listing(sku, MERCADO_LIVRE, ListingState.PUBLISHING)
            try:
                remote = writer.create_item(body)
            except MarketplaceApiError as exc:
                # 4xx (fora o 408): o canal recusou e nada foi criado. Rede, 5xx
                # ou POST sem id deixam PUBLISHING: a criação fica em dúvida.
                if 400 <= (exc.http_status or 0) < 500 and exc.http_status != 408:
                    store.upsert_listing(
                        sku, MERCADO_LIVRE, ListingState.FAILED, error=exc.code
                    )
                raise
    published = not item_id
    in_sync = _same("price", price, remote.get("price")) and (
        stock is None
        or _same("available_quantity", stock, remote.get("available_quantity"))
    )
    state = (
        ListingState.PAUSED
        if remote.get("status") == "paused"
        else ListingState.PUBLISHED
        if in_sync
        else ListingState.UPDATE_PENDING
    )
    saved = store.record_readback(
        MERCADO_LIVRE, sku, state=state, remote=remote, published=published
    )
    if in_sync:
        store.resolve_sync_conflicts(MERCADO_LIVRE, sku, list(SYNC_FIELDS.values()))
    store.event(
        "LISTING_PUBLISHED" if published else "LISTING_UPDATED",
        sku,
        {
            "channel": MERCADO_LIVRE,
            "external_write": True,
            "remote_status": remote.get("status"),
        },
    )
    return {
        "status": "PUBLISHED" if published else "UPDATED",
        "listing": saved,
        "remote": remote,
        "external_write": True,
    }


def pause_mercado_livre_listing(
    store: MarketplaceStore, writer: Any, product: dict[str, Any]
) -> dict[str, Any]:
    """Pausa o anúncio no canal (``PUT status=paused``) e grava a leitura de volta.

    Passa pelo mesmo ExternalWriteGuard do publish; sem ele e as flags, nada sai.
    """
    if reasons := _write_blockers(store, writer, product):
        return _blocked(*reasons)
    sku = str(product.get("sku") or product.get("id") or "")
    item_id = (store.listing(MERCADO_LIVRE, sku) or {}).get("external_listing_id")
    if not item_id:
        return _blocked("REMOTE_LISTING_MISSING")
    with _WRITE_LOCK:
        remote = writer.pause_item(item_id)
    paused = remote.get("status") == "paused"
    saved = store.record_readback(
        MERCADO_LIVRE,
        sku,
        state=ListingState.PAUSED if paused else ListingState.SYNC_CONFLICT,
        remote=remote,
    )
    store.event(
        "LISTING_PAUSED",
        sku,
        {
            "channel": MERCADO_LIVRE,
            "external_write": True,
            "remote_status": remote.get("status"),
        },
    )
    return {
        "status": "PAUSED" if paused else "PAUSE_NOT_CONFIRMED",
        "listing": saved,
        "external_write": True,
    }
