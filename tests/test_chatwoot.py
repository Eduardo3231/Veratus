"""Chatwoot: caixa única do fundador, sem dado pessoal e sem eco das nossas mensagens."""

from __future__ import annotations

import hashlib
import hmac
import logging

import requests

from veratus_agents import chatwoot
from veratus_agents.chatwoot import ChatwootBridge, ChatwootClient

KEY = "whatsapp:wa_" + "a" * 32
TOKEN = "cw-test-token"


class _Response:
    def __init__(self, body, status=200):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class _Session:
    """Chatwoot de mentira: guarda cada POST e responde como a API."""

    def __init__(self, fail=None):
        self.calls, self.fail, self.next_id = [], dict(fail or {}), 500

    def post(self, url, *, json, headers, timeout):
        assert headers == {"api_access_token": TOKEN}
        path = url.split("/api/v1/accounts/7", 1)[1]
        self.calls.append((path, json))
        for suffix, error in list(self.fail.items()):
            if path.endswith(suffix):
                del self.fail[suffix]
                raise error
        if path == "/contacts":
            contact = {"id": 11, "contact_inboxes": [{"source_id": "src-1"}]}
            return _Response({"payload": {"contact": contact}})
        if path == "/conversations":
            return _Response({"id": 42})
        if path.endswith("/messages"):
            self.next_id += 1
            return _Response({"id": self.next_id})
        return _Response({})


def _bridge(tmp_path, session, enabled=True):
    client = ChatwootClient("https://cw.test", 7, TOKEN, session=session)
    return ChatwootBridge(tmp_path / "cw.sqlite3", client, inbox_id=3, enabled=enabled)


def _founder(message_id, text, **changes):
    payload = {
        "event": "message_created",
        "id": message_id,
        "content": text,
        "message_type": "outgoing",
        "private": False,
        "sender": {"id": 1, "type": "user"},
        "conversation": {"id": 42, "status": "open"},
        "content_attributes": {},
    }
    return {**payload, **changes}


def test_flag_off_makes_no_call_and_webhook_needs_the_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("CHATWOOT_SYNC_ENABLED", "1")
    session = _Session()
    bridge = _bridge(tmp_path, session, enabled=None)
    assert bridge.mirror_inbound("whatsapp", KEY, "oi") == {"status": "DISABLED"}
    assert bridge.mirror_reply(KEY, "Olá!") == {"status": "DISABLED"}
    assert bridge.escalate(KEY, "PEDIDO_DE_ATENDENTE") == {"status": "DISABLED"}
    assert bridge.handle_webhook(_founder(9, "oi"))["action"] == "IGNORE"
    assert session.calls == []

    monkeypatch.delenv("CHATWOOT_WEBHOOK_TOKEN", raising=False)
    assert not chatwoot.verify_webhook_token("")
    assert not chatwoot.verify_webhook_token("qualquer")
    monkeypatch.setenv("CHATWOOT_WEBHOOK_TOKEN", "cw-hook-test")
    assert chatwoot.verify_webhook_token("cw-hook-test")
    assert not chatwoot.verify_webhook_token("cw-hook-tesT")
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "cw-secret-test")
    body, ts = b'{"event":"message_created"}', "1700000000"
    good = (
        "sha256="
        + hmac.new(
            b"cw-secret-test", ts.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
    )
    assert chatwoot.verify_webhook_signature(body, ts, good, now=1700000010)
    assert not chatwoot.verify_webhook_signature(body + b" ", ts, good, now=1700000010)
    assert not chatwoot.verify_webhook_signature(body, ts, good, now=1700009999)


def test_mirror_strips_personal_data_and_reuses_the_mapping(tmp_path):
    session = _Session()
    bridge = _bridge(tmp_path, session)
    digits = "".join(str((3 * index + 1) % 10) for index in range(11))
    document = f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    phone = "(11) 9" + "8765-" + "4321"
    text = (
        f"Meu CPF {document}, fone {phone}, ana@exemplo.com\n"
        "Rua das Flores, 120, CEP 01310-100\nQuero o Ocean Blue"
    )
    first = bridge.mirror_inbound("whatsapp", KEY, text)
    assert first["status"] == "MIRRORED" and first["conversation_id"] == "42"
    bridge.mirror_inbound(
        "whatsapp", KEY, "Ana Lima, CEP 01310-100", intent="dados_pedido"
    )

    paths = [path for path, _ in session.calls]
    assert paths == [
        "/contacts",
        "/conversations",
        "/conversations/42/labels",
        "/conversations/42/messages",
        "/conversations/42/messages",
    ]
    contact, conversation, labels = (body for _, body in session.calls[:3])
    assert contact["name"] == "Cliente · WhatsApp" and contact["inbox_id"] == 3
    assert KEY not in contact["identifier"] and "wa_" not in contact["identifier"]
    assert conversation["custom_attributes"] == {"veratus_channel": "whatsapp"}
    assert conversation["status"] == "pending" and labels == {"labels": ["whatsapp"]}
    message = session.calls[3][1]
    assert message["message_type"] == "incoming" and not message["private"]
    sent = " ".join(body["content"] for _, body in session.calls[3:]) + repr(contact)
    for secret in (document, digits, "98765", "4321", "ana@", "01310", "Flores", "Ana"):
        assert secret not in sent, secret
    assert "Quero o Ocean Blue" in message["content"]
    assert session.calls[4][1]["content"] == chatwoot.ORDER_DATA_NOTE


def test_only_the_founder_reply_comes_back_never_our_own_echo(tmp_path):
    session = _Session(fail={"/messages": requests.Timeout()})
    bridge = _bridge(tmp_path, session)
    lost = bridge.mirror_reply(KEY, "Olá! Aqui é o Harvey.")
    assert lost == {"status": "FAILED", "error_type": "Timeout"}
    posted = bridge.mirror_reply(KEY, "O Ocean Blue sai por R$ 289,90.")
    ours = posted["message_id"]

    ignored = [
        _founder(ours, "O Ocean Blue sai por R$ 289,90."),  # id que postamos
        _founder(777, "Olá! Aqui é o Harvey."),  # POST caído: o eco pelo conteúdo
        _founder(778, "eco", content_attributes={"veratus_mirror": True}),
        _founder(779, "nota", private=True),
        _founder(780, "cliente", message_type="incoming"),
        _founder(781, "robô", sender={"id": 2, "type": "agent_bot"}),
        _founder(782, "outra", conversation={"id": 99}),
        {"event": "conversation_status_changed", "id": 42, "status": "open"},
        {"event": "contact_created", "id": 11},
        "não é json",
    ]
    assert [bridge.handle_webhook(item)["action"] for item in ignored] == [
        "IGNORE"
    ] * len(ignored)

    reply = bridge.handle_webhook(_founder(790, " Oi, aqui é o fundador. "))
    assert reply == {
        "action": "SEND",
        "conv_key": KEY,
        "text": "Oi, aqui é o fundador.",
        "message_id": "790",
    }
    repeated = bridge.handle_webhook(_founder(790, "Oi, aqui é o fundador."))
    assert repeated == {"action": "IGNORE", "reason": "duplicate"}


def test_escalate_opens_for_the_founder_and_resolve_returns_to_bot(tmp_path, caplog):
    session = _Session()
    bridge = _bridge(tmp_path, session)
    bridge.mirror_inbound("instagram", "instagram:ig_1", "quero falar com uma pessoa")
    session.calls.clear()

    escalated = bridge.escalate("instagram:ig_1", "PEDIDO_DE_ATENDENTE")
    assert escalated == {"status": "ESCALATED", "conversation_id": "42"}
    note, labels, status = (body for _, body in session.calls)
    assert (
        note["private"] is True and "pediu para falar com uma pessoa" in note["content"]
    )
    assert labels == {"labels": ["instagram", "fundador"]}
    assert status == {"status": "open"}

    resolved = {"event": "conversation_status_changed", "id": 42, "status": "resolved"}
    assert bridge.handle_webhook(resolved) == {
        "action": "BOT",
        "conv_key": "instagram:ig_1",
    }
    session.calls.clear()
    bridge.mirror_inbound("instagram", "instagram:ig_1", "obrigado")
    assert session.calls[-1] == (
        "/conversations/42/toggle_status",
        {"status": "pending"},
    )

    session.fail = {"/messages": chatwoot.ChatwootError(422)}
    with caplog.at_level(logging.WARNING, logger="veratus.chatwoot"):
        failed = bridge.mirror_inbound(
            "instagram", "instagram:ig_1", "segredo do cliente"
        )
    assert failed == {"status": "FAILED", "error_type": "CHATWOOT_HTTP_422"}
    assert "segredo" not in caplog.text and "CHATWOOT_HTTP_422" in caplog.text
