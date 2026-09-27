"""Nenhum envio para quem não iniciou conversa com a Veratus.

A Meta só permite responder: no Instagram, a quem comentou (resposta privada
ao comment_id); no WhatsApp, dentro da janela de 24 h aberta pelo cliente.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from veratus_agents.instagram_comments import InstagramClient
from veratus_agents.whatsapp import send_block_reasons

ROOT = Path(__file__).resolve().parents[1]
# A message addressed to a person's account ID instead of to a comment.
USER_ADDRESSED = re.compile(
    r"""["']recipient["']\s*:\s*\{\s*["'](id|user_id|username)["']"""
)
BULK_SENDER = ROOT / "integrations" / "meta_dm_sender.py"


def _repository_sources() -> list[Path]:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
        paths = [ROOT / name for name in listed.split("\0") if name]
    except (OSError, subprocess.CalledProcessError):
        paths = [path for path in ROOT.rglob("*") if ".venv" not in path.parts]
    return [
        path
        for path in paths
        if path.suffix in {".py", ".md", ".json"} and path.is_file()
    ]


def test_bulk_dm_sender_does_not_exist() -> None:
    assert not BULK_SENDER.exists()


def test_no_file_messages_a_person_by_account_id() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _repository_sources()
        if USER_ADDRESSED.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert offenders == []


def test_whatsapp_text_leaves_only_through_the_gated_channel() -> None:
    callers = {
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in _repository_sources()
        if path.suffix == ".py"
        and "tests" not in path.relative_to(ROOT).parts
        and ".send_text(" in path.read_text(encoding="utf-8", errors="ignore")
    }
    assert callers <= {"veratus_agents/whatsapp.py"}


def test_whatsapp_refuses_conversations_the_customer_did_not_open() -> None:
    now = datetime.now(timezone.utc)
    allowed = {"now": now, "enabled": True, "credentials_present": True}

    never_wrote = {"state": "HUMAN", "last_inbound_at": None}
    wrote_long_ago = {
        "state": "HUMAN",
        "last_inbound_at": (now - timedelta(hours=25)).isoformat(),
    }
    wrote_recently = {
        "state": "HUMAN",
        "last_inbound_at": (now - timedelta(hours=1)).isoformat(),
    }

    for conversation in (never_wrote, wrote_long_ago):
        reasons = send_block_reasons(conversation, "Olá", **allowed)
        assert "CUSTOMER_SERVICE_WINDOW_CLOSED" in reasons
    assert send_block_reasons(wrote_recently, "Olá", **allowed) == []


def test_instagram_only_replies_to_a_comment() -> None:
    sent: list[dict] = []

    class _Response:
        ok = True

        @staticmethod
        def json() -> dict:
            return {"message_id": "m1"}

    class _Session:
        def post(self, url: str, **kwargs):
            sent.append(kwargs["json"])
            return _Response()

    InstagramClient("token", "ig-user", session=_Session()).send_private_reply(
        "comment-1", "Olá"
    )

    assert sent == [
        {"recipient": {"comment_id": "comment-1"}, "message": {"text": "Olá"}}
    ]
