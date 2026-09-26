from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from integrations.webhook import app, rate_store
from veratus_agents.orders import (
    OrderConflictError,
    OrderLedger,
    OrderRecord,
    OrderStatus,
    OrderTransitionError,
    StatusChange,
    local_day_bounds,
)

ADMIN = {"X-Veratus-Admin-Token": "admin-test-key"}


def sale(**changes):
    values = {
        "order_id": "wa-20260925-01",
        "sku": "ocean-blue",
        "channel": "whatsapp",
        "visit_ref": "VT-RJ5MO2",
        "utm_source": "instagram",
        "sale_price": "289.90",
        "payment_method": "pix",
        "status": "PAID",
        "evidence_ref": "comprovante-pix-pasta-vendas-01",
        "recorded_by": "fundador",
    }
    values.update(changes)
    return values


def test_record_is_idempotent_and_rejects_conflicting_reuse(tmp_path):
    ledger = OrderLedger(tmp_path / "ops.sqlite3")

    first = ledger.record(OrderRecord.model_validate(sale()))
    retry = ledger.record(OrderRecord.model_validate(sale(sale_price=289.9)))

    assert first[0] == "created"
    assert retry[0] == "duplicate"
    assert retry[1]["created_at"] == first[1]["created_at"]
    assert first[1]["net_merchandise_brl"] == "289.90"
    with pytest.raises(OrderConflictError):
        ledger.record(OrderRecord.model_validate(sale(sale_price="199.90")))
    reopened = OrderLedger(tmp_path / "ops.sqlite3")
    stored = reopened.get("wa-20260925-01")
    assert [event["event_type"] for event in stored["events"]] == ["ORDER_RECORDED"]


@pytest.mark.parametrize(
    "changes",
    [
        {"evidence_ref": "cliente maria@example.com"},
        {"evidence_ref": "conversa com (11) 95832-3612"},
        {"recorded_by": "5511958323612"},
        {"utm_content": "+55 11 99999-0000"},
        {"customer_name": "Maria"},
        {"created_at": "2026-09-24T10:00:00"},
        {"created_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
        {"discount": "300.00"},
        {"visit_ref": "https://example.com"},
    ],
)
def test_record_rejects_personal_data_and_ambiguous_values(changes):
    with pytest.raises(ValidationError):
        OrderRecord.model_validate(sale(**changes))


def test_marketplace_order_numbers_are_not_mistaken_for_phones():
    order = OrderRecord.model_validate(
        sale(order_id="2000009876543210", evidence_ref="ml-pedido-2000009876543210")
    )

    assert order.evidence_ref == "ml-pedido-2000009876543210"


def test_status_changes_follow_graph_and_replay_safely(tmp_path):
    ledger = OrderLedger(tmp_path / "ops.sqlite3")
    ledger.record(OrderRecord.model_validate(sale()))
    shipped = StatusChange(
        event_id="wa-20260925-01:shipped", status="SHIPPED", recorded_by="fundador"
    )

    assert ledger.change_status("wa-20260925-01", shipped)[0] == "changed"
    assert ledger.change_status("wa-20260925-01", shipped)[0] == "duplicate"
    with pytest.raises(OrderTransitionError):
        ledger.change_status(
            "wa-20260925-01",
            StatusChange(
                event_id="wa-20260925-01:pending",
                status="PENDING",
                recorded_by="fundador",
            ),
        )
    with pytest.raises(OrderConflictError):
        ledger.change_status(
            "wa-20260925-01",
            StatusChange(
                event_id="wa-20260925-01:shipped",
                status="DELIVERED",
                recorded_by="fundador",
            ),
        )
    with pytest.raises(KeyError):
        ledger.change_status(
            "missing-order",
            StatusChange(
                event_id="missing-order:paid", status="PAID", recorded_by="fundador"
            ),
        )

    history = ledger.get("wa-20260925-01")["events"]
    assert [(item["from_status"], item["to_status"]) for item in history] == [
        (None, "PAID"),
        ("PAID", "SHIPPED"),
    ]


def test_summary_counts_only_confirmed_revenue_beyond_list_limit(tmp_path):
    ledger = OrderLedger(tmp_path / "ops.sqlite3")
    ledger.record(OrderRecord.model_validate(sale(discount="20.00")))
    ledger.record(
        OrderRecord.model_validate(
            sale(order_id="wa-20260925-02", status="PENDING", visit_ref=None)
        )
    )
    ledger.record(OrderRecord.model_validate(sale(order_id="wa-20260925-03")))

    summary = ledger.summary()

    assert len(ledger.list_orders(limit=1)) == 1
    assert summary["orders"] == 3
    assert summary["by_status"] == {"PAID": 2, "PENDING": 1}
    assert summary["confirmed_net_merchandise_brl"] == "559.80"
    assert ledger.list_orders(status=OrderStatus.PENDING)[0]["visit_ref"] is None


def test_day_filters_follow_sao_paulo_calendar(tmp_path):
    ledger = OrderLedger(tmp_path / "ops.sqlite3")
    ledger.record(
        OrderRecord.model_validate(sale(created_at="2026-09-24T22:30:00-03:00"))
    )

    same_day = local_day_bounds(date(2026, 9, 24), date(2026, 9, 24))
    next_day = local_day_bounds(date(2026, 9, 25), None)

    assert same_day == ("2026-09-24T03:00:00+00:00", "2026-09-25T03:00:00+00:00")
    assert len(ledger.list_orders(date_from=same_day[0], date_to=same_day[1])) == 1
    assert ledger.list_orders(date_from=next_day[0]) == []


def test_orders_http_requires_admin_and_never_echoes_rejected_values(tmp_path):
    rate_store.clear()
    with patch.dict(
        "os.environ",
        {
            "VERATUS_ADMIN_TOKEN": "admin-test-key",
            "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
            "DATABASE_URL": "",
        },
    ):
        client = app.test_client()
        assert client.post("/os/orders", json=sale()).status_code == 401

        created = client.post("/os/orders", headers=ADMIN, json=sale())
        replay = client.post("/os/orders", headers=ADMIN, json=sale())
        conflict = client.post(
            "/os/orders", headers=ADMIN, json=sale(payment_method="credit_card")
        )
        unknown = client.post(
            "/os/orders",
            headers=ADMIN,
            json=sale(order_id="wa-20260925-09", sku="nao-existe"),
        )
        leaked = client.post(
            "/os/orders",
            headers=ADMIN,
            json=sale(order_id="wa-20260925-10", evidence_ref="maria@example.com"),
        )
        status = client.post(
            "/os/orders/wa-20260925-01/status",
            headers=ADMIN,
            json={
                "event_id": "wa-20260925-01:delivered",
                "status": "DELIVERED",
                "recorded_by": "fundador",
            },
        )
        listing = client.get("/os/orders?status=PAID&from=2026-01-01", headers=ADMIN)
        detail = client.get("/os/orders/wa-20260925-01", headers=ADMIN)

    assert created.status_code == 201 and created.json["status"] == "created"
    assert replay.status_code == 200 and replay.json["status"] == "duplicate"
    assert conflict.status_code == 409
    assert unknown.status_code == 400 and unknown.json["message"] == "unknown_sku"
    assert leaked.status_code == 400
    assert "maria@example.com" not in leaked.get_data(as_text=True)
    assert leaked.json["errors"][0]["field"] == "evidence_ref"
    assert status.status_code == 409
    assert "PAID->DELIVERED" in status.json["message"]
    assert listing.status_code == 200
    assert listing.json["summary"]["confirmed_orders"] == 1
    assert detail.json["order"]["events"][0]["event_type"] == "ORDER_RECORDED"
