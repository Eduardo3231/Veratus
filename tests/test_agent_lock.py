import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "agent_lock", Path(__file__).resolve().parents[1] / "scripts" / "agent_lock.py"
)
agent_lock = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent_lock)


def test_second_agent_is_refused_until_release(tmp_path):
    path = tmp_path / ".agent-lock.json"

    assert agent_lock.acquire("claude", "automações", path)[0]
    refused, holder = agent_lock.acquire("codex", "landing", path)
    renewed, _ = agent_lock.acquire("claude", "automações", path)

    assert refused is False and holder["agent"] == "claude"
    assert renewed is True
    assert agent_lock.release("codex", path) is False
    assert agent_lock.release("claude", path) is True
    assert agent_lock.acquire("codex", "landing", path)[0]


def test_expired_lock_is_free(tmp_path):
    path = tmp_path / ".agent-lock.json"
    old = datetime.now(timezone.utc) - timedelta(hours=5)
    path.write_text(
        json.dumps({"agent": "codex", "scope": "x", "expires_at": old.isoformat()}),
        encoding="utf-8",
    )

    assert agent_lock.read_lock(path) is None
    assert agent_lock.acquire("claude", "automações", path)[0]
