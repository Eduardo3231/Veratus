"""Escritório virtual: cada agente com o que fez, sem dado de cliente."""

from __future__ import annotations

import json
from unittest.mock import patch

from cryptography.fernet import Fernet

from integrations import webhook
from veratus_agents import office
from veratus_agents.conversations import InboundText
from veratus_agents.operations import AGENT_REGISTRY

KEY = Fernet.generate_key().decode("ascii")


def _env(tmp_path):
    return {
        "VERATUS_ADMIN_TOKEN": "admin-test-key",
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "VERATUS_SESSION_SALT": "sal",
        "VERATUS_TOKEN_ENCRYPTION_KEY": KEY,
    }


def test_every_role_has_a_desk_and_the_day_reads_without_customer_data(tmp_path):
    assert {m["id"] for m in office.STAFF} == {*AGENT_REGISTRY, "fundador"}
    webhook.rate_store.clear()
    webhook._runtime_engine = None
    webhook._operational_runtime_instance = None
    with patch.dict("os.environ", _env(tmp_path)):
        engine = webhook._conversation_engine()
        engine.handle(
            InboundText("whatsapp", "wa_1", "m1", "Ana, quanto custa? 11 98888-7777")
        )
        ordem = webhook.app.test_client().post(
            "/os/commands",
            headers={"X-Veratus-Admin-Token": "admin-test-key"},
            json={"message": "Analise o estado atual dos produtos e dos marketplaces"},
        )
        snap = office.snapshot(
            engine, webhook._runtime(), webhook._order_ledger(), env={}
        )
    assert ordem.status_code == 201
    agents = snap["agentes"]
    # As 19 pessoas do time trabalharam na revisão; o Harvey deixou o rascunho.
    assert all(agents[a]["status"] == "trabalhando" for a in AGENT_REGISTRY)
    assert "WhatsApp" in " ".join(i["texto"] for i in agents["sales-agent"]["ultimas"])
    # Toda tarefa vira frase, nunca o código da ação.
    texts = [item["texto"] for a in agents.values() for item in a["ultimas"]]
    assert texts and not any("_" in text.split(":")[0] for text in texts)
    dump = json.dumps(snap, ensure_ascii=False)
    assert "Ana" not in dump and "98888" not in dump
    assert snap["external_write"] is False


def test_office_routes_need_the_token_and_the_page_carries_no_data(tmp_path):
    webhook.rate_store.clear()
    client = webhook.app.test_client()
    with patch.dict("os.environ", _env(tmp_path)):
        denied = client.get("/os/escritorio")
        live = client.get(
            "/os/escritorio", headers={"X-Veratus-Admin-Token": "admin-test-key"}
        )
        page = client.get("/os/escritorio/painel")
    assert denied.status_code in (401, 403)
    assert live.status_code == 200 and len(live.json["funcionarios"]) == 20
    assert page.status_code == 200 and "VeratusOffice" in page.text
    assert "Augusto" not in page.text  # a página só lê com o token
