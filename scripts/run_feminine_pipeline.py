"""Execute the two founder-approved Phase 4 commands locally and persist the audit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from veratus_agents.data_discovery import discover_products
from veratus_agents.operations import OperationalRuntime

RUNTIME_PATH = ROOT / "runtime" / "phase4-feminine.sqlite3"
REPORT_PATH = ROOT / "docs" / "phase4-feminine-runtime-report.json"


def main() -> None:
    RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
    runtime = OperationalRuntime(
        str(RUNTIME_PATH),
        product_source=discover_products,
    )
    intake = runtime.execute(
        "Gerente, cadastre a nova coleção feminina da Veratus e prepare os produtos para revisão.",
        idempotency_key="phase4-feminine-intake-v1",
    )
    distribution = runtime.execute(
        "Gerente, prepare os produtos femininos aprovados para os marketplaces.",
        idempotency_key="phase4-feminine-distribution-v1",
    )
    report = {
        "phase": "VERATUS OS — FASE 4",
        "external_writes": False,
        "intake": intake,
        "marketplace_preparation": distribution,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"Intake: {intake['status']} ({len(intake['tasks'])} tasks)")
    print(
        "Marketplace preparation: "
        f"{distribution['status']} ({len(distribution['tasks'])} tasks)"
    )
    print(f"Audit written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
