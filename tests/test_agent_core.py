from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from integrations.push_to_mailerlite import read_authorized_leads
from veratus_agents.catalog import (
    find_product,
    list_products,
    load_catalog,
    validate_catalog_sync,
)
from veratus_agents.metrics import SQLiteMetricsStore, summarize_performance
from veratus_agents.policy import hard_review_customer_reply
from veratus_agents.product_master import (
    PricingInput,
    ProductCreate,
    ProductStatus,
    ProductUpdate,
    SQLiteProductStore,
    calculate_pricing,
)
from veratus_agents.schemas import PerformanceRecord, ReviewDecision, SalesDraft
from veratus_agents.storage import RunStore, make_run_repository
from veratus_agents.workflow import AgentConfigurationError, _session_id


def test_catalog_has_eighteen_products_and_landing_uses_catalog_products():
    assert len(list_products()) == 18
    assert len({product["id"] for product in load_catalog()}) == 18
    ok, diff = validate_catalog_sync()
    assert ok, diff


def test_find_product_by_name_and_style():
    assert find_product("Ocean Blue")["id"] == "ocean-blue"
    assert find_product("modelo inexistente") is None
    assert {p["id"] for p in list_products("clássico")} >= {
        "arctic-white",
        "platinum-classic",
        "silver-prestige",
        "bronze-heritage",
    }


def test_run_store_requires_human_decision(tmp_path: Path):
    store = RunStore(tmp_path / "runs.sqlite3")
    run_id = store.create_run(
        session_id="session-test",
        source="test",
        payload={"message": "olá"},
    )
    store.finish_run(
        run_id,
        status="pending_review",
        draft={"customer_reply": "Olá"},
        review={"approved_for_human_review": True},
    )
    run = store.get_run(run_id)
    assert run["status"] == "pending_review"
    assert run["human_decision"] is None
    assert (
        store.decide(
            run_id,
            decision="approved",
            actor="operador-teste",
            note="revisado",
            approved_reply="Olá. Posso confirmar os dados atuais com a equipe.",
        )
        is True
    )
    assert store.get_run(run_id)["status"] == "approved"
    assert store.get_run(run_id)["approved_reply_hash"]
    assert (
        store.decide(
            run_id, decision="rejected", actor="operador-teste", note="tarde demais"
        )
        is False
    )
    assert store.get_run(run_id)["human_decision"] == "approved"
    with pytest.raises(ValueError):
        store.decide(run_id, decision="invalid", actor="operador-teste")
    assert store.get_run(run_id)["status"] == "approved"


def test_hard_policy_blocks_unverified_commercial_claims():
    assert hard_review_customer_reply("Hoje está R$ 199,90 com frete incluso.")
    assert hard_review_customer_reply("Chega amanhã.")
    assert hard_review_customer_reply("Tem garantia de 2 anos.")
    assert hard_review_customer_reply("Meu OPENAI_API_KEY é secreto.")
    assert hard_review_customer_reply("Sai por 299 reais.")
    assert hard_review_customer_reply("Temos em estoque.")
    assert hard_review_customer_reply("Aceitamos 12x no cartão.")
    assert hard_review_customer_reply("Reservo para você.")
    assert hard_review_customer_reply("Garantia por um ano.")
    assert hard_review_customer_reply("Movimento automático japonês.")
    assert (
        hard_review_customer_reply("Vou confirmar o valor, frete e prazo com a equipe.")
        == []
    )


def test_event_id_deduplicates_within_customer_and_source(tmp_path: Path):
    store = RunStore(tmp_path / "runs.sqlite3")
    first, created = store.get_or_create_run(
        session_id="session-a",
        source="api",
        payload={"message": "Olá"},
        event_id="evt-1",
    )
    repeat, created_repeat = store.get_or_create_run(
        session_id="session-a",
        source="api",
        payload={"message": "Olá"},
        event_id="evt-1",
    )
    another, another_created = store.get_or_create_run(
        session_id="session-b",
        source="api",
        payload={"message": "Olá"},
        event_id="evt-1",
    )
    assert created and not created_repeat and another_created
    assert first == repeat and first != another


def test_needs_revision_cannot_be_approved(tmp_path: Path):
    store = RunStore(tmp_path / "runs.sqlite3")
    run_id = store.create_run(
        session_id="session-test", source="test", payload={"message": "teste"}
    )
    store.finish_run(
        run_id,
        status="needs_revision",
        draft={"customer_reply": "R$ 299"},
        review={"gate_passed": False},
    )
    assert (
        store.decide(
            run_id,
            decision="approved",
            actor="operador-teste",
            approved_reply="resposta",
        )
        is False
    )
    assert store.get_run(run_id)["human_decision"] is None


def test_session_id_needs_secret_and_excludes_customer_identifier():
    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(AgentConfigurationError),
    ):
        _session_id("cliente@example.com")
    with patch.dict("os.environ", {"VERATUS_SESSION_SALT": "private-test-salt"}):
        assert "cliente@example.com" not in _session_id("cliente@example.com")
        assert _session_id("cliente@example.com") == _session_id("cliente@example.com")
        assert _session_id("cliente@example.com") != _session_id("outro@example.com")


def test_agent_contracts_reject_free_form_or_extra_fields():
    with pytest.raises(ValidationError):
        SalesDraft(customer_reply="ok", intent="qualquer_coisa")
    with pytest.raises(ValidationError):
        ReviewDecision(
            approved_for_human_review=True,
            status="approved_for_human_review",
            hidden_instruction="não permitido",
        )
    with pytest.raises(ValidationError):
        ReviewDecision(
            approved_for_human_review=True,
            status="blocked",
            issues=["contradição"],
        )


def test_metrics_are_idempotent_and_calculate_cpa(tmp_path):
    store = SQLiteMetricsStore(tmp_path / "metrics.sqlite3")
    record = PerformanceRecord(
        event_id="meta-2026-09-18-a",
        occurred_on="2026-09-18",
        channel="meta",
        campaign="teste",
        creative="ocean-blue",
        impressions=1000,
        clicks=50,
        leads=4,
        conversations=3,
        sales=2,
        spend_brl="300.00",
        revenue_brl="800.00",
        product_cost_brl="130.00",
        shipping_cost_brl="50.00",
        fees_brl="80.00",
        evidence="exportação do gerenciador e pedidos internos",
    )
    assert store.add(record) is True
    assert store.add(record) is False
    summary = summarize_performance(store.list_records())
    assert summary["kpis"]["cpa_brl"] == 150.0
    assert summary["kpis"]["ctr_percent"] == 5.0
    assert summary["kpis"]["roas"] == 2.6667
    assert summary["kpis"]["average_ticket_brl"] == 400.0
    assert summary["kpis"]["contribution_margin_brl"] == 240.0


def test_product_master_requires_qa_and_audits_ceo_override(tmp_path):
    store = SQLiteProductStore(tmp_path / "products.sqlite3")
    product = store.create(
        ProductCreate(
            name="Relógio piloto",
            category="Relógios",
            category_code="REL",
            product_code="AUT",
        ),
        actor="product-agent",
        event_id="product-create-0001",
    )
    assert product["sku"] == "VRT-REL-AUT-0001"
    assert product["status"] == "NEW"

    product, issues = store.transition(
        product["sku"],
        target=ProductStatus.APPROVED,
        actor="fundador",
        event_id="product-override-0001",
        reason="Prioridade comercial",
        override=True,
    )
    assert product["status"] == "NEW"
    assert issues and any("material" in issue for issue in issues)

    updated = store.update(
        product["sku"],
        ProductUpdate(
            material="Aço inoxidável confirmado pelo responsável",
            supplier_ref="FORN-001",
            availability="Disponível sob consulta",
            images=["asset://watch-front"],
            cost_brl="65.00",
            price_brl="189.90",
            contribution_margin_brl="74.90",
            max_cpa_brl="40.00",
            evidence=[
                {"field": "material", "reference": "ficha-interna-001"},
                {"field": "cost_brl", "reference": "cotacao-001"},
                {"field": "availability", "reference": "confirmacao-001"},
            ],
        ),
        actor="product-agent",
        event_id="product-update-0001",
        reason="Dados confirmados pelo responsável",
    )
    assert updated["material"].startswith("Aço")
    enriched, issues = store.transition(
        product["sku"],
        target=ProductStatus.ENRICHING,
        actor="supervisor-produto",
        event_id="product-transition-0001",
        reason="Ficha completa para QA",
    )
    assert not issues and enriched["status"] == "ENRICHING"
    approved, issues = store.transition(
        product["sku"],
        target=ProductStatus.APPROVED,
        actor="quality-agent",
        event_id="product-transition-0002",
        reason="QA documental aprovado",
    )
    assert not issues and approved["status"] == "APPROVED"
    events = store.events(product["sku"])
    assert {event["event_type"] for event in events} >= {
        "product_created",
        "product_updated",
        "status_transition",
    }


def test_pricing_is_deterministic_and_separates_reinvestment():
    result = calculate_pricing(
        PricingInput(
            product_cost_brl="65.00",
            shipping_cost_brl="25.00",
            marketplace_fee_percent="10",
            tax_percent="5",
            desired_margin_percent="30",
        )
    )
    assert result["fixed_cost_brl"] == "90.00"
    assert result["recommended_price_brl"] == "163.64"
    assert result["max_cpa_brl"] == "49.09"


def test_mailerlite_import_requires_recorded_consent(tmp_path):
    input_path = tmp_path / "mailing.csv"
    input_path.write_text(
        "email,whatsapp,source,consent,consent_source\n"
        "autorizado@example.com,,form,true,registro-001\n"
        "sem-consentimento@example.com,,form,,\n",
        encoding="utf-8",
    )
    authorized, skipped = read_authorized_leads(input_path)
    assert [lead["email"] for lead in authorized] == ["autorizado@example.com"]
    assert skipped == 1


def test_repository_factory_prefers_postgres_when_configured(tmp_path):
    marker = object()
    with patch(
        "veratus_agents.postgres_storage.PostgresRunStore", return_value=marker
    ) as postgres:
        selected = make_run_repository(
            tmp_path / "runs.sqlite3", "postgresql://example"
        )
    assert selected is marker
    postgres.assert_called_once_with("postgresql://example")
