from __future__ import annotations

import datetime as dt
import sqlite3
from collections import defaultdict
from contextlib import closing
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Protocol

from .schemas import PerformanceRecord

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketing_performance (
    event_id TEXT PRIMARY KEY,
    occurred_on TEXT NOT NULL,
    channel TEXT NOT NULL,
    campaign TEXT NOT NULL,
    creative TEXT NOT NULL,
    impressions INTEGER NOT NULL CHECK (impressions >= 0),
    clicks INTEGER NOT NULL CHECK (clicks >= 0),
    leads INTEGER NOT NULL CHECK (leads >= 0),
    conversations INTEGER NOT NULL CHECK (conversations >= 0),
    sales INTEGER NOT NULL CHECK (sales >= 0),
    spend_cents INTEGER NOT NULL CHECK (spend_cents >= 0),
    revenue_cents INTEGER NOT NULL CHECK (revenue_cents >= 0),
    product_cost_cents INTEGER NOT NULL DEFAULT 0 CHECK (product_cost_cents >= 0),
    shipping_cost_cents INTEGER NOT NULL DEFAULT 0 CHECK (shipping_cost_cents >= 0),
    fees_cents INTEGER NOT NULL DEFAULT 0 CHECK (fees_cents >= 0),
    taxes_cents INTEGER NOT NULL DEFAULT 0 CHECK (taxes_cents >= 0),
    refunds_cents INTEGER NOT NULL DEFAULT 0 CHECK (refunds_cents >= 0),
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_marketing_performance_date
    ON marketing_performance(occurred_on);
CREATE INDEX IF NOT EXISTS idx_marketing_performance_campaign
    ON marketing_performance(channel, campaign, occurred_on);
"""

POSTGRES_SCHEMA = SQLITE_SCHEMA.replace("occurred_on TEXT", "occurred_on DATE").replace(
    "created_at TEXT", "created_at TIMESTAMPTZ"
)


def _cents(value: Decimal) -> int:
    return int(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)


def _money(cents: int) -> float:
    return float((Decimal(cents) / 100).quantize(Decimal("0.01")))


class MetricsRepository(Protocol):
    def add(self, record: PerformanceRecord) -> bool: ...

    def list_records(
        self,
        *,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        channel: str | None = None,
        campaign: str | None = None,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]: ...


class SQLiteMetricsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript(SQLITE_SCHEMA)
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(marketing_performance)"
                )
            }
            for column in (
                "product_cost_cents",
                "shipping_cost_cents",
                "fees_cents",
                "taxes_cents",
                "refunds_cents",
            ):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE marketing_performance ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0 CHECK ({column} >= 0)"
                    )
            connection.commit()

    def add(self, record: PerformanceRecord) -> bool:
        with closing(sqlite3.connect(self.path)) as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO marketing_performance
                   (event_id, occurred_on, channel, campaign, creative, impressions,
                    clicks, leads, conversations, sales, spend_cents, revenue_cents,
                    product_cost_cents, shipping_cost_cents, fees_cents, taxes_cents,
                    refunds_cents, evidence, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _record_values(record, for_sqlite=True),
            )
            connection.commit()
            return cursor.rowcount > 0

    def list_records(
        self,
        *,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        channel: str | None = None,
        campaign: str | None = None,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        where, params = _filters(date_from, date_to, channel, campaign, "?")
        query = (
            "SELECT * FROM marketing_performance"
            + where
            + " ORDER BY occurred_on DESC LIMIT ?"
        )
        params.append(max(1, min(limit, 10_000)))
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]


class PostgresMetricsStore:
    def __init__(self, database_url: str):
        import psycopg

        self.database_url = database_url
        with psycopg.connect(database_url) as connection:
            connection.execute(POSTGRES_SCHEMA)
            for column in (
                "product_cost_cents",
                "shipping_cost_cents",
                "fees_cents",
                "taxes_cents",
                "refunds_cents",
            ):
                connection.execute(
                    f"ALTER TABLE marketing_performance ADD COLUMN IF NOT EXISTS {column} BIGINT NOT NULL DEFAULT 0 CHECK ({column} >= 0)"
                )

    def add(self, record: PerformanceRecord) -> bool:
        import psycopg

        with psycopg.connect(self.database_url) as connection:
            cursor = connection.execute(
                """INSERT INTO marketing_performance
                   (event_id, occurred_on, channel, campaign, creative, impressions,
                    clicks, leads, conversations, sales, spend_cents, revenue_cents,
                    product_cost_cents, shipping_cost_cents, fees_cents, taxes_cents,
                    refunds_cents, evidence, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (event_id) DO NOTHING""",
                _record_values(record),
            )
            return cursor.rowcount > 0

    def list_records(
        self,
        *,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        channel: str | None = None,
        campaign: str | None = None,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        import psycopg
        from psycopg.rows import dict_row

        where, params = _filters(date_from, date_to, channel, campaign, "%s")
        query = (
            "SELECT * FROM marketing_performance"
            + where
            + " ORDER BY occurred_on DESC LIMIT %s"
        )
        params.append(max(1, min(limit, 10_000)))
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def _record_values(
    record: PerformanceRecord, *, for_sqlite: bool = False
) -> tuple[Any, ...]:
    occurred_on: dt.date | str = record.occurred_on
    created_at: dt.datetime | str = dt.datetime.now(dt.timezone.utc)
    if for_sqlite:
        occurred_on = record.occurred_on.isoformat()
        created_at = created_at.isoformat()
    return (
        record.event_id,
        occurred_on,
        record.channel,
        record.campaign,
        record.creative,
        record.impressions,
        record.clicks,
        record.leads,
        record.conversations,
        record.sales,
        _cents(record.spend_brl),
        _cents(record.revenue_brl),
        _cents(record.product_cost_brl),
        _cents(record.shipping_cost_brl),
        _cents(record.fees_brl),
        _cents(record.taxes_brl),
        _cents(record.refunds_brl),
        record.evidence,
        created_at,
    )


def _filters(
    date_from: dt.date | None,
    date_to: dt.date | None,
    channel: str | None,
    campaign: str | None,
    placeholder: str,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, value, operator in (
        ("occurred_on", date_from, ">="),
        ("occurred_on", date_to, "<="),
        ("channel", channel, "="),
        ("campaign", campaign, "="),
    ):
        if value is not None:
            clauses.append(f"{column} {operator} {placeholder}")
            params.append(value)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def make_metrics_repository(
    sqlite_path: str | Path, database_url: str | None = None
) -> MetricsRepository:
    if database_url:
        return PostgresMetricsStore(database_url)
    return SQLiteMetricsStore(sqlite_path)


def summarize_performance(records: list[dict[str, Any]]) -> dict[str, Any]:
    totals = _sum_records(records)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = f"{record['channel']} / {record['campaign']} / {record['creative']}"
        grouped[key].append(record)

    breakdown = [
        {"name": key, **_summary_block(_sum_records(group))}
        for key, group in grouped.items()
    ]
    breakdown.sort(
        key=lambda item: (
            item["totals"]["sales"],
            item["totals"]["revenue_brl"],
        ),
        reverse=True,
    )
    return {
        **_summary_block(totals),
        "records": len(records),
        "by_creative": breakdown,
        "definitions": {
            "cpa_brl": "gasto / vendas atribuídas",
            "cpl_brl": "gasto / leads",
            "ctr_percent": "cliques / impressões × 100",
            "lead_to_sale_percent": "vendas / leads × 100",
            "roas": "receita atribuída / gasto",
            "average_ticket_brl": "receita atribuída / vendas atribuídas",
            "pre_media_contribution_brl": "receita - produto - frete - taxas - impostos - estornos",
            "contribution_margin_brl": "receita - produto - frete - taxas - impostos - estornos - mídia",
        },
        "recommendations": _recommendations(totals),
    }


def _sum_records(records: list[dict[str, Any]]) -> defaultdict[str, int]:
    totals: defaultdict[str, int] = defaultdict(int)
    for record in records:
        for field in (
            "impressions",
            "clicks",
            "leads",
            "conversations",
            "sales",
            "spend_cents",
            "revenue_cents",
            "product_cost_cents",
            "shipping_cost_cents",
            "fees_cents",
            "taxes_cents",
            "refunds_cents",
        ):
            totals[field] += int(record[field])
    return totals


def _divide(numerator: int, denominator: int, multiplier: int = 1) -> float | None:
    return round((numerator / denominator) * multiplier, 4) if denominator else None


def _summary_block(totals: dict[str, int]) -> dict[str, Any]:
    spend = totals["spend_cents"]
    revenue = totals["revenue_cents"]
    return {
        "totals": {
            "impressions": totals["impressions"],
            "clicks": totals["clicks"],
            "leads": totals["leads"],
            "conversations": totals["conversations"],
            "sales": totals["sales"],
            "spend_brl": _money(spend),
            "revenue_brl": _money(revenue),
            "product_cost_brl": _money(totals["product_cost_cents"]),
            "shipping_cost_brl": _money(totals["shipping_cost_cents"]),
            "fees_brl": _money(totals["fees_cents"]),
            "taxes_brl": _money(totals["taxes_cents"]),
            "refunds_brl": _money(totals["refunds_cents"]),
        },
        "kpis": {
            "ctr_percent": _divide(totals["clicks"], totals["impressions"], 100),
            "cpc_brl": _money(round(spend / totals["clicks"]))
            if totals["clicks"]
            else None,
            "cpl_brl": _money(round(spend / totals["leads"]))
            if totals["leads"]
            else None,
            "cpa_brl": _money(round(spend / totals["sales"]))
            if totals["sales"]
            else None,
            "lead_to_sale_percent": _divide(totals["sales"], totals["leads"], 100),
            "click_to_sale_percent": _divide(totals["sales"], totals["clicks"], 100),
            "roas": _divide(revenue, spend),
            "average_ticket_brl": _money(round(revenue / totals["sales"]))
            if totals["sales"]
            else None,
            "pre_media_contribution_brl": _money(
                revenue
                - totals["product_cost_cents"]
                - totals["shipping_cost_cents"]
                - totals["fees_cents"]
                - totals["taxes_cents"]
                - totals["refunds_cents"]
            ),
            "contribution_margin_brl": _money(
                revenue
                - totals["product_cost_cents"]
                - totals["shipping_cost_cents"]
                - totals["fees_cents"]
                - totals["taxes_cents"]
                - totals["refunds_cents"]
                - spend
            ),
        },
    }


def _recommendations(totals: dict[str, int]) -> list[str]:
    notes: list[str] = []
    if totals["spend_cents"] == 0:
        notes.append("Registre o gasto confirmado para calcular CPC, CPL, CPA e ROAS.")
    if totals["impressions"] and not totals["clicks"]:
        notes.append("Há impressões sem cliques; revise gancho, enquadramento e CTA.")
    if totals["clicks"] and not totals["leads"]:
        notes.append(
            "Há cliques sem leads; revise a continuidade entre anúncio, página e atendimento."
        )
    if totals["leads"] and not totals["sales"]:
        notes.append(
            "Há leads sem vendas atribuídas; revise objeções e proposta antes de ampliar mídia."
        )
    if totals["sales"] and totals["revenue_cents"] == 0:
        notes.append("Registre a receita atribuída às vendas para calcular ROAS.")
    if not notes:
        notes.append(
            "Compare criativos pelo CPA e pela receita; altere uma variável por teste."
        )
    return notes
