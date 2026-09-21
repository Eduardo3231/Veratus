from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import uuid
from contextlib import closing
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class ProductStatus(StrEnum):
    NEW = "NEW"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    ENRICHING = "ENRICHING"
    QA_REJECTED = "QA_REJECTED"
    APPROVED = "APPROVED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    PUBLISHED = "PUBLISHED"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    PAUSED = "PAUSED"


ALLOWED_TRANSITIONS: dict[ProductStatus, set[ProductStatus]] = {
    ProductStatus.NEW: {
        ProductStatus.ENRICHING,
        ProductStatus.NEEDS_INFORMATION,
        ProductStatus.PAUSED,
    },
    ProductStatus.NEEDS_INFORMATION: {
        ProductStatus.ENRICHING,
        ProductStatus.PAUSED,
    },
    ProductStatus.ENRICHING: {
        ProductStatus.NEEDS_INFORMATION,
        ProductStatus.QA_REJECTED,
        ProductStatus.APPROVED,
        ProductStatus.PAUSED,
    },
    ProductStatus.QA_REJECTED: {
        ProductStatus.ENRICHING,
        ProductStatus.PAUSED,
    },
    ProductStatus.APPROVED: {
        ProductStatus.PUBLISHED,
        ProductStatus.PUBLISH_FAILED,
        ProductStatus.OUT_OF_STOCK,
        ProductStatus.PAUSED,
    },
    ProductStatus.PUBLISH_FAILED: {
        ProductStatus.APPROVED,
        ProductStatus.PAUSED,
    },
    ProductStatus.PUBLISHED: {
        ProductStatus.OUT_OF_STOCK,
        ProductStatus.PUBLISH_FAILED,
        ProductStatus.PAUSED,
    },
    ProductStatus.OUT_OF_STOCK: {
        ProductStatus.APPROVED,
        ProductStatus.PAUSED,
    },
    ProductStatus.PAUSED: {
        ProductStatus.ENRICHING,
        ProductStatus.APPROVED,
    },
}


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    field: str = Field(min_length=1, max_length=80)
    reference: str = Field(min_length=1, max_length=300)


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=140)
    category: str = Field(min_length=2, max_length=80)
    subcategory: str | None = Field(default=None, max_length=80)
    category_code: str = Field(pattern=r"^[A-Z0-9]{2,5}$")
    product_code: str = Field(pattern=r"^[A-Z0-9]{2,5}$")
    material: str | None = Field(default=None, max_length=180)
    supplier_ref: str | None = Field(default=None, max_length=100)
    availability: str | None = Field(default=None, max_length=100)
    inventory_mode: str | None = Field(default=None, max_length=40)
    channel_stock_cap: int | None = Field(default=None, ge=0)
    supplier_visibility: str | None = Field(default="PRIVATE", max_length=30)
    images: list[str] = Field(default_factory=list, max_length=12)
    variants: list[str] = Field(default_factory=list, max_length=30)
    cost_brl: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    price_brl: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    contribution_margin_brl: Decimal | None = Field(default=None, decimal_places=2)
    max_cpa_brl: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def price_cannot_be_below_cost(self) -> ProductCreate:
        if (
            self.cost_brl is not None
            and self.price_brl is not None
            and self.price_brl < self.cost_brl
        ):
            raise ValueError("price_brl não pode ser menor que cost_brl")
        return self


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=2, max_length=140)
    category: str | None = Field(default=None, min_length=2, max_length=80)
    subcategory: str | None = Field(default=None, min_length=2, max_length=80)
    material: str | None = Field(default=None, min_length=1, max_length=180)
    supplier_ref: str | None = Field(default=None, min_length=1, max_length=100)
    availability: str | None = Field(default=None, min_length=1, max_length=100)
    inventory_mode: str | None = Field(default=None, min_length=2, max_length=40)
    channel_stock_cap: int | None = Field(default=None, ge=0)
    supplier_visibility: str | None = Field(default=None, min_length=2, max_length=30)
    images: list[str] | None = Field(default=None, max_length=12)
    variants: list[str] | None = Field(default=None, max_length=30)
    cost_brl: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    price_brl: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    contribution_margin_brl: Decimal | None = Field(default=None, decimal_places=2)
    max_cpa_brl: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    evidence: list[EvidenceRef] | None = Field(default=None, max_length=30)


class PricingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_cost_brl: Decimal = Field(ge=0, decimal_places=2)
    shipping_cost_brl: Decimal = Field(default=Decimal(0), ge=0, decimal_places=2)
    packaging_cost_brl: Decimal = Field(default=Decimal(0), ge=0, decimal_places=2)
    marketplace_fee_percent: Decimal = Field(
        default=Decimal(0), ge=0, le=80, decimal_places=4
    )
    tax_percent: Decimal = Field(default=Decimal(0), ge=0, le=80, decimal_places=4)
    desired_margin_percent: Decimal = Field(
        default=Decimal(30), ge=0, le=90, decimal_places=4
    )
    promotional_discount_percent: Decimal = Field(
        default=Decimal(0), ge=0, le=80, decimal_places=4
    )

    @model_validator(mode="after")
    def percentages_must_leave_positive_revenue(self) -> PricingInput:
        total = (
            self.marketplace_fee_percent
            + self.tax_percent
            + self.desired_margin_percent
        )
        if total >= 100:
            raise ValueError("taxas, impostos e margem precisam somar menos de 100%")
        return self


def calculate_pricing(inputs: PricingInput) -> dict[str, str]:
    fixed_cost = (
        inputs.product_cost_brl + inputs.shipping_cost_brl + inputs.packaging_cost_brl
    )
    fees_rate = (inputs.marketplace_fee_percent + inputs.tax_percent) / 100
    margin_rate = inputs.desired_margin_percent / 100
    minimum_price = fixed_cost / (Decimal(1) - fees_rate)
    recommended_price = fixed_cost / (Decimal(1) - fees_rate - margin_rate)
    promotional_price = recommended_price * (
        Decimal(1) - inputs.promotional_discount_percent / 100
    )
    contribution = promotional_price * (Decimal(1) - fees_rate) - fixed_cost
    contribution_percent = (
        contribution / promotional_price * 100 if promotional_price else Decimal(0)
    )
    return {
        "fixed_cost_brl": str(_money(fixed_cost)),
        "minimum_price_brl": str(_money(minimum_price)),
        "recommended_price_brl": str(_money(recommended_price)),
        "promotional_price_brl": str(_money(promotional_price)),
        "contribution_margin_brl": str(_money(contribution)),
        "contribution_margin_percent": str(_money(contribution_percent)),
        "max_cpa_brl": str(_money(max(Decimal(0), contribution))),
    }


def qa_issues(product: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    required = {
        "supplier_ref": "referência interna do fornecedor",
        "availability": "disponibilidade confirmada",
        "cost_brl": "custo confirmado",
        "price_brl": "preço calculado e confirmado",
        "contribution_margin_brl": "margem de contribuição calculada",
        "max_cpa_brl": "CPA máximo calculado",
    }
    if product.get("category") != "jewelry_accessories":
        required["material"] = "material confirmado"
    for field, label in required.items():
        if product.get(field) in (None, ""):
            issues.append(f"Falta {label}.")
    if not product.get("images"):
        issues.append("Falta ao menos uma imagem do item exato.")
    evidence_fields = {
        item.get("field")
        for item in product.get("evidence", [])
        if isinstance(item, dict)
    }
    evidence_required = ["cost_brl", "availability"]
    if product.get("category") != "jewelry_accessories":
        evidence_required.append("material")
    for field in evidence_required:
        if field not in evidence_fields:
            issues.append(f"Falta evidência para {field}.")
    cost = product.get("cost_brl")
    price = product.get("price_brl")
    if (
        cost is not None
        and price is not None
        and Decimal(str(price)) < Decimal(str(cost))
    ):
        issues.append("O preço não cobre o custo do produto.")
    return issues


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_master (
    sku TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    product_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_product_master_status ON product_master(status, updated_at);
CREATE TABLE IF NOT EXISTS product_sku_counters (
    prefix TEXT PRIMARY KEY,
    next_value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS product_events (
    event_id TEXT PRIMARY KEY,
    sku TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_product_events_sku ON product_events(sku, created_at);
"""


class ProductRepository(Protocol):
    def create(
        self, product: ProductCreate, *, actor: str, event_id: str
    ) -> dict[str, Any]: ...
    def get(self, sku: str) -> dict[str, Any] | None: ...
    def list_products(
        self, *, status: ProductStatus | None = None, limit: int = 100
    ) -> list[dict[str, Any]]: ...
    def update(
        self, sku: str, update: ProductUpdate, *, actor: str, event_id: str, reason: str
    ) -> dict[str, Any] | None: ...
    def transition(
        self,
        sku: str,
        *,
        target: ProductStatus,
        actor: str,
        event_id: str,
        reason: str,
        override: bool = False,
    ) -> tuple[dict[str, Any] | None, list[str]]: ...
    def events(self, sku: str, *, limit: int = 100) -> list[dict[str, Any]]: ...


class SQLiteProductStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript(SQLITE_SCHEMA)
            connection.commit()

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        product = json.loads(row["product_json"])
        product.update(
            {
                "sku": row["sku"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        return product

    def create(
        self, product: ProductCreate, *, actor: str, event_id: str
    ) -> dict[str, Any]:
        actor = _require_actor(actor)
        event_id = _require_event_id(event_id)
        prefix = f"VRT-{product.category_code}-{product.product_code}"
        now = _utcnow()
        payload = product.model_dump(mode="json")
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT sku FROM product_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if duplicate:
                row = connection.execute(
                    "SELECT * FROM product_master WHERE sku = ?", (duplicate["sku"],)
                ).fetchone()
                connection.commit()
                return self._decode(row)
            row = connection.execute(
                "SELECT next_value FROM product_sku_counters WHERE prefix = ?",
                (prefix,),
            ).fetchone()
            number = int(row["next_value"]) if row else 1
            if row:
                connection.execute(
                    "UPDATE product_sku_counters SET next_value = ? WHERE prefix = ?",
                    (number + 1, prefix),
                )
            else:
                connection.execute(
                    "INSERT INTO product_sku_counters(prefix, next_value) VALUES (?, ?)",
                    (prefix, 2),
                )
            sku = f"{prefix}-{number:04d}"
            connection.execute(
                "INSERT INTO product_master VALUES (?, ?, ?, ?, ?)",
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
            connection.commit()
        return self.get(sku)  # type: ignore[return-value]

    def get(self, sku: str) -> dict[str, Any] | None:
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM product_master WHERE sku = ?", (sku,)
            ).fetchone()
        return self._decode(row) if row else None

    def list_products(
        self, *, status: ProductStatus | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            if status:
                rows = connection.execute(
                    "SELECT * FROM product_master WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                    (status.value, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM product_master ORDER BY updated_at DESC LIMIT ?",
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
        actor, reason = _require_audit(actor, reason)
        event_id = _require_event_id(event_id)
        changes = update.model_dump(mode="json", exclude_none=True)
        if not changes:
            raise ValueError("ao menos um campo precisa ser alterado")
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM product_events WHERE event_id = ?", (event_id,)
            ).fetchone():
                connection.commit()
                return self.get(sku)
            row = connection.execute(
                "SELECT * FROM product_master WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            status = ProductStatus(row["status"])
            if status in {ProductStatus.APPROVED, ProductStatus.PUBLISHED}:
                raise ValueError(
                    "retorne o produto para ENRICHING antes de alterar dados"
                )
            payload = json.loads(row["product_json"])
            payload.update(changes)
            ProductCreate.model_validate(payload)
            now = _utcnow()
            connection.execute(
                "UPDATE product_master SET product_json = ?, updated_at = ? WHERE sku = ?",
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
            connection.commit()
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
        actor, reason = _require_audit(actor, reason)
        event_id = _require_event_id(event_id)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM product_events WHERE event_id = ?", (event_id,)
            ).fetchone():
                connection.commit()
                return self.get(sku), []
            row = connection.execute(
                "SELECT * FROM product_master WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                connection.commit()
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
                connection.commit()
                return product, issues
            now = _utcnow()
            connection.execute(
                "UPDATE product_master SET status = ?, updated_at = ? WHERE sku = ? AND status = ?",
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
            connection.commit()
        return self.get(sku), []

    def events(self, sku: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM product_events WHERE sku = ? ORDER BY created_at DESC LIMIT ?",
                (sku, max(1, min(limit, 500))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
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
            "INSERT INTO product_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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


def _require_actor(actor: str) -> str:
    actor = actor.strip()
    if not actor or len(actor) > 100:
        raise ValueError("actor é obrigatório")
    return actor


def _require_audit(actor: str, reason: str) -> tuple[str, str]:
    actor = _require_actor(actor)
    reason = reason.strip()
    if not reason or len(reason) > 500:
        raise ValueError("reason é obrigatório")
    return actor, reason


def _require_event_id(event_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,200}", event_id):
        raise ValueError("event_id inválido")
    return event_id


def make_product_repository(
    sqlite_path: str | Path, database_url: str | None = None
) -> ProductRepository:
    if database_url:
        from .postgres_product_master import PostgresProductStore

        return PostgresProductStore(database_url)
    return SQLiteProductStore(sqlite_path)


def new_event_id(prefix: str = "evt") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"
