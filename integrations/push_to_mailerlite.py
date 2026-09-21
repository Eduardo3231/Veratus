"""Sincroniza somente contatos cuja autorização foi revisada.

Exemplo seguro, sem envio:
    python -m integrations.push_to_mailerlite --input marketing/mailing-authorized.csv --dry-run

O arquivo precisa conter `consent=true` e uma `consent_source` não vazia em cada
linha. O webhook público deixa esses campos vazios; um operador precisa revisar a
origem antes da sincronização.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import time
from pathlib import Path
from typing import Any

import requests

API_ROOT = "https://api.mailerlite.com/api/v2"
CONSENT_VALUES = {"1", "true", "sim", "yes"}


def _headers() -> dict[str, str]:
    api_key = os.getenv("MAILERLITE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Defina MAILERLITE_API_KEY no ambiente.")
    return {"Content-Type": "application/json", "X-MailerLite-ApiKey": api_key}


def _contact_ref(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:10]


def read_authorized_leads(path: Path) -> tuple[list[dict[str, str]], int]:
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")
    authorized: list[dict[str, str]] = []
    skipped = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            email = (row.get("email") or "").strip().lower()
            consent = (row.get("consent") or "").strip().lower()
            consent_source = (row.get("consent_source") or "").strip()
            if not email or consent not in CONSENT_VALUES or not consent_source:
                skipped += 1
                continue
            authorized.append(
                {
                    "email": email,
                    "whatsapp": (row.get("whatsapp") or "").strip(),
                    "source": (row.get("source") or "reviewed-import").strip(),
                    "consent_source": consent_source[:200],
                }
            )
    return authorized, skipped


def get_subscriber_by_email(
    email: str, headers: dict[str, str]
) -> dict[str, Any] | None:
    response = requests.get(
        f"{API_ROOT}/subscribers/{email}", headers=headers, timeout=30
    )
    return response.json() if response.ok else None


def add_to_group(subscriber_id: str, group_id: str, headers: dict[str, str]) -> bool:
    response = requests.post(
        f"{API_ROOT}/groups/{group_id}/subscribers",
        json={"id": subscriber_id},
        headers=headers,
        timeout=30,
    )
    return response.ok


def upsert_subscriber(
    subscriber: dict[str, str], headers: dict[str, str], group_id: str | None
) -> tuple[bool, str]:
    payload = {
        "email": subscriber["email"],
        "fields": {
            "whatsapp": subscriber["whatsapp"],
            "source": subscriber["source"],
            "consent_source": subscriber["consent_source"],
        },
        "resubscribe": False,
    }
    response = requests.post(
        f"{API_ROOT}/subscribers", json=payload, headers=headers, timeout=30
    )
    if response.status_code in (200, 201):
        subscriber_id = response.json().get("id")
        if group_id and subscriber_id:
            add_to_group(str(subscriber_id), group_id, headers)
        return True, "created"
    if response.status_code == 400:
        found = get_subscriber_by_email(subscriber["email"], headers)
        if found:
            subscriber_id = str(found.get("id"))
            updated = requests.put(
                f"{API_ROOT}/subscribers/{subscriber_id}",
                json=payload,
                headers=headers,
                timeout=30,
            )
            if updated.ok:
                if group_id:
                    add_to_group(subscriber_id, group_id, headers)
                return True, "updated"
    return False, f"http_{response.status_code}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sincroniza leads revisados e autorizados com o MailerLite."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    subscribers, skipped = read_authorized_leads(args.input)
    print(f"Autorizados: {len(subscribers)}; ignorados: {skipped}.")
    if args.dry_run:
        return 0

    headers = _headers()
    group_id = os.getenv("MAILERLITE_GROUP_ID", "").strip() or None
    failures = 0
    for index, subscriber in enumerate(subscribers, start=1):
        ok, status = upsert_subscriber(subscriber, headers, group_id)
        print(
            f"[{index}/{len(subscribers)}] "
            f"ref={_contact_ref(subscriber['email'])} status={status}"
        )
        failures += int(not ok)
        time.sleep(0.5)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
