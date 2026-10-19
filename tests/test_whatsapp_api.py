"""WhatsApp: templates, coexistência, mídia, status, opt-out, botões e saúde.

Tudo com cliente falso: nenhuma chamada sai da máquina.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from veratus_agents.channel_adapters import FounderAlertSender, WhatsAppAdapter
from veratus_agents.conversations import DraftRequest, TemplateSalesModel
from veratus_agents.harvey import WHATSAPP_NUMBER
from veratus_agents.whatsapp import (
    WhatsAppChannel,
    account_health,
    load_templates,
)

NUMBER = "5511999990000"
NOW = datetime.now(timezone.utc)


def change(field, value):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": field, "value": value}]}],
    }


def messages(*items, statuses=(), pnid="PNID"):
    return change(
        "messages",
        {
            "metadata": {"phone_number_id": pnid},
            "messages": list(items),
            "statuses": list(statuses),
        },
    )


def item(message_id, kind="text", at=NOW, **content):
    return {
        "from": NUMBER,
        "id": message_id,
        "timestamp": str(int(at.timestamp())),
        "type": kind,
        **content,
    }


class FakeClient:
    phone_number_id = "PNID"

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def send_message(self, to, content):
        self.calls.append(("send", to, content))
        return f"wamid.OUT{len(self.calls)}"

    def send_text(self, to, body):
        return self.send_message(to, {"type": "text", "text": {"body": body}})

    def mark_read(self, message_id, *, typing=True):
        self.calls.append(("read", message_id, typing))

    def get(self, path, fields):
        self.calls.append(("get", path, fields))
        return next(v for k, v in self.responses.items() if path.endswith(k))


@pytest.fixture
def channel(tmp_path):
    return WhatsAppChannel(
        tmp_path / "ops.sqlite3",
        salt="sal",
        encryption_key=Fernet.generate_key().decode(),
    )


def _conversation(channel, at=NOW):
    channel.receive(
        messages(item("wamid.IN1", text={"body": "oi"}, at=at)), phone_number_id=""
    )
    return channel.list_conversations()[0]["conversation_id"]


def test_template_only_with_flags_registry_and_linked_order(channel):
    conversation_id = _conversation(channel, at=NOW - timedelta(days=2))
    client = FakeClient()
    adapter = WhatsAppAdapter(
        channel, client=client, enabled=True, order_lookup=lambda cid: "ord-1"
    )
    off = adapter.send_template(
        conversation_id, "pedido_confirmado", idempotency_key="p1"
    )
    missing = WhatsAppAdapter(channel, client=client, enabled=True).send_template(
        conversation_id, "pedido_confirmado", idempotency_key="p1"
    )
    unknown = channel.deliver(
        "wa_nao_existe",
        {},
        body="x",
        kind="template",
        actor="t",
        client=client,
        enabled=True,
        idempotency_key="k",
    )
    registry = load_templates()
    registry["pedido_enviado"] = {**registry["pedido_enviado"], "status": "APPROVED"}
    sent = channel.send_template(
        conversation_id,
        "pedido_enviado",
        ["ord-1", "AA 1\n\tBR"],
        actor="t",
        client=client,
        enabled=True,
        templates_enabled=True,
        idempotency_key="s1",
        registry=registry,
    )
    channel.receive(
        change(
            "message_template_status_update",
            {
                "event": "PAUSED",
                "message_template_name": "pedido_enviado",
                "message_template_language": "pt_BR",
            },
        )
    )
    paused = channel.send_template(
        conversation_id,
        "pedido_enviado",
        ["ord-1", "AA1BR"],
        actor="t",
        client=client,
        enabled=True,
        templates_enabled=True,
        idempotency_key="s2",
        registry=registry,
    )

    assert off["status"] == "BLOCKED"
    assert {"WHATSAPP_TEMPLATES_DISABLED", "TEMPLATE_NOT_APPROVED"} <= set(
        off["reasons"]
    )
    assert missing["reasons"] == ["ORDER_NOT_LINKED"]
    assert unknown["reasons"] == ["CONVERSATION_NOT_FOUND"]
    assert sent["status"] == "SENT"
    [(_, to, content)] = client.calls
    assert to == NUMBER
    texts = [p["text"] for p in content["template"]["components"][0]["parameters"]]
    assert texts == ["ord-1", "AA 1 BR"]
    out = [
        m for m in channel.get(conversation_id)["messages"] if m["direction"] == "OUT"
    ]
    assert out[0]["kind"] == "template" and "AA 1 BR" in out[0]["body"]
    assert "TEMPLATE_PAUSED" in paused["reasons"]


def test_founder_echo_stops_the_bot_and_history_is_not_a_message(channel):
    conversation_id = _conversation(channel)
    echo = change(
        "smb_message_echoes",
        {
            "metadata": {"phone_number_id": "PNID"},
            "message_echoes": [
                {
                    "from": WHATSAPP_NUMBER,
                    "to": NUMBER,
                    "id": "wamid.ECHO",
                    "timestamp": str(int(NOW.timestamp())),
                    "type": "text",
                    "text": {"body": "Oi, aqui é o responsável."},
                }
            ],
        },
    )
    replied = []
    channel.receive(echo, on_founder_reply=replied.append, phone_number_id="PNID")
    channel.receive(echo, on_founder_reply=replied.append, phone_number_id="PNID")
    history = channel.receive(change("history", {"history": [{"threads": []}]}))
    conversation = channel.get(conversation_id)

    assert replied == [conversation_id]
    assert conversation["state"] == "HUMAN"
    assert [m["actor"] for m in conversation["messages"]] == [None, "founder-app"]
    assert history["messages"] == 0 and history["ignored"] == 1


def test_media_and_clicks_reach_the_engine(channel):
    channel.receive(
        messages(
            item("wamid.A", "audio", audio={"id": "m1"}),
            item(
                "wamid.B",
                "interactive",
                at=NOW + timedelta(seconds=1),
                interactive={
                    "type": "button_reply",
                    "button_reply": {"id": "quero_pedir", "title": "x"},
                },
            ),
            item("wamid.C", "reaction", at=NOW + timedelta(seconds=2), reaction={}),
        ),
        phone_number_id="",
    )
    pending = WhatsAppAdapter(channel, client=None).pending()
    reply = TemplateSalesModel().draft(
        DraftRequest(
            channel="whatsapp",
            conversation_id="c",
            message="",
            intent="midia_recebida",
            topic="audio",
            product=None,
            context={},
            order=None,
            discount_request=False,
        )
    )["customer_reply"]

    assert [(p.kind, p.text) for p in pending] == [
        ("audio", ""),
        ("text", "Quero pedir"),
    ]
    assert reply.startswith("Recebi seu áudio")


def test_other_number_is_dropped_and_failed_status_opens_one_task(channel):
    conversation_id = _conversation(channel)
    other = channel.receive(
        messages(item("wamid.T", text={"body": "teste"}), pnid="TEST"),
        phone_number_id="PNID",
    )
    channel.deliver(
        conversation_id,
        {"type": "text"},
        body="Olá",
        kind="text",
        actor="t",
        client=FakeClient(),
        enabled=True,
        idempotency_key="k1",
    )
    status = {"id": "wamid.OUT1", "status": "failed", "errors": [{"code": 131026}]}
    failed = []
    for _ in range(2):  # a Meta repete o webhook
        channel.receive(
            messages(statuses=[status]),
            on_send_failed=lambda *a: failed.append(a),
            phone_number_id="PNID",
        )

    assert other["messages"] == 0 and other["ignored"] == 1
    assert failed == [(conversation_id, "131026")]


def test_founder_alert_has_its_own_flag_and_never_uses_the_business_number():
    emails = []
    client = FakeClient()
    alert = {"channel": "whatsapp", "reason": "R", "summary": "a\nb", "suggestion": "s"}
    own = FounderAlertSender(
        whatsapp_client=client,
        founder_number=f"+{WHATSAPP_NUMBER}",
        whatsapp_enabled=True,
        email_sender=lambda *a: emails.append(a),
    )
    with patch.dict(
        "os.environ",
        {"WHATSAPP_SEND_ENABLED": "true", "FOUNDER_ALERT_WHATSAPP_ENABLED": "false"},
    ):
        default = FounderAlertSender(
            whatsapp_client=client,
            founder_number="5511900000000",
            email_sender=lambda *a: emails.append(a),
        )(alert)

    assert own(alert) == "EMAIL" and default == "EMAIL"
    assert client.calls == []


def test_opt_out_revokes_marketing_and_skips_the_engine(channel):
    conversation_id = _conversation(channel)
    channel.grant_opt_in(conversation_id, source="site")
    adapter = WhatsAppAdapter(channel, client=None)
    allowed = adapter.marketing_allowed(conversation_id)
    out = []
    channel.receive(
        messages(
            item("wamid.OUT", text={"body": "Parar"}, at=NOW + timedelta(seconds=1))
        ),
        on_opt_out=lambda *a: out.append(a),
        phone_number_id="",
    )

    assert allowed and not adapter.marketing_allowed(conversation_id)
    assert out == [(conversation_id, "wamid.OUT")]
    assert [m["message_id"] for m in channel.pending_inbound()] == ["wamid.IN1"]


def test_buttons_receipts_and_images_stay_behind_their_flags(channel):
    conversation_id = _conversation(channel)
    client = FakeClient()
    adapter = WhatsAppAdapter(channel, client=client, enabled=True)
    link = "Pague aqui: https://www.mercadopago.com.br/checkout/v1/redirect?pref_id=1"
    env = {
        "WHATSAPP_INTERACTIVE_ENABLED": "true",
        "WHATSAPP_READ_RECEIPTS_ENABLED": "true",
        "CONVERSATION_MODE_WHATSAPP": "AUTO",
        "WHATSAPP_MEDIA_ENABLED": "false",
    }
    with patch.dict("os.environ", env):
        adapter.send(conversation_id, link, idempotency_key="pay")
        adapter.pending()
        adapter.pending()  # a mesma mensagem não recebe dois recibos
        image = adapter.send_image(
            conversation_id, "nao-existe.webp", "Ocean Blue", idempotency_key="img"
        )
    with patch.dict(
        "os.environ",
        {
            "CONVERSATION_MODE_WHATSAPP": "SHADOW",
            "WHATSAPP_READ_RECEIPTS_ENABLED": "true",
        },
    ):
        shadow = FakeClient()
        WhatsAppAdapter(channel, client=shadow, enabled=True).pending()

    kinds = [
        (call[0], call[2]["type"] if call[0] == "send" else call[2])
        for call in client.calls
    ]
    assert kinds == [("send", "interactive"), ("read", True)]
    assert client.calls[0][2]["interactive"]["type"] == "cta_url"
    assert {"MEDIA_DISABLED", "IMAGE_NOT_RELEASED"} <= set(image["reasons"])
    assert shadow.calls == []


def test_health_reads_the_account_without_writing():
    templates = {
        "data": [
            {
                "name": n,
                "language": "pt_BR",
                "status": "APPROVED",
                "category": "UTILITY",
            }
            for n in load_templates()
        ]
    }
    client = FakeClient(
        {
            "PNID": {
                "display_phone_number": "+55 11 95832-3612",
                "name_status": "APPROVED",
                "quality_rating": "GREEN",
            },
            "subscribed_apps": {"data": [{"id": "app"}]},
            "message_templates": templates,
        }
    )
    report = account_health(client, "WABA", expected_number=WHATSAPP_NUMBER)
    missing = account_health(None, None, expected_number=WHATSAPP_NUMBER)

    assert report["status"] == "OK", json.dumps(report)
    assert {call[0] for call in client.calls} == {"get"}
    assert missing["status"] == "NOT_CONFIGURED" and not missing["external_call"]
