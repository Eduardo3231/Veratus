from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .config import AgentSettings
from .metrics import make_metrics_repository, summarize_performance
from .schemas import PerformanceRecord


def import_csv(path: Path) -> tuple[int, int]:
    settings = AgentSettings.from_env()
    store = make_metrics_repository(settings.operations_db, settings.database_url)
    created = duplicates = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            try:
                record = PerformanceRecord.model_validate(row)
            except Exception as exc:
                raise ValueError(f"Linha {line_number} inválida: {exc}") from exc
            if store.add(record):
                created += 1
            else:
                duplicates += 1
    return created, duplicates


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Importa métricas verificadas e calcula KPIs da Veratus."
    )
    parser.add_argument("--import-csv", type=Path, dest="csv_path")
    parser.add_argument("--channel")
    parser.add_argument("--campaign")
    args = parser.parse_args()

    settings = AgentSettings.from_env()
    if args.csv_path:
        created, duplicates = import_csv(args.csv_path)
        print(f"Importados: {created}; duplicados ignorados: {duplicates}.")
    store = make_metrics_repository(settings.operations_db, settings.database_url)
    summary = summarize_performance(
        store.list_records(channel=args.channel, campaign=args.campaign)
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
