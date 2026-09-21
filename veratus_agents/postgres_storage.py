from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    session_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','pending_review','needs_revision','approved','rejected','failed')),
    input_json JSONB NOT NULL,
    draft_json JSONB,
    review_json JSONB,
    metrics_json JSONB,
    human_decision TEXT CHECK (human_decision IS NULL OR human_decision IN ('approved','rejected')),
    human_note TEXT,
    decided_at TIMESTAMPTZ,
    decided_by TEXT,
    approved_reply TEXT,
    approved_reply_hash TEXT,
    policy_version TEXT,
    event_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_session ON agent_runs(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status, created_at);
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS metrics_json JSONB;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS event_id TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS decided_by TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS approved_reply TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS approved_reply_hash TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS policy_version TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_event
    ON agent_runs(session_id, source, event_id) WHERE event_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS agent_session_items (
    sequence BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    item_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_session_items_session
    ON agent_session_items(session_id, sequence);
CREATE TABLE IF NOT EXISTS agent_run_events (
    audit_event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_run_events_run
    ON agent_run_events(run_id, created_at);
"""


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class PostgresRunStore:
    def __init__(self, database_url: str):
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL deve apontar para PostgreSQL")
        self.database_url = database_url
        self._ensure_schema()

    @staticmethod
    def _driver():
        try:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Instale psycopg[binary] para usar PostgreSQL") from exc
        return psycopg, dict_row, Jsonb

    def _ensure_schema(self) -> None:
        psycopg, _, _ = self._driver()
        with psycopg.connect(self.database_url) as connection:
            connection.execute(SCHEMA)

    def get_or_create_run(
        self,
        *,
        session_id: str,
        source: str,
        payload: dict[str, Any],
        event_id: str | None = None,
    ) -> tuple[str, bool]:
        psycopg, _, Jsonb = self._driver()
        run_id = f"run_{uuid.uuid4().hex}"
        now = _utcnow()
        with psycopg.connect(self.database_url) as connection:
            if event_id:
                row = connection.execute(
                    """INSERT INTO agent_runs
                       (run_id, created_at, updated_at, session_id, source, status, input_json, event_id)
                       VALUES (%s,%s,%s,%s,%s,'running',%s,%s)
                       ON CONFLICT (session_id, source, event_id) WHERE event_id IS NOT NULL
                       DO NOTHING RETURNING run_id""",
                    (run_id, now, now, session_id, source, Jsonb(payload), event_id),
                ).fetchone()
                if row:
                    return row[0], True
                existing = connection.execute(
                    "SELECT run_id FROM agent_runs WHERE session_id=%s AND source=%s AND event_id=%s",
                    (session_id, source, event_id),
                ).fetchone()
                if existing is None:
                    raise RuntimeError("Falha ao resolver evento idempotente")
                return existing[0], False
            connection.execute(
                """INSERT INTO agent_runs
                   (run_id, created_at, updated_at, session_id, source, status, input_json)
                   VALUES (%s,%s,%s,%s,%s,'running',%s)""",
                (run_id, now, now, session_id, source, Jsonb(payload)),
            )
        return run_id, True

    def create_run(
        self, *, session_id: str, source: str, payload: dict[str, Any]
    ) -> str:
        return self.get_or_create_run(
            session_id=session_id, source=source, payload=payload
        )[0]

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        draft: dict[str, Any],
        review: dict[str, Any],
        metrics: dict[str, Any] | None = None,
    ) -> bool:
        if status not in {"pending_review", "needs_revision"}:
            raise ValueError("status final inválido")
        psycopg, _, Jsonb = self._driver()
        with psycopg.connect(self.database_url) as connection:
            cursor = connection.execute(
                """UPDATE agent_runs SET updated_at=%s, status=%s, draft_json=%s,
                   review_json=%s, metrics_json=%s WHERE run_id=%s AND status='running'""",
                (
                    _utcnow(),
                    status,
                    Jsonb(draft),
                    Jsonb(review),
                    Jsonb(metrics or {}),
                    run_id,
                ),
            )
            return cursor.rowcount > 0

    def fail_run(self, run_id: str, message: str) -> None:
        _, _, Jsonb = self._driver()
        import psycopg

        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "UPDATE agent_runs SET updated_at=%s,status='failed',review_json=%s WHERE run_id=%s AND status='running'",
                (_utcnow(), Jsonb({"error": message}), run_id),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        psycopg, dict_row, _ = self._driver()
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id=%s", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return self._decode_run(dict(row))

    def list_runs(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        psycopg, dict_row, _ = self._driver()
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            if status:
                rows = connection.execute(
                    "SELECT * FROM agent_runs WHERE status=%s ORDER BY created_at DESC LIMIT %s",
                    (status, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                ).fetchall()
        return [self._decode_run(dict(row)) for row in rows]

    @staticmethod
    def _decode_run(data: dict[str, Any]) -> dict[str, Any]:
        for source, target in (
            ("input_json", "input"),
            ("draft_json", "draft"),
            ("review_json", "review"),
            ("metrics_json", "metrics"),
        ):
            data[target] = data.pop(source)
        for key in ("created_at", "updated_at"):
            if hasattr(data[key], "isoformat"):
                data[key] = data[key].isoformat()
        return data

    def decide(
        self,
        run_id: str,
        *,
        decision: str,
        actor: str,
        note: str = "",
        approved_reply: str | None = None,
    ) -> bool:
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision deve ser approved ou rejected")
        actor = actor.strip()[:100]
        if not actor:
            raise ValueError("actor é obrigatório")
        if decision == "approved" and not (approved_reply or "").strip():
            raise ValueError("approved_reply é obrigatório para aprovação")
        final_reply = (approved_reply or "").strip()[:1800] or None
        reply_hash = (
            hashlib.sha256(final_reply.encode("utf-8")).hexdigest()
            if final_reply
            else None
        )
        psycopg, _, Jsonb = self._driver()
        now = _utcnow()
        with psycopg.connect(self.database_url) as connection:
            cursor = connection.execute(
                """UPDATE agent_runs SET updated_at=%s,status=%s,human_decision=%s,human_note=%s,
                   decided_at=%s,decided_by=%s,approved_reply=%s,approved_reply_hash=%s,policy_version=%s
                   WHERE run_id=%s AND status='pending_review' AND human_decision IS NULL""",
                (
                    now,
                    decision,
                    decision,
                    note[:2000],
                    now,
                    actor,
                    final_reply,
                    reply_hash,
                    "commercial-v1",
                    run_id,
                ),
            )
            if cursor.rowcount:
                connection.execute(
                    """INSERT INTO agent_run_events
                       (audit_event_id, run_id, event_type, actor, payload_json, created_at)
                       VALUES (%s,%s,'human_decision',%s,%s,%s)""",
                    (
                        f"audit_{uuid.uuid4().hex}",
                        run_id,
                        actor,
                        Jsonb(
                            {
                                "decision": decision,
                                "note": note[:2000],
                                "approved_reply_hash": reply_hash,
                                "policy_version": "commercial-v1",
                            }
                        ),
                        now,
                    ),
                )
            return cursor.rowcount > 0
