"""Leads do endpoint público ``/webhook``, guardados no banco.

Substitui o CSV em ``/tmp``, que o Render apaga a cada deploy e que dois workers
gravavam sem trava entre processos. O webhook nunca registra consentimento: um
operador revisa a origem antes de qualquer sincronização de e-mail.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .sqlstore import SqlStore

CONSENT_VALUES = {"1", "true", "sim", "yes"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    lead_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    email TEXT NOT NULL,
    whatsapp TEXT NOT NULL,
    source TEXT NOT NULL,
    campaign_json TEXT NOT NULL,
    consent TEXT NOT NULL,
    consent_source TEXT NOT NULL,
    import_ref TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_leads_received ON leads(received_at);
"""


def has_valid_consent(consent: str | None, consent_source: str | None) -> bool:
    return (consent or "").strip().lower() in CONSENT_VALUES and bool(
        (consent_source or "").strip()
    )


def import_ref(row: dict[str, str]) -> str:
    """Stable key so importing the same CSV twice adds nothing."""
    key = "|".join(
        (row.get(field) or "").strip().lower()
        for field in ("timestamp", "email", "whatsapp")
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


class LeadStore(SqlStore):
    SCHEMA = SCHEMA

    def add(
        self,
        *,
        email: str,
        whatsapp: str,
        source: str,
        campaign: dict[str, str] | None = None,
        consent: str = "",
        consent_source: str = "",
        received_at: str | None = None,
        reference: str | None = None,
    ) -> bool:
        """Insert one lead; return False when ``reference`` was already imported."""
        with self._connect() as (db, mark):
            return bool(
                db.execute(
                    self._sql(
                        "INSERT INTO leads (lead_id, received_at, email, whatsapp, "
                        "source, campaign_json, consent, consent_source, import_ref) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT (import_ref) DO NOTHING",
                        mark,
                    ),
                    [
                        f"lead_{uuid.uuid4().hex}",
                        received_at or datetime.now(timezone.utc).isoformat(),
                        email[:320],
                        whatsapp[:40],
                        source[:200],
                        json.dumps(campaign or {}, ensure_ascii=False),
                        consent[:20],
                        consent_source[:200],
                        reference,
                    ],
                ).rowcount
            )

    def count(self) -> int:
        with self._connect() as (db, _):
            row = db.execute("SELECT COUNT(*) AS total FROM leads").fetchone()
        return int(row["total"])

    def authorized(self) -> list[dict[str, Any]]:
        """Leads whose consent and its source were recorded by an operator."""
        with self._connect() as (db, _):
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT email, whatsapp, source, consent, consent_source "
                    "FROM leads ORDER BY received_at"
                ).fetchall()
            ]
        return [
            row
            for row in rows
            if has_valid_consent(row["consent"], row["consent_source"])
        ]
