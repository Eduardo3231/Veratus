"""Exporta o feed do catálogo da Meta (Commerce Manager) para revisão.

    python scripts/exportar_catalogo_meta.py --saida catalogo.csv [--json relatorio.json]

Uma linha por produto pronto, a partir do anúncio do Facebook
(veratus_agents/meta_catalog.py), e o relatório de prontidão: quantos prontos,
quantos bloqueados e por quê. Linha com bloqueio não entra no CSV. Só leitura:
nada é enviado à Meta, sem rede, sem custo e sem fornecedor.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from veratus_agents.meta_catalog import (
    catalog_report,
    feed_csv,
    feed_rows,
    write_atomic,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/exportar_catalogo_meta.py",
        description="Feed CSV do catálogo da Meta, para revisão.",
    )
    parser.add_argument("--saida", required=True, type=Path, help="arquivo CSV")
    parser.add_argument("--json", type=Path, help="relatório em JSON (opcional)")
    args = parser.parse_args(argv)

    rows = feed_rows()
    report = catalog_report(rows)
    # Escrita atômica: um arquivo pela metade nunca fica no lugar do anterior.
    write_atomic(args.saida, feed_csv(rows))
    if args.json:
        write_atomic(args.json, json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print(
        f"{report['label']}: {report['ready']} prontos, {report['blocked']} "
        f"bloqueados de {report['total']} produtos"
    )
    print(f"  {report['exported']} linhas em {args.saida}")
    for reason in report["reasons"]:
        print(f"  {reason['code']} ({reason['count']}): {reason['text']}")
    if args.json:
        print(f"  relatório em {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
