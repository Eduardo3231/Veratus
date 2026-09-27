from __future__ import annotations

from decimal import Decimal

import pytest

from veratus_agents.operations import CommandEngine
from veratus_agents.paid_media import (
    AutonomyMode,
    CreativeReviewStatus,
    MetaAdsAdapter,
    PaidAcquisitionWorker,
    PaidMediaConfig,
    PaidMediaStore,
    calculate_economics,
    review_creative,
)


def product(*, complete_economics: bool = False):
    fields = {
        "sale_price": {"value": "289.90"},
        "cost": {"value": "65.00"},
    }
    if complete_economics:
        fields.update(
            {
                "shipping_cost_paid_by_veratus": {"value": "20.00"},
                "payment_fee": {"value": "10.00"},
                "tax": {"value": "15.00"},
            }
        )
    return {
        "id": "ocean-blue",
        "sku": "ocean-blue",
        "name": "Ocean Blue",
        "description": "Relógio azul Veratus.",
        "active": True,
        "missing_fields": [],
        "images": ["assets/catalog/ocean-blue.webp"],
        "fields": fields,
    }


def shadow_config(**changes):
    values = {
        "enabled": True,
        "autonomy_mode": AutonomyMode.SHADOW,
        "live_writes": False,
        "daily_budget_brl": Decimal(20),
        "test_max_spend_brl": Decimal(140),
        "max_budget_delta_pct": Decimal(0),
        "reconciliation_required": True,
        "circuit_breaker_enabled": True,
        "safety_factor": Decimal("0.70"),
    }
    values.update(changes)
    return PaidMediaConfig(**values)


def test_economics_blocks_when_operational_costs_are_missing():
    result = calculate_economics(product())

    assert result["status"] == "INCOMPLETE"
    assert result["missing_fields"] == [
        "shipping_cost_paid_by_veratus",
        "payment_fee",
        "tax",
    ]
    assert result["inputs"]["price"] == "289.90"
    assert result["inputs"]["unit_cost"] == "65.00"
    assert result["inputs"]["customer_shipping"] == "0.00"
    assert result["inputs"]["customer_fees"] == "0.00"
    assert result["break_even_cpa"] is None
    assert result["break_even_cpa_final"] is False
    assert result["target_cpa"] is None


def test_complete_economics_calculates_cpa_and_roas():
    result = calculate_economics(product(complete_economics=True))

    assert result["status"] == "COMPLETE"
    assert result["contribution_before_ads"] == "179.90"
    assert result["target_cpa"] == "125.93"
    assert result["break_even_cpa_final"] is True


def test_creative_review_is_structural_not_performance_prediction():
    result = review_creative(
        product(),
        {
            "image": "ocean.webp",
            "brand": "Veratus",
            "message": "Você não observa o tempo. Você faz parte dele.",
            "format": "4:5",
            "landing_match": True,
            "compliance": True,
        },
    )

    assert result["status"] == CreativeReviewStatus.NEEDS_REVISION
    assert {"hook", "offer", "proof", "cta"}.issubset(result["recommendations"])


def test_shadow_plan_persists_and_never_writes(tmp_path, monkeypatch):
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    store_path = tmp_path / "paid.sqlite3"
    command_path = tmp_path / "runtime.sqlite3"
    worker = PaidAcquisitionWorker(
        PaidMediaStore(str(store_path)),
        CommandEngine(persistence_path=str(command_path)),
        adapter=MetaAdsAdapter(),
        config=shadow_config(),
    )

    plan = worker.plan([product()], idempotency_key="first-shadow")
    blocked = worker.execute(
        plan["experiment_id"],
        plan["approval_request_id"],
        idempotency_key="execute-first-shadow",
    )

    assert plan["daily_budget_brl"] == "20.00"
    assert plan["max_spend_brl"] == "140.00"
    assert plan["external_write"] is False
    assert "ECONOMICS_INCOMPLETE" in plan["blockers"]
    assert "PURCHASE_TRACKING_UNVERIFIED" in plan["blockers"]
    assert plan["approval_request_id"] is None
    assert blocked["status"] == "BLOCKED"
    assert "SHADOW_MODE_NO_WRITE" in blocked["reasons"]
    assert blocked["external_write"] is False

    restarted = PaidAcquisitionWorker(
        PaidMediaStore(str(store_path)),
        CommandEngine(persistence_path=str(command_path)),
        adapter=MetaAdsAdapter(),
        config=shadow_config(),
    )
    assert (
        restarted.store.snapshot.experiments[0]["experiment_id"]
        == plan["experiment_id"]
    )
    assert restarted.latest_shift_report()["spend_today_brl"] == "0.00"


class HealthyMetaAdapter(MetaAdsAdapter):
    """Test double: readback connected and Purchase verified."""

    def health(self):
        return {"channel": "meta", "status": "CONFIGURED_READ_ONLY"}

    def get_tracking_status(self):
        return {"channel": "meta", "status": "HEALTHY"}


READY_CREATIVE = {
    "image": "ocean.webp",
    "hook": "O detalhe ganha vida.",
    "offer": "Consulta pelo WhatsApp",
    "brand": "Veratus",
    "message": "Você faz parte do tempo.",
    "proof": "Foto do item exato",
    "cta": "Consultar modelo",
    "format": "4:5",
    "landing_match": True,
    "compliance": True,
}


def unblocked_worker(tmp_path, **config):
    return PaidAcquisitionWorker(
        PaidMediaStore(str(tmp_path / "paid.sqlite3")),
        CommandEngine(persistence_path=str(tmp_path / "runtime.sqlite3")),
        adapter=HealthyMetaAdapter(),
        config=shadow_config(**config),
    )


def test_approved_plan_still_cannot_execute_with_live_writes_disabled(tmp_path):
    worker = unblocked_worker(tmp_path, autonomy_mode=AutonomyMode.SUPERVISED)
    plan = worker.plan([product(complete_economics=True)], READY_CREATIVE)
    approved = worker.approve(
        plan["experiment_id"], plan["approval_request_id"], actor="fundador"
    )

    result = worker.execute(
        plan["experiment_id"],
        plan["approval_request_id"],
        idempotency_key="approved-but-disabled",
    )

    assert plan["blockers"] == []
    assert approved["approval"] == "APPROVED"
    approval = worker.engine.approvals.requests[plan["approval_request_id"]]
    assert approval.payload["resolved_by"] == "fundador"
    assert result["status"] == "BLOCKED"
    assert result["reasons"] == ["PAID_MEDIA_LIVE_WRITES_DISABLED"]


def test_blocked_plan_cannot_be_approved(tmp_path):
    worker = PaidAcquisitionWorker(
        PaidMediaStore(str(tmp_path / "paid.sqlite3")),
        CommandEngine(),
        config=shadow_config(),
    )
    plan = worker.plan([product()])

    with pytest.raises(ValueError, match="plan_has_open_blockers"):
        worker.approve(plan["experiment_id"], "approval_x", actor="fundador")
    assert worker.engine.approvals.requests == {}


def test_plan_replays_until_inputs_change(tmp_path):
    worker = PaidAcquisitionWorker(
        PaidMediaStore(str(tmp_path / "paid.sqlite3")),
        CommandEngine(),
        config=shadow_config(),
    )

    first = worker.plan([product()])
    replay = worker.plan([product()])
    changed = worker.plan([product(complete_economics=True)])

    assert replay["experiment_id"] == first["experiment_id"]
    assert changed["experiment_id"] != first["experiment_id"]
    assert "ECONOMICS_INCOMPLETE" not in changed["blockers"]
    assert len(worker.store.snapshot.experiments) == 2


def test_execute_rejects_approval_from_another_experiment(tmp_path):
    worker = unblocked_worker(tmp_path, autonomy_mode=AutonomyMode.SUPERVISED)
    first = worker.plan([product(complete_economics=True)], READY_CREATIVE)
    second = worker.plan(
        [product(complete_economics=True)], {**READY_CREATIVE, "format": "9:16"}
    )
    worker.approve(first["experiment_id"], first["approval_request_id"], actor="f")

    result = worker.execute(
        second["experiment_id"],
        first["approval_request_id"],
        idempotency_key="cross-experiment",
    )

    assert "APPROVAL_REQUIRED" in result["reasons"]
    with pytest.raises(ValueError, match="approval_not_found"):
        worker.approve(second["experiment_id"], first["approval_request_id"], actor="f")


def test_sync_records_tracking_and_auth_blockers_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "do-not-leak")
    monkeypatch.delenv("META_AD_ACCOUNT_ID", raising=False)
    worker = PaidAcquisitionWorker(
        PaidMediaStore(str(tmp_path / "paid.sqlite3")),
        CommandEngine(),
        config=shadow_config(),
    )

    result = worker.sync()
    serialized = str(result) + str(worker.store.snapshot)

    assert result["external_write"] is False
    assert "META_READBACK_NOT_CONNECTED" in result["blockers"]
    assert "PURCHASE_TRACKING_UNVERIFIED" in result["blockers"]
    assert "do-not-leak" not in serialized
