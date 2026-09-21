"""Execute the handoff command set locally and write sanitised operational evidence."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RUNTIME = ROOT / "runtime" / "handoff-validation"
EVIDENCE = ROOT / "docs" / "veratus-os-execution-evidence.json"
ADMIN_TOKEN = "local-validation-token"

COMMANDS = [
    "Gerente, prepare todos os relógios ativos da Veratus para Mercado Livre, Shopee, TikTok Shop e Meta.",
    "Mostre produtos incompletos.",
    "Mostre produtos prontos.",
    "Mostre pendências do Mercado Livre.",
    "Mostre pendências da Shopee.",
    "Mostre pendências do TikTok Shop.",
    "Mostre pendências da Meta.",
    "Mostre canais conectados.",
    "Mostre aprovações pendentes.",
    "Mostre erros.",
    "Sincronize os drafts.",
]


def main() -> None:
    shutil.rmtree(RUNTIME, ignore_errors=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "VERATUS_ADMIN_TOKEN": ADMIN_TOKEN,
            "VERATUS_AGENT_RUNTIME_DIR": str(RUNTIME),
            "MARKETPLACE_MODE": "LOCAL",
            "PUBLISH_ENABLED": "false",
            "RATE_LIMIT_REQUESTS": "1000",
        }
    )

    from integrations import webhook

    webhook._runtime_engine = None
    webhook._operational_runtime_instance = None
    webhook._marketplace_store_cached.cache_clear()
    headers = {"X-Veratus-Admin-Token": ADMIN_TOKEN}
    client = webhook.app.test_client()

    runs = []
    for command in COMMANDS:
        response = client.post(
            "/os/commands", headers=headers, json={"message": command}
        )
        runs.append(
            {
                "command": command,
                "http_status": response.status_code,
                "response": response.json,
            }
        )

    listings = client.get("/os/listings", headers=headers).json["listings"]
    readiness = client.get("/os/readiness", headers=headers).json
    status_before_restart = client.get("/os/status", headers=headers).json

    store = webhook._marketplace_store()
    approval = store.request_approval(
        "mercado-livre", "arctic-white", {"sku": "arctic-white", "price": "289.90"}
    )
    resolved = store.resolve_approval(approval["request_id"], "APPROVED")
    from veratus_agents.marketplace_ops import ExternalWriteGuard

    product = {"sku": "arctic-white", "status": "APPROVED"}
    approval_view = type("Approval", (), {"status": resolved["status"]})()
    allowed, reasons = ExternalWriteGuard(store).check(
        "mercado-livre", product, approval=approval_view, publish_enabled=False
    )

    webhook._runtime_engine = None
    webhook._operational_runtime_instance = None
    status_after_restart = client.get("/os/status", headers=headers).json

    evidence = {
        "mode": "LOCAL",
        "publish_enabled": False,
        "runs": runs,
        "summary": {
            "commands": len(runs),
            "main_agents_invoked": len(
                runs[0]["response"]["execution"]["agents_invoked"]
            ),
            "listings_total": len(listings),
            "drafts": sum(item["state"] == "DRAFT" for item in listings),
            "blocked": sum(item["state"] == "BLOCKED" for item in listings),
            "black_gmt_shopee": next(
                item
                for item in listings
                if item["sku"] == "black-gmt" and item["channel"] == "shopee"
            )["state"],
            "products_complete": readiness["products_complete"],
            "publish_gate": {
                "approval": resolved["status"],
                "allowed": allowed,
                "reasons": reasons,
            },
            "restart_persistence": {
                "commands_before": status_before_restart["command_count"],
                "commands_after": status_after_restart["command_count"],
                "tasks_before": status_before_restart["task_count"],
                "tasks_after": status_after_restart["task_count"],
            },
        },
    }
    EVIDENCE.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(evidence["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
