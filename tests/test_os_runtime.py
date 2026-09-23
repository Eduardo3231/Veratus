from __future__ import annotations

from pathlib import Path

import pytest

from veratus_agents.operations import AGENT_REGISTRY, OperationalRuntime, TaskStatus


def _products() -> list[dict[str, object]]:
    return [
        {
            "id": "black-gmt",
            "name": "Black GMT",
            "status": "ACTIVE",
            "image": "black.png",
            "category": "Relógios",
            "cost": "65.00",
            "sale_price": "289.90",
        },
        {
            "id": "ocean-blue",
            "name": "Ocean Blue",
            "status": "ACTIVE",
            "image": "blue.png",
            "category": "Relógios",
            "cost": "65.00",
            "sale_price": "289.90",
        },
        {
            "id": "paused",
            "name": "Paused",
            "status": "PAUSED",
            "image": "paused.png",
            "category": "Relógios",
            "cost": "65.00",
            "sale_price": "289.90",
        },
    ]


def test_registry_has_exactly_thirteen_agents() -> None:
    assert len(AGENT_REGISTRY) == 13


def test_manager_command_traverses_real_hierarchy_and_blocks_external_writes(
    tmp_path: Path,
) -> None:
    runtime = OperationalRuntime(
        str(tmp_path / "runtime.sqlite3"), product_source=_products
    )
    report = runtime.execute(
        "Gerente, prepare todos os relógios ativos da Veratus para Mercado Livre, "
        "Shopee, TikTok Shop e Meta.",
        idempotency_key="prepare-all-v1",
    )

    assert report["status"] == "COMPLETED"
    assert set(report["agents_invoked"]) == set(AGENT_REGISTRY)
    assert report["external_writes"] == "BLOCKED"
    tasks = report["tasks"]
    assert all(item["status"] == TaskStatus.COMPLETED for item in tasks)

    quality = next(item for item in tasks if item["agent"] == "quality-agent")
    dependency_agents = {
        next(task for task in tasks if task["task_id"] == dependency)["agent"]
        for dependency in quality["depends_on"]
    }
    assert dependency_agents == {"copy-agent", "creative-agent", "pricing-agent"}

    shopee = next(item for item in tasks if item["agent"] == "shopee-agent")
    assert shopee["result"]["blocked"] == [
        {"product": "black-gmt", "reason": "BLOCKED_BY_CEO"}
    ]
    assert shopee["result"]["external_write"] == "BLOCKED_BY_GLOBAL_FLAG"
    assert len(report["audit_events"]) >= len(tasks) * 3


def test_runtime_is_idempotent_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    first = OperationalRuntime(str(path), product_source=_products).execute(
        "Mostre produtos prontos.", idempotency_key="ready-v1"
    )
    restarted = OperationalRuntime(str(path), product_source=_products)
    second = restarted.execute("Mostre produtos prontos.", idempotency_key="ready-v1")

    assert second["command_id"] == first["command_id"]
    assert len(second["tasks"]) == 1
    assert second["tasks"][0]["result"]["products"] == ["black-gmt", "ocean-blue"]


@pytest.mark.parametrize(
    ("message", "expected_action"),
    [
        ("Mostre produtos incompletos.", "PRODUCTS_INCOMPLETE"),
        ("Mostre pendências do Mercado Livre.", "CHANNEL_PENDING"),
        ("Mostre pendências da Shopee.", "CHANNEL_PENDING"),
        ("Mostre pendências do TikTok Shop.", "CHANNEL_PENDING"),
        ("Mostre pendências da Meta.", "CHANNEL_PENDING"),
        ("Mostre canais conectados.", "CHANNELS"),
        ("Mostre aprovações pendentes.", "APPROVALS"),
        ("Mostre erros.", "ERRORS"),
        ("Sincronize os drafts.", "SYNC"),
    ],
)
def test_operational_commands_are_structured_and_audited(
    tmp_path: Path, message: str, expected_action: str
) -> None:
    runtime = OperationalRuntime(
        str(tmp_path / f"{expected_action}.sqlite3"), product_source=_products
    )
    report = runtime.execute(message)

    assert report["status"] == "COMPLETED"
    assert report["tasks"][0]["action"] == expected_action
    assert any(event["action"] == "TASK_COMPLETED" for event in report["audit_events"])


def test_external_writes_cannot_be_enabled(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ExternalWriteGuard"):
        OperationalRuntime(
            str(tmp_path / "runtime.sqlite3"),
            product_source=_products,
            external_writes_enabled=True,
        )


def test_exact_production_audit_command_invokes_all_agents_without_writes(
    tmp_path: Path,
) -> None:
    runtime = OperationalRuntime(
        str(tmp_path / "runtime.sqlite3"), product_source=_products
    )

    report = runtime.execute(
        "Gerente, analise o estado atual da Veratus, os produtos ativos e as "
        "conexões dos marketplaces. Identifique as próximas ações operacionais "
        "sem publicar nada."
    )

    assert report["status"] == "COMPLETED"
    assert set(report["agents_invoked"]) == set(AGENT_REGISTRY)
    assert report["external_writes"] == "BLOCKED"
    consolidated = next(
        task
        for task in report["tasks"]
        if task["action"] == "CONSOLIDATE_OPERATIONAL_AUDIT"
    )
    assert consolidated["result"]["eligible_products"] == [
        "black-gmt",
        "ocean-blue",
    ]
    assert consolidated["result"]["external_writes"] is False


def test_prepare_blocks_product_with_unconfirmed_commercial_data(
    tmp_path: Path,
) -> None:
    def products():
        return [
            {
                "id": "unpriced",
                "name": "Unpriced",
                "status": "ACTIVE",
                "image": "asset.png",
                "category": "Relógios",
            }
        ]

    runtime = OperationalRuntime(
        str(tmp_path / "runtime.sqlite3"), product_source=products
    )
    report = runtime.execute(
        "Gerente, prepare todos os produtos tecnicamente aptos para distribuição "
        "e atualize a prontidão dos marketplaces."
    )

    quality = next(task for task in report["tasks"] if task["agent"] == "quality-agent")
    assert quality["result"]["eligible"] == []
    assert quality["result"]["blocked"] == [
        {"product": "unpriced", "missing_fields": ["sale_price", "cost"]}
    ]
    assert all(
        task["result"].get("drafts") == []
        for task in report["tasks"]
        if task["agent"] in OperationalRuntime.CHANNEL_AGENTS.values()
    )
