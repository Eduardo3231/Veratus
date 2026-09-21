import pytest

from veratus_agents.operations import (
    AGENT_REGISTRY,
    ApprovalStatus,
    ApprovalType,
    CommandEngine,
    CommandType,
    DependencyError,
    OperationalRuntime,
    PermissionError,
    TaskStatus,
    simulate_prepare_distribution,
)


def test_parse_mercado_livre_readiness_command():
    parsed = OperationalRuntime.parse_command(
        "Gerente, valide a conexão do Mercado Livre e atualize a prontidão dos produtos."
    )

    assert parsed.handler == "refresh_mercado_livre"
    assert parsed.channels == ("mercado-livre",)


def test_registry_has_thirteen_operational_agents():
    assert len(AGENT_REGISTRY) == 13
    assert AGENT_REGISTRY["general-manager"].level == 1
    assert AGENT_REGISTRY["shopee-agent"].external_write is False


def test_command_is_idempotent_and_ceo_has_priority(tmp_path):
    path = str(tmp_path / "runtime.sqlite3")
    engine = CommandEngine(persistence_path=path)
    first = engine.submit(
        "founder", "general-manager", CommandType.REPORT, {}, "report-today"
    )
    second = engine.submit(
        "founder", "general-manager", CommandType.REPORT, {}, "report-today"
    )

    assert first.command_id == second.command_id
    assert first.priority.value == "CEO_OVERRIDE"
    assert any(event.action == "COMMAND_CREATED" for event in engine.tasks.audit)
    restarted = CommandEngine(persistence_path=path)
    assert first.command_id in restarted.commands


def test_permissions_block_publish_capability():
    engine = CommandEngine()

    with pytest.raises(PermissionError):
        engine.authorize("shopee-agent", "PUBLISH")

    engine.authorize("shopee-agent", "CREATE_DRAFT")


def test_simulation_delegates_and_never_publishes():
    result = simulate_prepare_distribution(product_count=8)

    assert result["status"] == "COMPLETED"
    assert result["drafts"] == ["mercado-livre", "shopee", "tiktok-shop", "meta"]
    assert all(task["status"] == TaskStatus.COMPLETED for task in result["tasks"])
    assert all(
        task["result"].get("external_write") is not True
        for task in result["tasks"]
        if task["result"]
    )
    assert any(event["action"] == "CEO_OVERRIDE" for event in result["audit"])


def test_dependencies_block_until_parent_completes():
    engine = CommandEngine()
    command = engine.submit(
        "founder", "general-manager", CommandType.REPORT, {}, "dependency-test"
    )
    parent = engine.delegate(command, "general-manager", "PLAN", "product")
    child = engine.delegate(
        command, "product-supervisor", "QA", "product", depends_on=[parent.task_id]
    )

    assert child.status is TaskStatus.BLOCKED
    with pytest.raises(DependencyError):
        engine.tasks.start(child.task_id)

    engine.tasks.start(parent.task_id)
    engine.tasks.complete(parent.task_id, {})
    child.blocked_by.clear()
    engine.tasks.start(child.task_id)
    assert child.status is TaskStatus.RUNNING


def test_approval_reject_and_retry_limit_are_audited():
    engine = CommandEngine()
    approval = engine.approvals.request(
        "quality-agent",
        ApprovalType.PUBLISH_APPROVAL,
        "Publicação requer fundador",
        {"sku": "VRT-001"},
    )
    assert (
        engine.approvals.resolve(approval.request_id, ApprovalStatus.REJECTED).status
        is ApprovalStatus.REJECTED
    )

    command = engine.submit(
        "founder", "shopee-agent", CommandType.CREATE_DRAFT, {}, "retry-test"
    )
    task = engine.delegate(command, "shopee-agent", "CREATE_DRAFT", "VRT-001")
    engine.tasks.start(task.task_id)
    for _ in range(task.max_retries + 1):
        engine.tasks.fail(task.task_id, "provider_unavailable")
    assert task.status is TaskStatus.ESCALATED
    assert task.retry_count == task.max_retries


def test_ceo_override_blocks_shopee_without_external_publish():
    result = simulate_prepare_distribution(8, blocked_sku="VRT-REL-AUT-0001")

    assert "shopee" not in result["drafts"]
    assert any(event["action"] == "CEO_OVERRIDE" for event in result["audit"])
    assert all(
        task["result"].get("external_write") is not True
        for task in result["tasks"]
        if task["result"]
    )
