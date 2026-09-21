from __future__ import annotations

import json
from typing import Any

from psycopg.rows import dict_row

from .product_master import (
    ALLOWED_TRANSITIONS,
    ProductCreate,
    ProductStatus,
    ProductUpdate,
    _require_actor,
    _require_audit,
    _require_event_id,
    _utcnow,
    qa_issues,
)

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_master (
    sku TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    product_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_product_master_status ON product_master(status, updated_at);
CREATE TABLE IF NOT EXISTS product_sku_counters (
    prefix TEXT PRIMARY KEY,
    next_value BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_events (
    event_id TEXT PRIMARY KEY,
    sku TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_product_events_sku ON product_events(sku, created_at);
"""


class PostgresProductStore:
    def __init__(self, database_url: str):
        import psycopg

        self.database_url = database_url
        with psycopg.connect(database_url) as connection:
            connection.execute(POSTGRES_SCHEMA)

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        raw = row["product_json"]
        product = json.loads(raw) if isinstance(raw, str) else dict(raw)
        product.update(
            {
                "sku": row["sku"],
                "status": row["status"],
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
            }
        )
        return product

    def create(
        self, product: ProductCreate, *, actor: str, event_id: str
    ) -> dict[str, Any]:
        import psycopg

        actor = _require_actor(actor)
        event_id = _require_event_id(event_id)
        prefix = f"VRT-{product.category_code}-{product.product_code}"
        now = _utcnow()
        payload = product.model_dump(mode="json")
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            duplicate = connection.execute(
                "SELECT sku FROM product_events WHERE event_id = %s", (event_id,)
            ).fetchone()
            if duplicate:
                return self.get(duplicate["sku"])  # type: ignore[return-value]
            counter = connection.execute(
                """INSERT INTO product_sku_counters(prefix, next_value) VALUES (%s, 2)
                   ON CONFLICT (prefix) DO UPDATE
                   SET next_value = product_sku_counters.next_value + 1
                   RETURNING next_value - 1 AS number""",
                (prefix,),
            ).fetchone()
            sku = f"{prefix}-{int(counter['number']):04d}"
            connection.execute(
                """INSERT INTO product_master
                   (sku, status, product_json, created_at, updated_at)
                   VALUES (%s, %s, %s::jsonb, %s, %s)""",
                (sku, ProductStatus.NEW.value, json.dumps(payload), now, now),
            )
            self._insert_event(
                connection,
                event_id=event_id,
                sku=sku,
                event_type="product_created",
                actor=actor,
                reason="Cadastro inicial",
                from_status=None,
                to_status=ProductStatus.NEW,
                payload=payload,
                created_at=now,
            )
        return self.get(sku)  # type: ignore[return-value]

    def get(self, sku: str) -> dict[str, Any] | None:
        import psycopg

        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM product_master WHERE sku = %s", (sku,)
            ).fetchone()
        return self._decode(row) if row else None

    def list_products(
        self, *, status: ProductStatus | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        import psycopg

        limit = max(1, min(limit, 500))
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            if status:
                rows = connection.execute(
                    "SELECT * FROM product_master WHERE status = %s ORDER BY updated_at DESC LIMIT %s",
                    (status.value, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM product_master ORDER BY updated_at DESC LIMIT %s",
                    (limit,),
                ).fetchall()
        return [self._decode(row) for row in rows]

    def update(
        self,
        sku: str,
        update: ProductUpdate,
        *,
        actor: str,
        event_id: str,
        reason: str,
    ) -> dict[str, Any] | None:
        import psycopg

        actor, reason = _require_audit(actor, reason)
        event_id = _require_event_id(event_id)
        changes = update.model_dump(mode="json", exclude_none=True)
        if not changes:
            raise ValueError("ao menos um campo precisa ser alterado")
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            if connection.execute(
                "SELECT 1 FROM product_events WHERE event_id = %s", (event_id,)
            ).fetchone():
                return self.get(sku)
            row = connection.execute(
                "SELECT * FROM product_master WHERE sku = %s FOR UPDATE", (sku,)
            ).fetchone()
            if row is None:
                return None
            status = ProductStatus(row["status"])
            if status in {ProductStatus.APPROVED, ProductStatus.PUBLISHED}:
                raise ValueError(
                    "retorne o produto para ENRICHING antes de alterar dados"
                )
            raw = row["product_json"]
            payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
            payload.update(changes)
            ProductCreate.model_validate(payload)
            now = _utcnow()
            connection.execute(
                "UPDATE product_master SET product_json = %s::jsonb, updated_at = %s WHERE sku = %s",
                (json.dumps(payload), now, sku),
            )
            self._insert_event(
                connection,
                event_id=event_id,
                sku=sku,
                event_type="product_updated",
                actor=actor,
                reason=reason,
                from_status=status,
                to_status=status,
                payload={"fields": sorted(changes)},
                created_at=now,
            )
        return self.get(sku)

    def transition(
        self,
        sku: str,
        *,
        target: ProductStatus,
        actor: str,
        event_id: str,
        reason: str,
        override: bool = False,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        import psycopg

        actor, reason = _require_audit(actor, reason)
        event_id = _require_event_id(event_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            if connection.execute(
                "SELECT 1 FROM product_events WHERE event_id = %s", (event_id,)
            ).fetchone():
                return self.get(sku), []
            row = connection.execute(
                "SELECT * FROM product_master WHERE sku = %s FOR UPDATE", (sku,)
            ).fetchone()
            if row is None:
                return None, []
            current = ProductStatus(row["status"])
            issues: list[str] = []
            if not override and target not in ALLOWED_TRANSITIONS[current]:
                issues.append(
                    f"Transição {current.value} → {target.value} não permitida."
                )
            product = self._decode(row)
            if target in {ProductStatus.APPROVED, ProductStatus.PUBLISHED}:
                issues.extend(qa_issues(product))
            if issues:
                now = _utcnow()
                self._insert_event(
                    connection,
                    event_id=event_id,
                    sku=sku,
                    event_type=(
                        "ceo_override_blocked" if override else "transition_blocked"
                    ),
                    actor=actor,
                    reason=reason,
                    from_status=current,
                    to_status=target,
                    payload={"override": override, "issues": issues},
                    created_at=now,
                )
                return product, issues
            now = _utcnow()
            connection.execute(
                "UPDATE product_master SET status = %s, updated_at = %s WHERE sku = %s AND status = %s",
                (target.value, now, sku, current.value),
            )
            self._insert_event(
                connection,
                event_id=event_id,
                sku=sku,
                event_type="ceo_override" if override else "status_transition",
                actor=actor,
                reason=reason,
                from_status=current,
                to_status=target,
                payload={"override": override},
                created_at=now,
            )
        return self.get(sku), []

    def events(self, sku: str, *, limit: int = 100) -> list[dict[str, Any]]:
        import psycopg

        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT * FROM product_events WHERE sku = %s ORDER BY created_at DESC LIMIT %s",
                (sku, max(1, min(limit, 500))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            raw = item.pop("payload_json")
            item["payload"] = json.loads(raw) if isinstance(raw, str) else raw
            item["created_at"] = item["created_at"].isoformat()
            result.append(item)
        return result

    @staticmethod
    def _insert_event(
        connection: Any,
        *,
        event_id: str,
        sku: str,
        event_type: str,
        actor: str,
        reason: str,
        from_status: ProductStatus | None,
        to_status: ProductStatus,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        connection.execute(
            """INSERT INTO product_events
               (event_id, sku, event_type, actor, reason, from_status, to_status,
                payload_json, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)""",
            (
                event_id,
                sku,
                event_type,
                actor,
                reason,
                from_status.value if from_status else None,
                to_status.value,
                json.dumps(payload, ensure_ascii=False),
                created_at,
            ),
        )
