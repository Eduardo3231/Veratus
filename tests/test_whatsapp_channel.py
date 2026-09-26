from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from integrations.webhook import app, rate_store
from veratus_agents.meta_webhooks import verify_signature, verify_subscription
from veratus_agents.whatsapp import (
    WhatsAppApiError,
    WhatsAppChannel,
    WhatsAppCloudClient,
    extract_attribution,
)

NUMBER = "5511999990000"
LANDING_MESSAGE = (
    "Olá! Vim pelo site da Veratus e tenho interesse em Ocean Blue.\n"
    "Gostaria de confirmar disponibilidade, valores e condições atuais.\n"
    "Referência da visita: VT-1A2B3C | produto=ocean-blue\n"
    "Origem da visita: utm_source=instagram | utm_medium=comment_dm"
)
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def text_message(message_id, body, *, wa_id=NUMBER, at=NOW, referral=None):
    item = {
        "from": wa_id,
        "id": message_id,
        "timestamp": str(int(at.timestamp())),
        "type": "text",
        "text": {"body": body},
    }
    if referral:
        item["referral"] = referral
    return item


def notification(*messages, statuses=()):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "PNID"},
                            "messages": list(messages),
                            "statuses": list(statuses),
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture
def channel(tmp_path):
    return WhatsAppChannel(
        tmp_path / "ops.sqlite3",
        salt="test-salt",
        encryption_key=Fernet.generate_key().decode(),
    )


class FakeClient:
    def __init__(self):
        self.sent = []

    def send_text(self, to, body):
        self.sent.append((to, body))
        return f"wamid.OUT{len(self.sent)}"


def approved(run_id="run_1", reply="Olá! O Ocean Blue está disponível para consulta."):
    return lambda requested: (
        {"status": "approved", "approved_reply": reply} if requested == run_id else None
    )


def test_signature_and_subscription_checks():
    body = b'{"object":"whatsapp_business_account"}'
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    assert verify_signature(body, good, "secret")
    assert not verify_signature(body, good, "other-secret")
    assert not verify_signature(body, None, "secret")
    assert not verify_signature(body, good, "")
    assert verify_subscription("subscribe", "tok", "123", "tok") == "123"
    assert verify_subscription("subscribe", "bad", "123", "tok") is None
    assert verify_subscription("subscribe", "tok", "123", "") is None


def test_attribution_comes_from_the_landing_message():
    assert extract_attribution(LANDING_MESSAGE) == {
        "visit_ref": "VT-1A2B3C",
        "product_id": "ocean-blue",
        "utm": {"utm_source": "instagram", "utm_medium": "comment_dm"},
    }
    assert extract_attribution("oi") == {
        "visit_ref": None,
        "product_id": None,
        "utm": {},
    }


def test_receive_is_idempotent_and_never_stores_the_plain_number(channel, tmp_path):
    payload = notification(
        text_message(
            "wamid.IN1",
            LANDING_MESSAGE,
            referral={"ctwa_clid": "ARAk-test", "source_url": "https://fb.me/x"},
        )
    )

    first = channel.receive(payload)
    replay = channel.receive(payload)
    [conversation] = channel.list_conversations()

    assert first["messages"] == 1 and replay["duplicates"] == 1
    assert conversation["conversation_id"].startswith("wa_")
    assert conversation["contact"] == "***0000"
    assert conversation["visit_ref"] == "VT-1A2B3C"
    assert conversation["product_id"] == "ocean-blue"
    assert conversation["utm"]["utm_source"] == "instagram"
    assert conversation["click_to_whatsapp_ad"] is True
    assert NUMBER.encode() not in (tmp_path / "ops.sqlite3").read_bytes()
    assert NUMBER not in json.dumps(channel.get(conversation["conversation_id"]))


def test_handoff_request_moves_to_human_and_stops_drafting(channel):
    channel.receive(notification(text_message("wamid.IN1", "Oi, tudo bem?")))
    counts = channel.receive(
        notification(
            text_message(
                "wamid.IN2",
                "Prefiro falar com um atendente",
                at=NOW + timedelta(minutes=1),
            )
        )
    )
    calls = []

    result = channel.draft_pending(lambda **kwargs: calls.append(kwargs))
    [conversation] = channel.list_conversations()

    assert counts["handoffs"] == 1
    assert conversation["state"] == "HUMAN"
    assert calls == [] and result["drafted"] == []


def test_draft_pending_links_runs_and_isolates_failures(channel):
    channel.receive(notification(text_message("wamid.IN1", LANDING_MESSAGE)))
    channel.receive(
        notification(text_message("wamid.IN2", "Qual o prazo?", wa_id="5511988887777"))
    )
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        if kwargs["event_id"] == "wamid.IN2":
            raise TimeoutError("model timeout")
        return {"run_id": "run_1", "status": "pending_review"}

    result = channel.draft_pending(runner)

    assert result["drafted"] == [{"message_id": "wamid.IN1", "run_id": "run_1"}]
    assert result["failed"] == [{"message_id": "wamid.IN2", "error": "TimeoutError"}]
    assert calls[0]["source"] == "whatsapp"
    assert calls[0]["product_hint"] == "ocean-blue"
    assert calls[0]["customer_ref"].startswith("wa_")
    assert all(NUMBER not in str(call) for call in calls)
    assert channel.draft_pending(runner) == {"drafted": [], "failed": []}


def _drafted_conversation(channel):
    channel.receive(notification(text_message("wamid.IN1", LANDING_MESSAGE)))
    channel.draft_pending(lambda **_: {"run_id": "run_1"})
    return channel.list_conversations()[0]["conversation_id"]


def test_send_is_blocked_until_every_gate_passes(channel):
    conversation_id = _drafted_conversation(channel)
    other = channel.conversation_id("5511911112222")
    channel.receive(notification(text_message("wamid.X", "oi", wa_id="5511911112222")))

    def send(**changes):
        options = {
            "actor": "fundador",
            "run_lookup": approved(),
            "client": FakeClient(),
            "enabled": True,
            "run_id": "run_1",
            "now": NOW + timedelta(minutes=5),
        }
        options.update(changes)
        target = options.pop("conversation", conversation_id)
        return channel.send(target, **options)

    disabled = send(enabled=False, client=None)
    unapproved = send(run_lookup=lambda _: {"status": "pending_review"})
    foreign = send(conversation=other)
    late = send(now=NOW + timedelta(hours=25))

    assert disabled["status"] == "BLOCKED"
    assert {"WHATSAPP_SEND_DISABLED", "WHATSAPP_CREDENTIALS_MISSING"} <= set(
        disabled["reasons"]
    )
    assert disabled["preview"]["to"] == "***0000"
    assert "RUN_NOT_APPROVED" in unapproved["reasons"]
    assert "RUN_NOT_FROM_THIS_CONVERSATION" in foreign["reasons"]
    assert "CUSTOMER_SERVICE_WINDOW_CLOSED" in late["reasons"]
    assert all(
        result["external_message_sent"] is False
        for result in (disabled, unapproved, foreign, late)
    )


def test_approved_reply_is_sent_once_and_status_is_tracked(channel):
    conversation_id = _drafted_conversation(channel)
    client = FakeClient()
    options = {
        "actor": "fundador",
        "run_lookup": approved(),
        "client": client,
        "enabled": True,
        "run_id": "run_1",
        "now": NOW + timedelta(minutes=5),
    }

    sent = channel.send(conversation_id, **options)
    again = channel.send(conversation_id, **options)
    channel.receive(
        notification(statuses=[{"id": "wamid.OUT1", "status": "delivered"}])
    )
    messages = channel.get(conversation_id)["messages"]

    assert sent == {
        "status": "SENT",
        "message_id": "wamid.OUT1",
        "external_message_sent": True,
    }
    assert again["status"] == "duplicate"
    assert client.sent == [(NUMBER, "Olá! O Ocean Blue está disponível para consulta.")]
    assert messages[-1]["delivery_status"] == "delivered"
    with pytest.raises(ValueError, match="idempotency_key_required"):
        channel.send(conversation_id, **{**options, "run_id": None, "text": "Oi"})


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self.ok = status < 400
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_cloud_client_uses_bearer_header_and_sanitizes_errors():
    ok = FakeSession(FakeResponse(200, {"messages": [{"id": "wamid.OK"}]}))
    client = WhatsAppCloudClient("secret-token", "PNID", session=ok)

    assert client.send_text(NUMBER, "Olá") == "wamid.OK"
    url, kwargs = ok.calls[0]
    assert url.endswith("/PNID/messages")
    assert kwargs["headers"] == {"Authorization": "Bearer secret-token"}
    assert kwargs["json"]["to"] == NUMBER and kwargs["json"]["type"] == "text"
    assert "secret-token" not in url

    failing = FakeSession(
        FakeResponse(400, {"error": {"code": 131047, "message": "leak me"}})
    )
    with pytest.raises(WhatsAppApiError) as error:
        WhatsAppCloudClient("secret-token", "PNID", session=failing).send_text(
            NUMBER, "x"
        )
    assert error.value.code == "META_ERROR_131047"
    assert "leak me" not in str(error.value)


def test_webhook_http_verification_signature_and_admin_views(tmp_path):
    rate_store.clear()
    body = json.dumps(notification(text_message("wamid.IN1", LANDING_MESSAGE))).encode()
    signature = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    environment = {
        "VERATUS_ADMIN_TOKEN": "admin-test-key",
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "VERATUS_SESSION_SALT": "salt",
        "VERATUS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "WHATSAPP_APP_SECRET": "app-secret",
        "WHATSAPP_VERIFY_TOKEN": "verify-me",
        "DATABASE_URL": "",
    }
    with patch.dict("os.environ", environment):
        client = app.test_client()
        verified = client.get(
            "/integrations/whatsapp/webhook?hub.mode=subscribe"
            "&hub.verify_token=verify-me&hub.challenge=4242"
        )
        refused = client.get(
            "/integrations/whatsapp/webhook?hub.mode=subscribe"
            "&hub.verify_token=wrong&hub.challenge=4242"
        )
        unsigned = client.post(
            "/integrations/whatsapp/webhook",
            data=body,
            content_type="application/json",
        )
        signed = client.post(
            "/integrations/whatsapp/webhook",
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": signature},
        )
        anonymous = client.get("/integrations/whatsapp/conversations")
        listing = client.get(
            "/integrations/whatsapp/conversations",
            headers={"X-Veratus-Admin-Token": "admin-test-key"},
        )
        conversation_id = listing.json["conversations"][0]["conversation_id"]
        blocked = client.post(
            f"/integrations/whatsapp/conversations/{conversation_id}/send",
            headers={"X-Veratus-Admin-Token": "admin-test-key"},
            json={"actor": "fundador", "text": "Oi!", "idempotency_key": "k1"},
        )
    with patch.dict("os.environ", {**environment, "VERATUS_SESSION_SALT": ""}):
        misconfigured = app.test_client().post(
            "/integrations/whatsapp/webhook",
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": signature},
        )

    assert verified.status_code == 200 and verified.get_data(as_text=True) == "4242"
    assert refused.status_code == 403
    assert unsigned.status_code == 401
    assert signed.status_code == 200 and signed.json["messages"] == 1
    assert anonymous.status_code == 401
    assert NUMBER not in listing.get_data(as_text=True)
    assert blocked.status_code == 409
    assert "WHATSAPP_SEND_DISABLED" in blocked.json["reasons"]
    assert misconfigured.status_code == 503
