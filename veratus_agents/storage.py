from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL,
    draft_json TEXT,
    review_json TEXT,
    metrics_json TEXT,
    human_decision TEXT,
    human_note TEXT,
    decided_at TEXT,
    decided_by TEXT,
    approved_reply TEXT,
    approved_reply_hash TEXT,
    policy_version TEXT,
    event_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_session ON agent_runs(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status, created_at);
CREATE TABLE IF NOT EXISTS agent_run_events (
    audit_event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_run_events_run ON agent_run_events(run_id, created_at);
"""


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class RunRepository(Protocol):
    def get_or_create_run(
        self,
        *,
        session_id: str,
        source: str,
        payload: dict[str, Any],
        event_id: str | None = None,
    ) -> tuple[str, bool]: ...
    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        draft: dict[str, Any],
        review: dict[str, Any],
        metrics: dict[str, Any] | None = None,
    ) -> bool: ...
    def fail_run(self, run_id: str, message: str) -> None: ...
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...
    def list_runs(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]: ...
    def decide(
        self,
        run_id: str,
        *,
        decision: str,
        actor: str,
        note: str = "",
        approved_reply: str | None = None,
    ) -> bool: ...


class RunStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_runs)")}
            if "event_id" not in columns:
                conn.execute("ALTER TABLE agent_runs ADD COLUMN event_id TEXT")
            if "metrics_json" not in columns:
                conn.execute("ALTER TABLE agent_runs ADD COLUMN metrics_json TEXT")
            for column in (
                "decided_at",
                "decided_by",
                "approved_reply",
                "approved_reply_hash",
                "policy_version",
            ):
                if column not in columns:
                    conn.execute(f"ALTER TABLE agent_runs ADD COLUMN {column} TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_event ON agent_runs(session_id, source, event_id) WHERE event_id IS NOT NULL"
            )
            conn.commit()

    def create_run(
        self, *, session_id: str, source: str, payload: dict[str, Any]
    ) -> str:
        run_id, _ = self.get_or_create_run(
            session_id=session_id, source=source, payload=payload
        )
        return run_id

    def get_or_create_run(
        self,
        *,
        session_id: str,
        source: str,
        payload: dict[str, Any],
        event_id: str | None = None,
    ) -> tuple[str, bool]:
        run_id = f"run_{uuid.uuid4().hex}"
        now = _utcnow()
        with closing(sqlite3.connect(self.db_path)) as conn:
            try:
                conn.execute(
                    """INSERT INTO agent_runs
                       (run_id, created_at, updated_at, session_id, source, status, input_json, event_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        now,
                        now,
                        session_id,
                        source,
                        "running",
                        json.dumps(payload, ensure_ascii=False),
                        event_id,
                    ),
                )
                conn.commit()
                return run_id, True
            except sqlite3.IntegrityError:
                if event_id is None:
                    raise
                row = conn.execute(
                    "SELECT run_id FROM agent_runs WHERE session_id = ? AND source = ? AND event_id = ?",
                    (session_id, source, event_id),
                ).fetchone()
                if row is None:
                    raise
                return row[0], False

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
        with closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.execute(
                """
                UPDATE agent_runs SET updated_at = ?, status = ?, draft_json = ?, review_json = ?, metrics_json = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (
                    _utcnow(),
                    status,
                    json.dumps(draft, ensure_ascii=False),
                    json.dumps(review, ensure_ascii=False),
                    json.dumps(metrics or {}, ensure_ascii=False),
                    run_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def fail_run(self, run_id: str, message: str) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE agent_runs SET updated_at = ?, status = ?, review_json = ? WHERE run_id = ? AND status = 'running'",
                (
                    _utcnow(),
                    "failed",
                    json.dumps({"error": message}, ensure_ascii=False),
                    run_id,
                ),
            )
            conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return self._decode_run(dict(row))

    def list_runs(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            if status:
                rows = conn.execute(
                    "SELECT * FROM agent_runs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._decode_run(dict(row)) for row in rows]

    @staticmethod
    def _decode_run(data: dict[str, Any]) -> dict[str, Any]:
        for key in ("input_json", "draft_json", "review_json", "metrics_json"):
            raw = data.pop(key)
            data[key.removesuffix("_json")] = json.loads(raw) if raw else None
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
        now = _utcnow()
        with closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.execute(
                """
                UPDATE agent_runs
                SET updated_at = ?, status = ?, human_decision = ?, human_note = ?,
                    decided_at = ?, decided_by = ?, approved_reply = ?,
                    approved_reply_hash = ?, policy_version = ?
                WHERE run_id = ? AND status = 'pending_review' AND human_decision IS NULL
                """,
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
                conn.execute(
                    """INSERT INTO agent_run_events
                       (audit_event_id, run_id, event_type, actor, payload_json, created_at)
                       VALUES (?, ?, 'human_decision', ?, ?, ?)""",
                    (
                        f"audit_{uuid.uuid4().hex}",
                        run_id,
                        actor,
                        json.dumps(
                            {
                                "decision": decision,
                                "note": note[:2000],
                                "approved_reply_hash": reply_hash,
                                "policy_version": "commercial-v1",
                            },
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
            conn.commit()
            return cursor.rowcount > 0


def make_run_repository(
    db_path: str | Path, database_url: str | None = None
) -> RunRepository:
    if database_url:
        from .postgres_storage import PostgresRunStore

        return PostgresRunStore(database_url)
    return RunStore(db_path)
