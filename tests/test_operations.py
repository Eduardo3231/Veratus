import pytest

from veratus_agents.operations import (
    AGENT_REGISTRY,
    ApprovalStatus,
    ApprovalType,
    CommandEngine,
    CommandRoutedElsewhere,
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


def test_paid_acquisition_command_is_not_misrouted_to_catalogue_drafts():
    with pytest.raises(CommandRoutedElsewhere) as routed:
        OperationalRuntime.parse_command(
            "Gerente, acione o trabalhador de aquisição paga em modo SHADOW. "
            "Analise o Product Master, escolha um único produto e prepare um "
            "plano de campanha Meta Ads de R$20/dia. Não publique nada."
        )

    assert routed.value.endpoint == "/os/paid-media/plan"
    assert OperationalRuntime.parse_command(
        "Gerente, prepare todos os relógios ativos para Meta."
    ).channels == ("meta",)
    assert (
        OperationalRuntime.parse_command(
            "Quais aprovações pendentes de meta ads?"
        ).handler
        == "approvals"
    )
    assert (
        OperationalRuntime.parse_command(
            "Gerente, analise o estado atual da Veratus, os produtos ativos e as "
            "conexões dos marketplaces, incluindo tráfego pago."
        ).handler
        == "operational_audit"
    )


def test_registry_has_nineteen_operational_agents():
    assert len(AGENT_REGISTRY) == 19
    assert AGENT_REGISTRY["amazon-agent"].external_write is False
    assert AGENT_REGISTRY["general-manager"].level == 1
    assert AGENT_REGISTRY["shopee-agent"].external_write is False
    assert AGENT_REGISTRY["paid-acquisition-worker"].external_write is False


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
    assert result["drafts"] == [
        "mercado-livre",
        "shopee",
        "tiktok-shop",
        "meta",
        "amazon",
    ]
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


NEW_ROLES = {
    "customer-supervisor": (2, "general-manager"),
    "sales-agent": (3, "customer-supervisor"),
    "aftersales-agent": (3, "customer-supervisor"),
    "social-media-worker": (2, "general-manager"),
}


def test_new_roles_follow_the_founder_approved_table():
    from veratus_agents.operations import FORBIDDEN_FOR_NEW_ROLES

    expected = {
        "customer-supervisor": {
            "READ_PRODUCT",
            "READ_ORDERS",
            "CONVERSE",
            "HANDOFF",
            "REPORT",
        },
        "sales-agent": {
            "READ_PRODUCT",
            "CONVERSE",
            "CREATE_ORDER_DRAFT",
            "CREATE_PAYMENT_LINK",
            "REPORT",
        },
        "aftersales-agent": {"READ_ORDERS", "CONVERSE", "REPORT"},
        "social-media-worker": {
            "READ_PRODUCT",
            "CREATE_DRAFT",
            "SCHEDULE_APPROVED_CONTENT",
            "READ_INSIGHTS",
            "REPORT",
        },
    }
    for agent_id, (level, parent) in NEW_ROLES.items():
        definition = AGENT_REGISTRY[agent_id]
        assert (definition.level, definition.supervisor) == (level, parent)
        assert set(definition.permissions) == expected[agent_id]
        assert definition.external_write is False
        assert not definition.permissions & FORBIDDEN_FOR_NEW_ROLES


def test_gated_permissions_follow_channel_level_and_approval():
    from veratus_agents.operations import permission_allowed

    assert not permission_allowed("sales-agent", "CONVERSE", channel_mode="SHADOW")
    assert not permission_allowed("sales-agent", "CONVERSE", channel_mode="SUPERVISED")
    assert permission_allowed(
        "sales-agent", "CONVERSE", channel_mode="AUTO_SAFE", risk=0
    )
    assert not permission_allowed(
        "sales-agent", "CONVERSE", channel_mode="AUTO_SAFE", risk=1
    )
    assert permission_allowed("sales-agent", "CONVERSE", channel_mode="AUTO", risk=1)
    assert not permission_allowed(
        "sales-agent", "CONVERSE", channel_mode="AUTO", risk=2
    )
    assert not permission_allowed(
        "sales-agent", "CREATE_PAYMENT_LINK", channel_mode="AUTO_SAFE"
    )
    assert permission_allowed("sales-agent", "CREATE_PAYMENT_LINK", channel_mode="AUTO")
    assert not permission_allowed(
        "aftersales-agent", "CREATE_PAYMENT_LINK", channel_mode="AUTO"
    )
    assert permission_allowed(
        "social-media-worker", "SCHEDULE_APPROVED_CONTENT", item_status="APPROVED"
    )
    assert not permission_allowed(
        "social-media-worker",
        "SCHEDULE_APPROVED_CONTENT",
        item_status="PENDING_APPROVAL",
    )


def test_paid_acquisition_worker_keeps_its_own_contract():
    definition = AGENT_REGISTRY["paid-acquisition-worker"]

    # O laço de contratos não pode trocar o que a definição escreveu.
    assert definition.mission.startswith("planejar, monitorar e reconciliar")
    assert "attribution-reconciliation" in definition.capabilities
    assert "paid-media-adapter" in definition.tools
    assert definition.read_scope == ("product-master", "orders", "paid-media-readback")
    assert definition.write_scope == ("paid-media-draft",)
    assert "overspend" in definition.escalation_rules
    # Os demais seguem o contrato do time ou o padrão.
    assert AGENT_REGISTRY["sales-agent"].mission.startswith("conduzir a conversa")
    assert AGENT_REGISTRY["shopee-agent"].mission.startswith(
        "executar responsabilidades de"
    )


@pytest.mark.parametrize("agent_id", sorted(NEW_ROLES))
def test_new_roles_never_get_forbidden_permissions_at_runtime(monkeypatch, agent_id):
    from dataclasses import replace

    from veratus_agents.operations import (
        FORBIDDEN_FOR_NEW_ROLES,
        NEW_ROLE_IDS,
        permission_allowed,
    )

    assert NEW_ROLE_IDS == set(NEW_ROLES)
    # Mesmo com o registro adulterado, o papel novo não publica nem gasta.
    definition = AGENT_REGISTRY[agent_id]
    monkeypatch.setitem(
        AGENT_REGISTRY,
        agent_id,
        replace(
            definition, permissions=definition.permissions | FORBIDDEN_FOR_NEW_ROLES
        ),
    )
    engine = CommandEngine()
    for permission in sorted(FORBIDDEN_FOR_NEW_ROLES):
        assert not permission_allowed(
            agent_id,
            permission,
            channel_mode="AUTO",
            risk=0,
            item_status="APPROVED",
        )
        with pytest.raises(PermissionError):
            engine.authorize(agent_id, permission)
