"""Mapa do fluxo de vendas: só papéis do registro e nada sensível na saída."""

from __future__ import annotations

import json
from unittest.mock import patch

from cryptography.fernet import Fernet

from veratus_agents.conversations import CHANNELS
from veratus_agents.operations import AGENT_REGISTRY
from veratus_agents.sales_flow import SALES_FLOW, STAGES, flow_report, flow_roles


def test_every_role_in_the_flow_exists_in_the_registry():
    assert flow_roles() <= set(AGENT_REGISTRY)


def test_every_conversation_channel_is_a_real_channel():
    channels = {item["conversation_channel"] for item in SALES_FLOW}
    assert channels - {None} == set(CHANNELS)
    for item in SALES_FLOW:
        assert set(item["stages"]) <= set(STAGES)


class _Engine:
    def metrics(self):
        return {"whatsapp": {"conversations": 3}}

    def mode(self, channel):
        return "SHADOW"


def test_report_shows_flags_as_booleans_and_no_secret():
    env = {
        "WHATSAPP_SEND_ENABLED": "true",
        "WHATSAPP_ACCESS_TOKEN": "segredo-que-nao-pode-sair",
        "CONVERSATION_AUTOPROCESS_ENABLED": "false",
    }
    report = flow_report(_Engine(), None, env)
    whatsapp = next(c for c in report["channels"] if c["channel"] == "whatsapp")
    shopee = next(c for c in report["channels"] if c["channel"] == "shopee")
    assert whatsapp["send_enabled"] is True
    assert whatsapp["mode"] == "SHADOW"
    assert whatsapp["metrics"] == {"conversations": 3}
    assert shopee["send_enabled"] is None  # cola manual: não há chave de envio
    assert report["autoprocess"] is False
    assert "segredo-que-nao-pode-sair" not in json.dumps(report)


def test_flow_route_requires_the_admin_token(tmp_path):
    from integrations.webhook import app, rate_store

    rate_store.clear()

    env = {
        "VERATUS_ADMIN_TOKEN": "flow-admin",
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "VERATUS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "VERATUS_SESSION_SALT": "sal",
        "CONVERSATION_MODEL": "template",
        "OPENAI_API_KEY": "",
    }
    with patch.dict("os.environ", env):
        client = app.test_client()
        denied = client.get("/os/flow")
        allowed = client.get(
            "/os/flow", headers={"X-Veratus-Admin-Token": "flow-admin"}
        )
    assert denied.status_code == 401
    assert allowed.status_code == 200, allowed.json
    assert {c["channel"] for c in allowed.json["channels"]} >= {
        "whatsapp",
        "mercado_livre",
    }
    assert allowed.json["orders"]["orders"] == 0
