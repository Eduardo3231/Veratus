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
    assert len(channels.json["channels"]) == 5
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
    assert len(command.json["execution"]["agents_invoked"]) == 14
    assert command.json["execution"]["external_writes"] == "BLOCKED"


def test_pasted_marketplace_question_goes_to_the_engine_and_waits_in_the_queue(
    tmp_path,
):
    import json

    from cryptography.fernet import Fernet

    rate_store.clear()
    digits = str(12345678909)  # CPF fictício, montado em tempo de execução
    cpf = f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    env = {
        "VERATUS_ADMIN_TOKEN": "marketplace-admin",
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "VERATUS_SESSION_SALT": "sal",
        "VERATUS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "CONVERSATION_MODEL": "template",
        "OPENAI_API_KEY": "",
    }
    headers = {"X-Veratus-Admin-Token": "marketplace-admin"}
    body = {
        "conversation_id": "comprador.ana",
        "message_id": "pergunta-1",
        "text": f"Qual o prazo de entrega? Meu CPF é {cpf}",
        "product_hint": "royal-blue",
    }
    with patch.dict("os.environ", env, clear=False):
        client = app.test_client()
        pasted = client.post(
            "/os/conversations/shopee/inbound", headers=headers, json=body
        )
        again = client.post(
            "/os/conversations/shopee/inbound", headers=headers, json=body
        )
        direct = client.post(
            "/os/conversations/whatsapp/inbound", headers=headers, json=body
        )
        queue = client.get("/os/conversations/queue", headers=headers).json["drafts"]

    assert pasted.status_code == 200, pasted.json
    assert pasted.json["conversation_id"].startswith("cv_")
    assert again.json["action"] == "DUPLICATE"
    assert direct.status_code == 404
    # Nada sai: a resposta fica na fila para colar no painel da Shopee.
    assert pasted.json.get("sent") is not True
    assert [draft["channel"] for draft in queue] == ["shopee"]
    stored = json.dumps([pasted.json, queue], ensure_ascii=False)
    assert cpf not in stored and "comprador.ana" not in stored
