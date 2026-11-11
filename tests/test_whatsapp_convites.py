"""Convite de conversa: só para quem autorizou, uma vez, com template aprovado.

O fundador entrega o número e diz onde a pessoa autorizou o contato. Sem isso,
sem as chaves ou sem a aprovação da Meta, nada sai. Quando o cliente responde, a
mensagem cai na mesma conversa e segue para o Harvey.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from integrations import webhook
from veratus_agents.policy import hard_review_customer_reply
from veratus_agents.whatsapp import WhatsAppChannel, load_templates

NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
CELULAR = "(11) 99999-0000"
# A Meta devolve (e o cliente responde com) o wa_id sem o nono dígito.
WA_ID = "551199990000"


class FakeClient:
    def __init__(self, wa_id=None):
        self.sent = []
        self.wa_id = wa_id

    def send_message_contact(self, to, content):
        self.sent.append((to, content))
        return f"wamid.CONVITE{len(self.sent)}", self.wa_id or to


@pytest.fixture
def channel(tmp_path):
    return WhatsAppChannel(
        tmp_path / "ops.sqlite3",
        salt="test-salt",
        encryption_key=Fernet.generate_key().decode(),
    )


def _approved():
    registry = load_templates()
    registry["convite_conversa"] = {
        **registry["convite_conversa"],
        "status": "APPROVED",
    }
    return registry


def _invite(channel, client, conversation_id, **changes):
    options = {
        "client": client,
        "enabled": True,
        "marketing_enabled": True,
        "daily_limit": 20,
        "registry": _approved(),
        "now": NOW,
        **changes,
    }
    return channel.send_invitation(conversation_id, "convite_conversa", **options)


def test_invitation_needs_consent_keys_and_an_approved_template(channel):
    client = FakeClient()
    assert channel.start_outbound("123", source="formulário")["reasons"] == [
        "NUMBER_INVALID"
    ]
    assert channel.start_outbound(CELULAR, source="")["reasons"] == [
        "OPT_IN_SOURCE_REQUIRED"
    ]
    ready = channel.start_outbound(CELULAR, source="formulário do site, 08/11")
    assert ready["status"] == "READY" and ready["contact"] == "***0000"
    conversation_id = ready["conversation_id"]

    off = _invite(channel, client, conversation_id, marketing_enabled=False)
    draft = _invite(channel, client, conversation_id, registry=load_templates())
    assert off["reasons"] == ["WHATSAPP_MARKETING_DISABLED"]
    assert draft["reasons"] == ["TEMPLATE_NOT_APPROVED"]  # o do repositório é DRAFT
    assert client.sent == []

    sent = _invite(channel, client, conversation_id)
    again = _invite(channel, client, conversation_id)
    later = _invite(channel, client, conversation_id, now=NOW + timedelta(days=3))
    assert sent["status"] == "SENT" and again["status"] == "duplicate"
    assert later["reasons"] == ["INVITED_RECENTLY"]
    assert [to for to, _ in client.sent] == ["5511999990000"]
    assert client.sent[0][1]["template"]["name"] == "convite_conversa"

    other = channel.start_outbound("11 98888-1111", source="pediu no Instagram")
    capped = _invite(channel, client, other["conversation_id"], daily_limit=1)
    assert capped["reasons"] == ["DAILY_LIMIT_REACHED"] and len(client.sent) == 1


def test_the_reply_reaches_harvey_and_opt_out_holds_for_both_number_forms(channel):
    client = FakeClient(wa_id=WA_ID)
    ready = channel.start_outbound(CELULAR, source="lista da loja", product_id="colar")
    sent = _invite(channel, client, ready["conversation_id"])
    assert sent["conversation_id"] == channel.conversation_id(WA_ID)

    def reply(message_id, body, seconds):
        at = NOW + timedelta(seconds=seconds)
        message = {
            "from": WA_ID,
            "id": message_id,
            "timestamp": str(int(at.timestamp())),
            "type": "text",
            "text": {"body": body},
        }
        value = {"metadata": {"phone_number_id": "PNID"}, "messages": [message]}
        entry = {"id": "WABA", "changes": [{"field": "messages", "value": value}]}
        payload = {"object": "whatsapp_business_account", "entry": [entry]}
        return channel.receive(payload, phone_number_id="PNID")

    assert reply("wamid.R1", "Quero ver", 60)["messages"] == 1
    # Uma conversa só: o convite e a resposta juntos, com a peça do convite.
    assert [c["conversation_id"] for c in channel.list_conversations()] == [
        sent["conversation_id"]
    ]
    pending = channel.pending_inbound()
    assert [(m["body"], m["product_id"]) for m in pending] == [("Quero ver", "colar")]
    kinds = [m["kind"] for m in channel.get(sent["conversation_id"])["messages"]]
    assert kinds == ["invitation", "text"]

    reply("wamid.R2", "PARAR", 120)
    for form in (CELULAR, WA_ID):
        blocked = channel.start_outbound(form, source="lista da loja")
        assert blocked["reasons"] == ["OPTED_OUT"]
    assert len(client.sent) == 1


def test_route_needs_the_token_and_sends_nothing_by_default(tmp_path):
    body = load_templates()["convite_conversa"]["body"]
    assert not hard_review_customer_reply(body, [], product_collection="feminine")
    assert "assistente virtual" in body and "PARAR" in body

    env = {
        "VERATUS_ADMIN_TOKEN": "admin-test-key",
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "VERATUS_SESSION_SALT": "sal",
        "VERATUS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "WHATSAPP_MARKETING_ENABLED": "",
        "WHATSAPP_SEND_ENABLED": "",
    }
    data = {"contatos": [{"numero": CELULAR, "origem": "formulário do site"}]}
    webhook.rate_store.clear()
    client = webhook.app.test_client()
    with patch.dict("os.environ", env):
        denied = client.post("/os/whatsapp/convites", json=data)
        answer = client.post(
            "/os/whatsapp/convites",
            json=data,
            headers={"X-Veratus-Admin-Token": "admin-test-key"},
        )
    assert denied.status_code in (401, 403)
    assert answer.status_code == 200 and answer.json["enviados"] == 0
    result = answer.json["resultados"][0]
    assert result["status"] == "BLOCKED" and not result["external_message_sent"]
    assert "WHATSAPP_MARKETING_DISABLED" in result["reasons"]
    assert "99999" not in answer.get_data(as_text=True)
