#!/usr/bin/env python3
"""Uma pasta, um agente por vez (Claude Code, Codex ou pessoa).

Uso:
  python scripts/agent_lock.py status
  python scripts/agent_lock.py acquire --agent codex --scope "landing"
  python scripts/agent_lock.py release --agent codex

A trava expira em 4 horas sem renovação; renovar é chamar ``acquire`` de novo
com o mesmo agente.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parents[1] / ".agent-lock.json"
TTL = timedelta(hours=4)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def read_lock(path: Path = LOCK_PATH) -> dict | None:
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    expires = datetime.fromisoformat(
        lock.get("expires_at", "1970-01-01T00:00:00+00:00")
    )
    return lock if expires > _now() else None


def acquire(agent: str, scope: str, path: Path = LOCK_PATH) -> tuple[bool, dict]:
    current = read_lock(path)
    if current and current["agent"] != agent:
        return False, current
    lock = {
        "agent": agent,
        "scope": scope,
        "acquired_at": (current or {}).get("acquired_at", _now().isoformat()),
        "expires_at": (_now() + TTL).isoformat(),
    }
    if current is None:
        path.unlink(missing_ok=True)  # expired or unreadable
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False, read_lock(path) or {"agent": "unknown"}
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(lock, handle, indent=2)
    else:
        path.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    return True, lock


def release(agent: str, path: Path = LOCK_PATH, *, force: bool = False) -> bool:
    current = read_lock(path)
    if current and current["agent"] != agent and not force:
        return False
    path.unlink(missing_ok=True)
    return True


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    take = sub.add_parser("acquire")
    take.add_argument("--agent", required=True)
    take.add_argument("--scope", required=True)
    give = sub.add_parser("release")
    give.add_argument("--agent", required=True)
    give.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "status":
        lock = read_lock()
        print(json.dumps(lock, indent=2, ensure_ascii=False) if lock else "livre")
        return 0
    if args.command == "acquire":
        ok, lock = acquire(args.agent, args.scope)
        if not ok:
            print(
                f"OCUPADA por {lock.get('agent')} ({lock.get('scope')}) até "
                f"{lock.get('expires_at')}. Não edite esta pasta.",
                file=sys.stderr,
            )
            return 1
        print(f"trava de {args.agent} até {lock['expires_at']}")
        return 0
    if not release(args.agent, force=args.force):
        print(
            "a trava pertence a outro agente; use --force só com autorização",
            file=sys.stderr,
        )
        return 1
    print("livre")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
