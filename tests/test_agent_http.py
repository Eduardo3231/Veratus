from unittest.mock import patch

from integrations.webhook import app, rate_store
from veratus_agents.storage import RunStore


def test_agent_api_auth_validation_and_decision(tmp_path):
    rate_store.clear()
    db_path = tmp_path / "agent-operations.sqlite3"
    with patch.dict(
        "os.environ",
        {
            "VERATUS_AGENT_SHARED_SECRET": "inbound-test-key",
            "VERATUS_ADMIN_TOKEN": "admin-test-key",
            "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        },
    ):
        client = app.test_client()
        health = client.get("/agent/health")
        assert health.status_code == 200
        assert health.json["external_sending_enabled"] is False
        assert client.post("/agent/sales/draft", json={}).status_code == 401
        assert client.get("/agent/runs/missing").status_code == 401
        inbound = {"X-Veratus-Agent-Key": "inbound-test-key"}
        admin = {"X-Veratus-Admin-Token": "admin-test-key"}
        assert client.get("/agent/runs/missing", headers=admin).status_code == 404
        assert (
            client.post(
                "/agent/runs/missing/decision",
                headers=admin,
                json={
                    "decision": "approve",
                    "actor": "operador",
                    "approved_reply": "Olá",
                },
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/agent/sales/draft",
                headers=inbound,
                data="not json",
                content_type="text/plain",
            ).status_code
            == 415
        )
        assert (
            client.post(
                "/agent/sales/draft",
                headers=inbound,
                data="{",
                content_type="application/json",
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/agent/sales/draft", headers=inbound, json={"message": "x"}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/agent/sales/draft",
                headers=inbound,
                json={"message": "x" * 5000, "customer_ref": "x"},
            ).status_code
            == 413
        )

        def fake_run_sales_workflow(**kwargs):
            assert kwargs["message"] == "Gostei do Ocean Blue."
            store = RunStore(db_path)
            run_id = store.create_run(
                session_id="hashed-session",
                source="api",
                payload={"message": kwargs["message"]},
            )
            store.finish_run(
                run_id,
                status="pending_review",
                draft={"customer_reply": "Vamos conversar."},
                review={"approved_for_human_review": True},
            )
            return {
                "run_id": run_id,
                "status": "pending_review",
                "draft": {"customer_reply": "Vamos conversar."},
                "review": {"approved_for_human_review": True},
                "external_message_sent": False,
            }

        with patch(
            "veratus_agents.run_sales_workflow", side_effect=fake_run_sales_workflow
        ):
            created = client.post(
                "/agent/sales/draft",
                headers=inbound,
                json={"customer_ref": "demo-001", "message": "Gostei do Ocean Blue."},
            )
        assert created.status_code == 200
        run_id = created.json["run_id"]
        assert client.get(f"/agent/runs/{run_id}", headers=inbound).status_code == 401
        assert (
            client.get(f"/agent/runs/{run_id}", headers=admin).json["status"]
            == "pending_review"
        )
        queue = client.get("/agent/runs?status=pending_review", headers=admin)
        assert queue.status_code == 200
        assert queue.json["runs"][0]["run_id"] == run_id
        assert (
            client.post(
                f"/agent/runs/{run_id}/decision",
                headers=admin,
                json={"decision": ["approve"], "actor": "operador"},
            ).status_code
            == 400
        )
        assert (
            client.post(
                f"/agent/runs/{run_id}/decision",
                headers=admin,
                json={
                    "decision": "approve",
                    "actor": "operador",
                    "approved_reply": "Vamos conversar.",
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/agent/runs/{run_id}/decision",
                headers=admin,
                json={"decision": "reject", "actor": "operador"},
            ).status_code
            == 409
        )
        assert (
            client.get(f"/agent/runs/{run_id}", headers=admin).json["status"]
            == "approved"
        )


def test_metrics_api_requires_admin_and_is_idempotent(tmp_path):
    rate_store.clear()
    with patch.dict(
        "os.environ",
        {
            "VERATUS_ADMIN_TOKEN": "admin-test-key",
            "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        },
    ):
        client = app.test_client()
        payload = {
            "event_id": "manual-2026-09-18-sales",
            "occurred_on": "2026-09-18",
            "channel": "meta",
            "campaign": "teste",
            "creative": "ocean-blue",
            "impressions": 1000,
            "clicks": 50,
            "leads": 4,
            "conversations": 3,
            "sales": 2,
            "spend_brl": "300.00",
            "revenue_brl": "800.00",
            "evidence": "registro manual validado",
        }
        assert client.post("/agent/metrics/records", json=payload).status_code == 401
        admin = {"X-Veratus-Admin-Token": "admin-test-key"}
        assert (
            client.post(
                "/agent/metrics/records", headers=admin, json=payload
            ).status_code
            == 201
        )
        assert (
            client.post("/agent/metrics/records", headers=admin, json=payload).json[
                "status"
            ]
            == "duplicate"
        )
        summary = client.get("/agent/metrics/summary", headers=admin)
        assert summary.status_code == 200
        assert summary.json["kpis"]["cpa_brl"] == 150.0


def test_product_master_api_blocks_incomplete_approval(tmp_path):
    rate_store.clear()
    with patch.dict(
        "os.environ",
        {
            "VERATUS_ADMIN_TOKEN": "admin-test-key",
            "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        },
    ):
        client = app.test_client()
        admin = {"X-Veratus-Admin-Token": "admin-test-key"}
        request_body = {
            "actor": "product-agent",
            "event_id": "api-product-create-0001",
            "product": {
                "name": "Relógio piloto",
                "category": "Relógios",
                "category_code": "REL",
                "product_code": "AUT",
            },
        }
        assert client.post("/os/products", json=request_body).status_code == 401
        created = client.post("/os/products", headers=admin, json=request_body)
        assert created.status_code == 201
        sku = created.json["product"]["sku"]
        blocked = client.post(
            f"/os/products/{sku}/override",
            headers=admin,
            json={
                "actor": "fundador",
                "event_id": "api-product-override-0001",
                "reason": "Publicação urgente",
                "target": "APPROVED",
            },
        )
        assert blocked.status_code == 409
        assert blocked.json["issues"]
        event_types = {
            event["event_type"]
            for event in client.get(f"/os/products/{sku}/events", headers=admin).json[
                "events"
            ]
        }
        assert event_types == {"product_created", "ceo_override_blocked"}

        price = client.post(
            "/os/pricing/calculate",
            headers=admin,
            json={
                "product_cost_brl": "65.00",
                "shipping_cost_brl": "25.00",
                "marketplace_fee_percent": "10",
                "tax_percent": "5",
                "desired_margin_percent": "30",
            },
        )
        assert price.status_code == 200
        assert price.json["pricing"]["recommended_price_brl"] == "163.64"
