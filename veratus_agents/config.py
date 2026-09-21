from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env", override=False)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AgentSettings:
    model: str
    database_url: str | None
    runtime_dir: Path
    operations_db: Path
    memory_db: Path
    trace_sensitive_data: bool
    max_turns: int
    max_output_tokens: int
    request_timeout_seconds: float
    workflow_timeout_seconds: float
    model_max_retries: int
    max_total_tokens: int
    session_history_limit: int

    @classmethod
    def from_env(cls) -> AgentSettings:
        runtime_setting = os.getenv("VERATUS_AGENT_RUNTIME_DIR", "").strip()
        runtime_dir = Path(runtime_setting) if runtime_setting else BASE_DIR / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            model=os.getenv("VERATUS_AGENT_MODEL", "gpt-4.1-mini").strip()
            or "gpt-4.1-mini",
            database_url=os.getenv("DATABASE_URL", "").strip() or None,
            runtime_dir=runtime_dir,
            operations_db=runtime_dir / "agent-operations.sqlite3",
            memory_db=runtime_dir / "agent-memory.sqlite3",
            trace_sensitive_data=_env_bool("VERATUS_AGENT_TRACE_SENSITIVE_DATA", False),
            max_turns=max(2, int(os.getenv("VERATUS_AGENT_MAX_TURNS", "8"))),
            max_output_tokens=max(
                200, int(os.getenv("VERATUS_AGENT_MAX_OUTPUT_TOKENS", "900"))
            ),
            request_timeout_seconds=max(
                5.0, float(os.getenv("VERATUS_AGENT_REQUEST_TIMEOUT_SECONDS", "45"))
            ),
            workflow_timeout_seconds=max(
                20.0, float(os.getenv("VERATUS_AGENT_WORKFLOW_TIMEOUT_SECONDS", "90"))
            ),
            model_max_retries=max(
                0, min(2, int(os.getenv("VERATUS_AGENT_MODEL_MAX_RETRIES", "1")))
            ),
            max_total_tokens=max(
                1000, int(os.getenv("VERATUS_AGENT_MAX_TOTAL_TOKENS", "12000"))
            ),
            session_history_limit=max(
                4, int(os.getenv("VERATUS_AGENT_SESSION_HISTORY_LIMIT", "30"))
            ),
        )
