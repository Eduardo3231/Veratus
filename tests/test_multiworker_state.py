"""Two real worker processes share one database without losing or duplicating state.

Each scenario starts two OS processes (as gunicorn does with WEB_CONCURRENCY=2)
that begin at the same instant. SQLite always runs; PostgreSQL runs when
VERATUS_TEST_DATABASE_URL points to a disposable database.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from veratus_agents.operations import CommandEngine, OperationalRuntime
from veratus_agents.paid_media import PaidMediaStore

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tests" / "_state_worker.py"
POSTGRES_URL = os.getenv("VERATUS_TEST_DATABASE_URL", "").strip()
COUNT = 12

BACKENDS = ["sqlite"] + (["postgres"] if POSTGRES_URL else [])


@pytest.fixture(params=BACKENDS)
def database(request, tmp_path) -> str:
    if request.param == "sqlite":
        return str(tmp_path)
    import psycopg

    with psycopg.connect(POSTGRES_URL, autocommit=True) as db:
        db.execute("DROP TABLE IF EXISTS operational_runtime_snapshot")
        db.execute("DROP TABLE IF EXISTS paid_media_runtime_snapshot")
    return POSTGRES_URL


def _paths(database: str) -> tuple[str, str]:
    if database.startswith("postgresql://"):
        return database, database
    return str(Path(database) / "runtime.sqlite3"), str(Path(database) / "paid.sqlite3")


def _run_two(scenario: str, database: str, count: int = COUNT) -> list[dict]:
    start_at = time.time() + 1.5
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                str(WORKER),
                scenario,
                database,
                name,
                str(start_at),
                str(count),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for name in ("worker-a", "worker-b")
    ]
    results = []
    for process in processes:
        out, err = process.communicate(timeout=180)
        assert process.returncode == 0, err
        results.append(json.loads(out.strip().splitlines()[-1]))
    return results


def test_no_worker_erases_the_other_workers_records(database: str) -> None:
    _run_two("distinct", database)
    stored = CommandEngine(persistence_path=_paths(database)[0])

    keys = {f"worker-{side}-{index}" for side in "ab" for index in range(COUNT)}
    assert keys <= set(stored.idempotency)
    assert len(stored.commands) == 2 * COUNT
    completed = [t for t in stored.tasks.tasks.values() if t.result.get("worker")]
    assert len(completed) == 2 * COUNT
    assert len(stored.approvals.requests) == 2 * COUNT
    created = [e for e in stored.tasks.audit if e.action == "COMMAND_CREATED"]
    assert len(created) == 2 * COUNT


def test_one_idempotency_key_creates_one_command(database: str) -> None:
    first, second = _run_two("same_keys", database)
    stored = CommandEngine(persistence_path=_paths(database)[0])

    assert first == second
    assert len(stored.commands) == COUNT
    assert all(stored.idempotency[key] == first[key] for key in first)


def test_one_plan_key_creates_one_paid_media_experiment(database: str) -> None:
    first, second = _run_two("paid_plan", database, count=6)
    store = PaidMediaStore(_paths(database)[1])

    assert first == second
    assert len(store.snapshot.experiments) == 6
    assert set(store.snapshot.idempotency) >= set(first)


def test_same_command_runs_once_across_workers(database: str, tmp_path) -> None:
    solo = OperationalRuntime(
        str(tmp_path / "solo.sqlite3"),
        product_source=lambda: [
            {"id": "ocean-blue", "name": "Ocean Blue", "status": "ACTIVE"}
        ],
    ).execute("Quais canais conectados?", idempotency_key="same-run")

    first, second = _run_two("execute", database)
    stored = CommandEngine(persistence_path=_paths(database)[0])

    assert first["command_id"] == second["command_id"]
    tasks = [
        task
        for task in stored.tasks.tasks.values()
        if task.command_id == first["command_id"]
    ]
    assert len(tasks) == len(solo["tasks"])
