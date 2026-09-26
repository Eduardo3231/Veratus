from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from integrations.webhook import app, rate_store
from veratus_agents.instagram_comments import (
    CommentReplyQueue,
    InstagramApiError,
    load_rules,
    match_rule,
)

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
LANDING_URL, RULES = load_rules()


def comments(*items):
    return {
        "object": "instagram",
        "entry": [
            {
                "id": "IG_ACCOUNT",
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": comment_id,
                            "text": text,
                            "from": {"id": author, "username": "alguem"},
                            "media": {"id": "MEDIA_1"},
                        },
                    }
                    for comment_id, text, author in items
                ],
            }
        ],
    }


class FakeClient:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def send_private_reply(self, comment_id, text):
        self.calls.append((comment_id, text))
        if self.error:
            raise self.error
        return f"m_{comment_id}"


def test_keywords_ignore_case_and_accents_but_not_partial_words():
    assert match_rule("Quero o CATÁLOGO!", RULES).rule_id == "colecao"
    assert match_rule("me manda o link", RULES).rule_id == "colecao"
    assert match_rule("que lindo", RULES) is None
    assert match_rule("catalogação", RULES) is None


def test_only_matching_comments_are_queued_once(tmp_path):
    queue = CommentReplyQueue(tmp_path / "ops.sqlite3")
    payload = comments(
        ("c1", "coleção", "user_1"),
        ("c2", "lindo!", "user_2"),
        ("c3", "catálogo", "IG_ACCOUNT"),
    )

    first = queue.enqueue(payload, RULES, own_account_id="IG_ACCOUNT")
    replay = queue.enqueue(payload, RULES, own_account_id="IG_ACCOUNT")
    stored = queue.list_replies()

    assert first == {"queued": 1, "ignored": 2, "duplicates": 0}
    assert replay["duplicates"] == 1
    assert [item["comment_id"] for item in stored] == ["c1"]
    assert "user_1" not in json.dumps(stored)


def test_disabled_sending_only_previews_a_tracked_link(tmp_path):
    queue = CommentReplyQueue(tmp_path / "ops.sqlite3")
    queue.enqueue(comments(("c1", "catalogo", "user_1")), RULES)
    client = FakeClient()

    result = queue.process(
        client=client, enabled=False, landing_url=LANDING_URL, rules=RULES
    )

    [preview] = result["preview"]
    link = next(word for word in preview["text"].split() if word.startswith("https://"))
    query = parse_qs(urlsplit(link).query)
    assert result["blocked_reasons"] == ["INSTAGRAM_DM_DISABLED"]
    assert query == {
        "utm_source": ["instagram"],
        "utm_medium": ["comment_dm"],
        "utm_campaign": ["comentario_colecao"],
        "utm_content": ["MEDIA_1"],
    }
    assert client.calls == []
    assert queue.list_replies()[0]["status"] == "PENDING"


def test_enabled_sending_replies_once_expires_old_and_retries_transient(tmp_path):
    queue = CommentReplyQueue(tmp_path / "ops.sqlite3")
    queue.enqueue(comments(("c1", "catalogo", "u1")), RULES)
    options = {"landing_url": LANDING_URL, "rules": RULES}

    client = FakeClient()
    sent = queue.process(client=client, enabled=True, now=NOW, **options)
    again = queue.process(client=client, enabled=True, now=NOW, **options)

    assert sent["sent"] == ["c1"] and sent["external_messages_sent"] == 1
    assert again["sent"] == [] and len(client.calls) == 1

    queue.enqueue(comments(("c2", "coleção", "u2")), RULES)
    transient = queue.process(
        client=FakeClient(InstagramApiError("META_ERROR_2", 500)),
        enabled=True,
        **options,
    )
    assert transient["failed"][0]["error"] == "META_ERROR_2"
    assert {item["comment_id"]: item["status"] for item in queue.list_replies()}[
        "c2"
    ] == "PENDING"

    expired = queue.process(
        client=FakeClient(),
        enabled=True,
        now=datetime.now(timezone.utc) + timedelta(days=8),
        **options,
    )
    assert expired["expired"] == ["c2"]


def test_instagram_webhook_requires_signature_and_queues(tmp_path):
    rate_store.clear()
    body = json.dumps(comments(("c9", "Quero o catálogo", "user_9"))).encode()
    signature = "sha256=" + hmac.new(b"ig-secret", body, hashlib.sha256).hexdigest()
    environment = {
        "VERATUS_ADMIN_TOKEN": "admin-test-key",
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "INSTAGRAM_APP_SECRET": "ig-secret",
        "INSTAGRAM_DM_ENABLED": "false",
        "DATABASE_URL": "",
    }
    with patch.dict("os.environ", environment):
        client = app.test_client()
        unsigned = client.post(
            "/integrations/instagram/webhook",
            data=body,
            content_type="application/json",
        )
        signed = client.post(
            "/integrations/instagram/webhook",
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": signature},
        )
        processed = client.post(
            "/integrations/instagram/replies/process",
            headers={"X-Veratus-Admin-Token": "admin-test-key"},
            json={},
        )

    assert unsigned.status_code == 401
    assert signed.status_code == 200 and signed.json["queued"] == 1
    assert processed.json["external_messages_sent"] == 0
    assert "INSTAGRAM_DM_DISABLED" in processed.json["blocked_reasons"]
