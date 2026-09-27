"""One web worker, run as its own OS process by test_multiworker_state.py.

Usage: python tests/_state_worker.py SCENARIO DATABASE WORKER START_AT COUNT
DATABASE is a directory (SQLite files inside) or a postgresql:// URL.
Prints a JSON result on stdout.
"""

from __future__ import annotations

import json
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veratus_agents.operations import (
    ApprovalType,
    CommandEngine,
    CommandType,
    OperationalRuntime,
)
from veratus_agents.paid_media import (
    AutonomyMode,
    MetaAdsAdapter,
    PaidAcquisitionWorker,
    PaidMediaConfig,
    PaidMediaStore,
)

PRODUCT = {
    "id": "ocean-blue",
    "sku": "ocean-blue",
    "name": "Ocean Blue",
    "description": "Relógio azul Veratus.",
    "active": True,
    "missing_fields": [],
    "images": ["assets/catalog/ocean-blue.webp"],
    "fields": {"sale_price": {"value": "289.90"}, "cost": {"value": "65.00"}},
}


def _paths(database: str) -> tuple[str, str]:
    if database.startswith(("postgresql://", "postgres://")):
        return database, database
    return str(Path(database) / "runtime.sqlite3"), str(Path(database) / "paid.sqlite3")


def distinct(database: str, worker: str, count: int) -> dict:
    engine = CommandEngine(persistence_path=_paths(database)[0])
    for index in range(count):
        command = engine.submit(
            "founder",
            "general-manager",
            CommandType.REPORT,
            {"worker": worker, "index": index},
            f"{worker}-{index}",
        )
        task = engine.delegate(
            command, "general-manager", "REPORT", f"{worker}-{index}"
        )
        engine.tasks.start(task.task_id)
        engine.tasks.complete(task.task_id, {"worker": worker})
        engine.approvals.request(
            worker, ApprovalType.PUBLISH_APPROVAL, "teste", {"index": index}
        )
        engine._persist()
    return {"commands": [cid for cid in engine.commands]}


def same_keys(database: str, worker: str, count: int) -> dict:
    engine = CommandEngine(persistence_path=_paths(database)[0])
    return {
        f"shared-{index}": engine.submit(
            worker,
            "general-manager",
            CommandType.REPORT,
            {"index": index},
            f"shared-{index}",
        ).command_id
        for index in range(count)
    }


def paid_plan(database: str, worker: str, count: int) -> dict:
    runtime_path, paid_path = _paths(database)
    config = PaidMediaConfig(
        enabled=True,
        autonomy_mode=AutonomyMode.SHADOW,
        live_writes=False,
        daily_budget_brl=Decimal(20),
        test_max_spend_brl=Decimal(140),
    )
    planner = PaidAcquisitionWorker(
        PaidMediaStore(paid_path),
        CommandEngine(persistence_path=runtime_path),
        adapter=MetaAdsAdapter(),
        config=config,
    )
    return {
        f"plan-{index}": planner.plan([PRODUCT], idempotency_key=f"plan-{index}")[
            "experiment_id"
        ]
        for index in range(count)
    }


def execute(database: str, worker: str, count: int) -> dict:
    runtime = OperationalRuntime(_paths(database)[0], product_source=lambda: [PRODUCT])
    report = runtime.execute("Quais canais conectados?", idempotency_key="same-run")
    return {"command_id": report["command_id"], "status": report["status"]}


SCENARIOS = {
    "distinct": distinct,
    "same_keys": same_keys,
    "paid_plan": paid_plan,
    "execute": execute,
}


def main() -> None:
    scenario, database, worker, start_at, count = sys.argv[1:6]
    while time.time() < float(start_at):
        time.sleep(0.002)
    print(json.dumps(SCENARIOS[scenario](database, worker, int(count))))


if __name__ == "__main__":
    main()
