from __future__ import annotations

import os
from dataclasses import asdict
from threading import Lock
from typing import Any

from .marketplace_ops import (
    CHANNELS,
    CategoryMappingStatus,
    ChannelState,
    CredentialStatus,
    ListingState,
    MarketplaceAccount,
    SyncConflict,
    _now,
)


class PostgresMarketplaceStore:
    """Durable marketplace state backed by a compact JSONB key-value table."""

    def __init__(self, database_url: str):
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL deve apontar para PostgreSQL")
        self.database_url = database_url
        self.locks: dict[str, Lock] = {}
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS marketplace_state (
                namespace TEXT NOT NULL,
                item_key TEXT NOT NULL,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (namespace, item_key))"""
            )
        for channel, definition in CHANNELS.items():
            self._put("channels", channel, asdict(definition), insert_only=True)

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url, connect_timeout=5)

    def _put(
        self,
        namespace: str,
        key: str,
        payload: dict[str, Any],
        *,
        insert_only: bool = False,
    ) -> dict[str, Any]:
        from psycopg.types.json import Jsonb

        with self._connect() as db:
            if insert_only:
                db.execute(
                    """INSERT INTO marketplace_state (namespace, item_key, payload)
                    VALUES (%s, %s, %s) ON CONFLICT (namespace, item_key) DO NOTHING""",
                    (namespace, key, Jsonb(payload)),
                )
            else:
                db.execute(
                    """INSERT INTO marketplace_state (namespace, item_key, payload)
                    VALUES (%s, %s, %s) ON CONFLICT (namespace, item_key)
                    DO UPDATE SET payload=EXCLUDED.payload, updated_at=NOW()""",
                    (namespace, key, Jsonb(payload)),
                )
        return self._get(namespace, key) or payload

    def _get(self, namespace: str, key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM marketplace_state WHERE namespace=%s AND item_key=%s",
                (namespace, key),
            ).fetchone()
        return dict(row[0]) if row else None

    def _all(self, namespace: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM marketplace_state WHERE namespace=%s ORDER BY item_key",
                (namespace,),
            ).fetchall()
        return [dict(row[0]) for row in rows]

    def channels(self) -> list[dict[str, Any]]:
        result = []
        for stored in self._all("channels"):
            item = dict(stored)
            if hasattr(item.get("mode"), "value"):
                item["mode"] = item["mode"].value
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
        prefix = channel.upper().replace("-", "_")
        raw = os.getenv(f"{prefix}_ACCOUNT_ID")
        store = os.getenv(f"{prefix}_STORE_ID")
        credential = (
            f"{prefix}_ACCESS_TOKEN" if os.getenv(f"{prefix}_ACCESS_TOKEN") else None
        )
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
            CredentialStatus.NOT_TESTED if credential else CredentialStatus.MISSING,
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
        status = status or (
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
        return self._put("category_mappings", f"{channel}|{internal_category}", data)

    def category_mappings(self, channel: str | None = None) -> list[dict[str, Any]]:
        values = self._all("category_mappings")
        return [item for item in values if not channel or item["channel"] == channel]

    def save_connection_check(
        self, channel: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        safe = {**data, "checked_at": _now()}
        result = self._put("connection_checks", channel, safe)
        self.event(
            "READ_ONLY_CONNECTION_CHECK", channel, {"status": safe.get("readiness")}
        )
        return result

    def connection_checks(self) -> list[dict[str, Any]]:
        return self._all("connection_checks")

    def save_category_details(
        self, channel: str, internal_category: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        details = {
            **data,
            "channel": channel,
            "internal_category": internal_category,
            "discovered_at": _now(),
        }
        return self._put("category_details", f"{channel}|{internal_category}", details)

    def category_details(self, channel: str | None = None) -> list[dict[str, Any]]:
        values = self._all("category_details")
        return [item for item in values if not channel or item["channel"] == channel]

    def save_attributes(
        self, channel: str, external_category_id: str, attributes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        discovered_at = _now()
        for attribute in attributes:
            attribute_id = str(attribute.get("id") or "")
            if attribute_id:
                self._put(
                    "attributes",
                    f"{channel}|{external_category_id}|{attribute_id}",
                    {
                        **attribute,
                        "channel": channel,
                        "external_category_id": external_category_id,
                        "discovered_at": discovered_at,
                    },
                )
        return self.attributes(channel, external_category_id)

    def attributes(
        self, channel: str | None = None, external_category_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            item
            for item in self._all("attributes")
            if (not channel or item["channel"] == channel)
            and (
                not external_category_id
                or item["external_category_id"] == external_category_id
            )
        ]

    def save_publish_plan(self, data: dict[str, Any]) -> dict[str, Any]:
        plan = dict(data)
        plan["plan_id"] = str(plan.get("plan_id") or f"plan_{os.urandom(8).hex()}")
        plan["created_at"] = _now()
        return self._put("publish_plans", plan["plan_id"], plan)

    def publish_plans(self) -> list[dict[str, Any]]:
        return self._all("publish_plans")

    def upsert_listing(
        self,
        sku: str,
        channel: str,
        state: ListingState,
        *,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        key = f"{channel}|{sku}"
        old = self._get("listings", key) or {}
        data = {
            "id": f"listing_{channel}_{sku}",
            "sku": sku,
            "channel": channel,
            "state": state.value,
            "external_listing_id": None,
            "external_product_id": None,
            "local_version": old.get("local_version", 0) + 1,
            "remote_version": old.get("remote_version"),
            "last_sync_at": old.get("last_sync_at"),
            "last_publish_at": old.get("last_publish_at"),
            "last_error": error,
            "payload": payload if payload is not None else old.get("payload", {}),
        }
        return self._put("listings", key, data)

    def listings(self, channel: str | None = None) -> list[dict[str, Any]]:
        values = self._all("listings")
        return [item for item in values if not channel or item["channel"] == channel]

    def listing(self, channel: str, sku: str) -> dict[str, Any] | None:
        return self._get("listings", f"{channel}|{sku}")

    def override(self, sku: str, channel: str, action: str, reason: str) -> None:
        self._put(
            "overrides",
            f"{channel}|{sku}|{action}",
            {
                "sku": sku,
                "channel": channel,
                "action": action,
                "reason": reason,
                "created_at": _now(),
            },
        )

    def is_overridden(self, sku: str, channel: str, action: str) -> bool:
        return self._get("overrides", f"{channel}|{sku}|{action}") is not None

    def recalled(self, key: str) -> dict[str, Any] | None:
        return self._get("idempotency", key)

    def remember(self, key: str, result: dict[str, Any]) -> dict[str, Any]:
        return self._put("idempotency", key, result, insert_only=True)

    def event(self, action: str, target: str, data: dict[str, Any]) -> None:
        event_id = f"event_{os.urandom(8).hex()}"
        self._put(
            "events",
            event_id,
            {
                "event_id": event_id,
                "action": action,
                "target": target,
                "data": data,
                "created_at": _now(),
            },
        )

    def audit(self) -> list[dict[str, Any]]:
        return sorted(self._all("events"), key=lambda item: item["created_at"])

    def record_sync_conflict(self, conflict: SyncConflict) -> dict[str, Any]:
        data = asdict(conflict)
        conflict_id = f"{conflict.type}:{conflict.channel}:{conflict.sku}"
        result = {"conflict_id": conflict_id, **data, "resolved_at": None}
        self._put("sync_conflicts", conflict_id, result)
        self.event(
            "SYNC_CONFLICT_DETECTED",
            conflict.sku,
            {"channel": conflict.channel, "type": conflict.type},
        )
        return result

    def sync_conflicts(self, *, unresolved_only: bool = True) -> list[dict[str, Any]]:
        values = self._all("sync_conflicts")
        return [
            item
            for item in values
            if not unresolved_only or not item.get("resolved_at")
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
        return self._put("approvals", request["request_id"], request, insert_only=True)

    def resolve_approval(self, request_id: str, status: str) -> dict[str, Any] | None:
        data = self._get("approvals", request_id)
        if data is None:
            return None
        data.update(status=status, resolved_at=_now())
        result = self._put("approvals", request_id, data)
        self.event("APPROVAL_RESOLVED", request_id, {"status": status})
        return result

    def approvals(self) -> list[dict[str, Any]]:
        return self._all("approvals")
