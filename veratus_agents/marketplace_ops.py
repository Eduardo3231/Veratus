from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
        agent: str = "marketplace-agent",
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
            from .operations import AGENT_REGISTRY

            if (
                agent in AGENT_REGISTRY
                and "PUBLISH" not in AGENT_REGISTRY[agent].permissions
            ):
                reasons.append("AGENT_PUBLISH_PERMISSION_REQUIRED")
        except ImportError:
            reasons.append("AGENT_REGISTRY_UNAVAILABLE")
        if approval is None or getattr(approval, "status", None) != "APPROVED":
            reasons.append("PUBLISH_APPROVAL_REQUIRED")
        if self.store.is_overridden(product.get("sku", ""), channel, "PUBLISH"):
            reasons.append("CEO_OVERRIDE_BLOCK")
        return not reasons, reasons


class ApprovalRequestLike:
    status: str


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
    key = f"{channel.value}:{product['sku']}:CREATE_DRAFT:{product.get('updated_at', '1')}"
    existing = store.listing(channel.value, product["sku"])
    with sqlite3.connect(store.path) as db:
        remembered = db.execute(
            "SELECT result_json FROM marketplace_idempotency WHERE key=?", (key,)
        ).fetchone()
    if remembered:
        return json.loads(remembered[0])
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


FOUNDER_CONFIRMED_DEFAULTS: dict[str, Any] = {
    "price_brl": "289.90",
    "cost_brl": "65.00",
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
