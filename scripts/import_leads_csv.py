"""Importa para o banco as linhas de um leads.csv antigo com consentimento válido.

Uso:
    python scripts/import_leads_csv.py --input caminho/leads.csv --dry-run
    python scripts/import_leads_csv.py --input caminho/leads.csv

Consentimento válido: ``consent`` em {1, true, sim, yes} e ``consent_source``
preenchido. O webhook público nunca gravou esses campos, então linhas sem eles
ficam de fora. A importação é idempotente e só imprime contagens, nunca contatos.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from veratus_agents.config import AgentSettings
from veratus_agents.leads import LeadStore, has_valid_consent, import_ref

UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content")


def _campaign(row: dict[str, str]) -> dict[str, str]:
    try:
        stored = json.loads(row.get("campaign") or "{}")
    except ValueError:
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    for key in UTM_KEYS:
        if row.get(key):
            stored.setdefault(key, row[key])
    return {key: str(value)[:200] for key, value in stored.items() if key in UTM_KEYS}


def import_csv(path: Path, store: LeadStore | None) -> dict[str, int]:
    """Import consented rows; with ``store=None`` only count (dry run)."""
    counts = dict.fromkeys(
        ("rows", "valid_consent", "imported", "already_imported", "skipped"), 0
    )
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            counts["rows"] += 1
            if not has_valid_consent(row.get("consent"), row.get("consent_source")):
                counts["skipped"] += 1
                continue
            counts["valid_consent"] += 1
            if store is None:
                continue
            inserted = store.add(
                email=(row.get("email") or "").strip().lower(),
                whatsapp=(row.get("whatsapp") or "").strip(),
                source=(row.get("source") or "csv-import").strip(),
                campaign=_campaign(row),
                consent=(row.get("consent") or "").strip().lower(),
                consent_source=(row.get("consent_source") or "").strip(),
                received_at=(row.get("timestamp") or "").strip() or None,
                reference=import_ref(row),
            )
            counts["imported" if inserted else "already_imported"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    store = None
    if not args.dry_run:
        settings = AgentSettings.from_env()
        store = LeadStore(settings.operations_db, settings.database_url)
    print(json.dumps(import_csv(args.input, store), ensure_ascii=False))


if __name__ == "__main__":
    main()
