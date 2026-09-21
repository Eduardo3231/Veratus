from unittest.mock import patch

from integrations.webhook import app, rate_store


def test_phase_two_reports_and_natural_command(tmp_path):
    rate_store.clear()
    with patch.dict(
        "os.environ",
        {
            "VERATUS_ADMIN_TOKEN": "marketplace-admin",
            "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
            "MARKETPLACE_MODE": "LOCAL",
        },
        clear=False,
    ):
        client = app.test_client()
        headers = {"X-Veratus-Admin-Token": "marketplace-admin"}

        channels = client.get("/os/channels", headers=headers)
        quality = client.get("/os/data-quality", headers=headers)
        readiness = client.get("/os/readiness", headers=headers)
        command = client.post(
            "/os/commands",
            headers=headers,
            json={"message": "Gerente, prepare todos os produtos ativos."},
        )

    assert channels.status_code == 200
    assert len(channels.json["channels"]) == 4
    assert quality.json["products_total"] == 18
    assert quality.json["segments"]["feminine_total"] == 9
    assert readiness.json["marketplace_mode"] == "LOCAL"
    assert readiness.json["channel_modes"]["mercado-livre"] in {
        "NOT_CONFIGURED",
        "NOT_READY",
        "BLOCKED_BY_CREDENTIALS",
    }
    assert command.status_code == 201
    assert command.json["execution"]["status"] == "COMPLETED"
    assert len(command.json["execution"]["agents_invoked"]) == 13
    assert command.json["execution"]["external_writes"] == "BLOCKED"
