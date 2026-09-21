"""Generate a secret-free Phase 3 connection status artifact."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from veratus_agents.data_discovery import discovery_report
from veratus_agents.marketplace_clients import connection_status, credential_matrix
from veratus_agents.marketplace_ops import MarketplaceStore
from veratus_agents.marketplace_service import MarketplaceConnectionService
from veratus_agents.operations import OperationalRuntime


def main() -> None:
    runtime_dir = ROOT / "runtime" / "phase-3"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for name in ("runtime.sqlite3", "marketplace.sqlite3"):
        (runtime_dir / name).unlink(missing_ok=True)
    store = MarketplaceStore(runtime_dir / "marketplace.sqlite3")
    runtime = OperationalRuntime(
        str(runtime_dir / "runtime.sqlite3"),
        product_source=lambda: discovery_report()["products"],
        marketplace_store=store,
        connection_service=MarketplaceConnectionService(store),
    )
    command = runtime.execute(
        "Gerente, verifique os marketplaces conectados e atualize a prontidão dos nove produtos.",
        idempotency_key="phase-3-read-only-connection-v1",
    )
    channels = {
        item["channel"]: item for item in store.connection_checks()
    } or connection_status()
    evidence = {
        "mode": os.getenv("MARKETPLACE_MODE", "LOCAL"),
        "publish_enabled": os.getenv("PUBLISH_ENABLED", "false").lower() == "true",
        "command_id": command["command_id"],
        "task_ids": [item["task_id"] for item in command["tasks"]],
        "agents_invoked": command["agents_invoked"],
        "channels": channels,
        "credential_matrix": credential_matrix(),
        "api_reads": {
            channel: item.get("api_reads", []) for channel, item in channels.items()
        },
        "category_discovered": store.category_details(),
        "attributes_discovered": store.attributes(),
        "draft_validated": False,
        "first_real_publish_candidate": None,
        "blockers": [
            "CREDENTIALS_MISSING",
            "ACCOUNT_NOT_TESTED",
            "CATEGORY_DISCOVERY_BLOCKED_BY_CREDENTIALS",
            "ATTRIBUTES_NOT_TESTED",
            "LOGISTICS_NOT_TESTED",
            "READBACK_NOT_TESTED",
        ],
        "ready_for_first_live_publish": False,
        "errors": [],
    }
    target = ROOT / "docs" / "connection-evidence.json"
    target.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "command_id": command["command_id"],
                "tasks": len(command["tasks"]),
                "agents": command["agents_invoked"],
                "ready": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
