"""Orders Ledger: registro mínimo, auditável e idempotente de vendas reais.

A compra acontece fora do site (WhatsApp ou marketplace). Este ledger guarda só
o necessário para conciliar mídia e economics: SKU, canal, referência da visita,
UTMs, valores, pagamento, estado e uma referência de evidência. Nome, telefone e
e-mail do cliente não pertencem a esta tabela.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .sqlstore import SqlStore


class OrderChannel(StrEnum):
    WHATSAPP = "whatsapp"
    MERCADO_LIVRE = "mercado_livre"
    SHOPEE = "shopee"
    TIKTOK_SHOP = "tiktok_shop"
    INSTAGRAM = "instagram"
    OTHER = "other"


class PaymentMethod(StrEnum):
    PIX = "pix"
    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    BOLETO = "boleto"
    BANK_TRANSFER = "bank_transfer"
    CASH = "cash"
    MARKETPLACE = "marketplace"
    OTHER = "other"
    UNKNOWN = "unknown"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELED = "CANCELED"
    REFUNDED = "REFUNDED"


ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset({OrderStatus.PAID, OrderStatus.CANCELED}),
    OrderStatus.PAID: frozenset(
        {OrderStatus.SHIPPED, OrderStatus.CANCELED, OrderStatus.REFUNDED}
    ),
    OrderStatus.SHIPPED: frozenset({OrderStatus.DELIVERED, OrderStatus.REFUNDED}),
    OrderStatus.DELIVERED: frozenset({OrderStatus.REFUNDED}),
    OrderStatus.CANCELED: frozenset(),
    OrderStatus.REFUNDED: frozenset(),
}
CONFIRMED_STATUSES = frozenset(
    {OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED}
)

_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
# Telefones brasileiros com sinais claros: +55, DDD entre parênteses, celular
# com hífen ou 11 dígitos corridos (DDD + 9 + 8). IDs numéricos longos de
# marketplace não são afetados.
_PHONE = re.compile(
    r"\+\s?55|\(\d{2}\)\s?\d{4,5}|(?<!\d)9\d{4}-\d{4}(?!\d)"
    r"|(?<!\d)(?:55)?\d{2}9\d{8}(?!\d)"
)
_TEXT_ID = r"^[A-Za-z0-9._:-]+$"


def _reject_personal_data(value: str | None) -> str | None:
    if value and (_EMAIL.search(value) or _PHONE.search(value)):
        raise ValueError("personal_data_not_allowed")
    return value


class OrderRecord(BaseModel):
    """Venda informada pelo fundador ou operador autorizado."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    order_id: str = Field(min_length=6, max_length=120, pattern=_TEXT_ID)
    created_at: datetime | None = None
    sku: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    channel: OrderChannel
    visit_ref: str | None = Field(default=None, pattern=r"^VT-[A-Z0-9]{1,13}$")
    # Pseudonymous WhatsApp conversation id (HMAC), never the phone number.
    conversation_ref: str | None = Field(default=None, pattern=r"^wa_[a-f0-9]{32}$")
    utm_source: str | None = Field(default=None, max_length=80)
    utm_medium: str | None = Field(default=None, max_length=80)
    utm_campaign: str | None = Field(default=None, max_length=80)
    utm_content: str | None = Field(default=None, max_length=80)
    utm_term: str | None = Field(default=None, max_length=80)
    sale_price: Decimal = Field(gt=0, le=1_000_000, decimal_places=2)
    discount: Decimal = Field(default=Decimal(0), ge=0, decimal_places=2)
    shipping_charged: Decimal = Field(
        default=Decimal(0), ge=0, le=1_000_000, decimal_places=2
    )
    payment_method: PaymentMethod
    status: OrderStatus = OrderStatus.PENDING
    evidence_ref: str = Field(min_length=3, max_length=200)
    recorded_by: str = Field(min_length=2, max_length=80)

    @field_validator(
        "evidence_ref",
        "recorded_by",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
    )
    @classmethod
    def no_personal_data(cls, value: str | None) -> str | None:
        return _reject_personal_data(value)

    @field_validator("created_at")
    @classmethod
    def aware_and_not_future(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("created_at_requires_timezone")
        if value > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("created_at_in_future")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def discount_within_price(self) -> OrderRecord:
        if self.discount > self.sale_price:
            raise ValueError("discount_exceeds_sale_price")
        return self


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str = Field(min_length=8, max_length=120, pattern=_TEXT_ID)
    status: OrderStatus
    recorded_by: str = Field(min_length=2, max_length=80)
    evidence_ref: str | None = Field(default=None, min_length=3, max_length=200)

    @field_validator("recorded_by", "evidence_ref")
    @classmethod
    def no_personal_data(cls, value: str | None) -> str | None:
        return _reject_personal_data(value)


class OrderConflictError(ValueError):
    """The same identifier was reused with different content or state."""


class OrderTransitionError(ValueError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS orders_ledger (
    order_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    sku TEXT NOT NULL,
    channel TEXT NOT NULL,
    visit_ref TEXT,
    conversation_ref TEXT,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    utm_content TEXT,
    utm_term TEXT,
    sale_price_cents BIGINT NOT NULL CHECK (sale_price_cents > 0),
    discount_cents BIGINT NOT NULL CHECK (discount_cents >= 0),
    shipping_charged_cents BIGINT NOT NULL CHECK (shipping_charged_cents >= 0),
    payment_method TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_ledger_created
    ON orders_ledger(created_at);
CREATE TABLE IF NOT EXISTS orders_ledger_events (
    event_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders_ledger(order_id),
    event_type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    evidence_ref TEXT,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_ledger_events_order
    ON orders_ledger_events(order_id, occurred_at);
"""

_ORDER_COLUMNS = (
    "order_id",
    "created_at",
    "sku",
    "channel",
    "visit_ref",
    "conversation_ref",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "sale_price_cents",
    "discount_cents",
    "shipping_charged_cents",
    "payment_method",
    "status",
    "evidence_ref",
    "recorded_by",
    "payload_hash",
    "recorded_at",
    "updated_at",
)
_MONEY_COLUMNS = ("sale_price", "discount", "shipping_charged")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cents(value: Decimal) -> int:
    return int(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)


def _brl(cents: int) -> str:
    return f"{Decimal(cents) / 100:.2f}"


def _payload_hash(record: OrderRecord) -> str:
    # Hash only what was submitted, so retrying the same body stays idempotent
    # even when created_at is filled by the server.
    submitted = record.model_dump(mode="json", exclude_unset=True)
    for name in _MONEY_COLUMNS:
        if name in submitted:
            submitted[name] = _cents(getattr(record, name))
    canonical = json.dumps(submitted, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _public(row: dict[str, Any]) -> dict[str, Any]:
    item = {key: row[key] for key in _ORDER_COLUMNS if key != "payload_hash"}
    for name in _MONEY_COLUMNS:
        item[name] = _brl(int(item.pop(f"{name}_cents")))
    item["net_merchandise_brl"] = _brl(
        int(row["sale_price_cents"]) - int(row["discount_cents"])
    )
    return item


class OrderLedger(SqlStore):
    """SQLite locally; PostgreSQL whenever ``database_url`` is configured."""

    SCHEMA = SCHEMA

    def record(self, order: OrderRecord) -> tuple[str, dict[str, Any]]:
        """Return ("created" | "duplicate", order). Raise on conflicting reuse."""
        now = _now()
        payload_hash = _payload_hash(order)
        created_at = (order.created_at or datetime.now(timezone.utc)).isoformat()
        values = {
            "order_id": order.order_id,
            "created_at": created_at,
            "sku": order.sku,
            "channel": order.channel.value,
            "visit_ref": order.visit_ref,
            "conversation_ref": order.conversation_ref,
            "utm_source": order.utm_source,
            "utm_medium": order.utm_medium,
            "utm_campaign": order.utm_campaign,
            "utm_content": order.utm_content,
            "utm_term": order.utm_term,
            "sale_price_cents": _cents(order.sale_price),
            "discount_cents": _cents(order.discount),
            "shipping_charged_cents": _cents(order.shipping_charged),
            "payment_method": order.payment_method.value,
            "status": order.status.value,
            "evidence_ref": order.evidence_ref,
            "recorded_by": order.recorded_by,
            "payload_hash": payload_hash,
            "recorded_at": now,
            "updated_at": now,
        }
        with self._connect() as (db, mark):
            inserted = db.execute(
                self._sql(
                    f"INSERT INTO orders_ledger ({', '.join(_ORDER_COLUMNS)}) "
                    f"VALUES ({', '.join('?' for _ in _ORDER_COLUMNS)}) "
                    "ON CONFLICT (order_id) DO NOTHING",
                    mark,
                ),
                [values[column] for column in _ORDER_COLUMNS],
            ).rowcount
            if inserted:
                self._add_event(
                    db,
                    mark,
                    event_id=f"{order.order_id}:recorded",
                    order_id=order.order_id,
                    event_type="ORDER_RECORDED",
                    from_status=None,
                    to_status=order.status.value,
                    actor=order.recorded_by,
                    evidence_ref=order.evidence_ref,
                    occurred_at=now,
                )
            row = self._row(db, mark, order.order_id)
        if not inserted and row["payload_hash"] != payload_hash:
            raise OrderConflictError("order_id_already_recorded_with_other_data")
        return ("created" if inserted else "duplicate"), _public(row)

    def change_status(self, order_id: str, change: StatusChange) -> tuple[str, dict]:
        """Apply one audited transition. Replaying the same event_id is a no-op."""
        with self._connect() as (db, mark):
            previous = db.execute(
                self._sql(
                    "SELECT order_id, to_status FROM orders_ledger_events "
                    "WHERE event_id = ?",
                    mark,
                ),
                [change.event_id],
            ).fetchone()
            if previous is not None:
                if (previous["order_id"], previous["to_status"]) != (
                    order_id,
                    change.status.value,
                ):
                    raise OrderConflictError("event_id_already_used")
                return "duplicate", _public(self._row(db, mark, order_id))
            row = self._row(db, mark, order_id)
            if row is None:
                raise KeyError(order_id)
            current = OrderStatus(row["status"])
            if change.status not in ALLOWED_TRANSITIONS[current]:
                raise OrderTransitionError(
                    f"transition_not_allowed:{current.value}->{change.status.value}"
                )
            now = _now()
            updated = db.execute(
                self._sql(
                    "UPDATE orders_ledger SET status = ?, updated_at = ? "
                    "WHERE order_id = ? AND status = ?",
                    mark,
                ),
                [change.status.value, now, order_id, current.value],
            ).rowcount
            if not updated:
                raise OrderConflictError("order_changed_concurrently")
            self._add_event(
                db,
                mark,
                event_id=change.event_id,
                order_id=order_id,
                event_type="STATUS_CHANGED",
                from_status=current.value,
                to_status=change.status.value,
                actor=change.recorded_by,
                evidence_ref=change.evidence_ref,
                occurred_at=now,
            )
            row = self._row(db, mark, order_id)
        return "changed", _public(row)

    def get(self, order_id: str) -> dict[str, Any] | None:
        with self._connect() as (db, mark):
            row = self._row(db, mark, order_id)
            if row is None:
                return None
            events = db.execute(
                self._sql(
                    "SELECT event_id, event_type, from_status, to_status, actor, "
                    "evidence_ref, occurred_at FROM orders_ledger_events "
                    "WHERE order_id = ? ORDER BY occurred_at, event_id",
                    mark,
                ),
                [order_id],
            ).fetchall()
        return {**_public(row), "events": [dict(event) for event in events]}

    @staticmethod
    def _where(
        status: OrderStatus | None,
        channel: OrderChannel | None,
        date_from: str | None,
        date_to: str | None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value, operator in (
            ("status", status.value if status else None, "="),
            ("channel", channel.value if channel else None, "="),
            ("created_at", date_from, ">="),
            ("created_at", date_to, "<"),
        ):
            if value is not None:
                clauses.append(f"{column} {operator} ?")
                params.append(value)
        return (f" WHERE {' AND '.join(clauses)}" if clauses else ""), params

    def list_orders(
        self,
        *,
        status: OrderStatus | None = None,
        channel: OrderChannel | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where, params = self._where(status, channel, date_from, date_to)
        params.append(max(1, min(limit, 1000)))
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql(
                    f"SELECT * FROM orders_ledger{where} "
                    "ORDER BY created_at DESC LIMIT ?",
                    mark,
                ),
                params,
            ).fetchall()
        return [_public(dict(row)) for row in rows]

    def summary(
        self,
        *,
        status: OrderStatus | None = None,
        channel: OrderChannel | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        """Totals over the whole filtered range, independent of any list limit.

        Pending, canceled and refunded orders are counted but never enter the
        confirmed revenue.
        """
        where, params = self._where(status, channel, date_from, date_to)
        with self._connect() as (db, mark):
            rows = db.execute(
                self._sql(
                    "SELECT status, COUNT(*) AS orders, "
                    "SUM(sale_price_cents - discount_cents) AS net_cents "
                    f"FROM orders_ledger{where} GROUP BY status",
                    mark,
                ),
                params,
            ).fetchall()
        by_status = {row["status"]: int(row["orders"]) for row in rows}
        confirmed = [row for row in rows if row["status"] in CONFIRMED_STATUSES]
        return {
            "orders": sum(by_status.values()),
            "by_status": by_status,
            "confirmed_orders": sum(int(row["orders"]) for row in confirmed),
            "confirmed_net_merchandise_brl": _brl(
                sum(int(row["net_cents"]) for row in confirmed)
            ),
        }

    @staticmethod
    def _row(db: Any, mark: str, order_id: str) -> dict[str, Any] | None:
        row = db.execute(
            OrderLedger._sql("SELECT * FROM orders_ledger WHERE order_id = ?", mark),
            [order_id],
        ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _add_event(db: Any, mark: str, **event: Any) -> None:
        columns = (
            "event_id",
            "order_id",
            "event_type",
            "from_status",
            "to_status",
            "actor",
            "evidence_ref",
            "occurred_at",
        )
        db.execute(
            OrderLedger._sql(
                f"INSERT INTO orders_ledger_events ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                mark,
            ),
            [event[column] for column in columns],
        )


# São Paulo has had no daylight saving time since 2019, so a fixed offset is exact.
SAO_PAULO = timezone(timedelta(hours=-3))


def local_day_bounds(
    first_day: date | None, last_day: date | None
) -> tuple[str | None, str | None]:
    """Convert inclusive São Paulo calendar days to UTC [start, end) bounds."""

    def start_of(day: date) -> str:
        return (
            datetime.combine(day, time.min, tzinfo=SAO_PAULO)
            .astimezone(timezone.utc)
            .isoformat()
        )

    return (
        start_of(first_day) if first_day else None,
        start_of(last_day + timedelta(days=1)) if last_day else None,
    )


def make_order_ledger(
    sqlite_path: str | Path, database_url: str | None = None
) -> OrderLedger:
    return OrderLedger(sqlite_path, database_url)
