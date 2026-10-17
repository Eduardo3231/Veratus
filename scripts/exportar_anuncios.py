"""Exporta os anúncios de marketplace do catálogo para revisão (nada é publicado).

    python scripts/exportar_anuncios.py --saida anuncios.json

Todos os produtos do catálogo × Mercado Livre, Shopee, Amazon e TikTok Shop
(veratus_agents/listing_content.py), mais o resumo de prontidão por canal:
quantos prontos, quantos bloqueados e por quê. Só leitura: sem rede, sem custo
e sem fornecedor (só campos públicos do catálogo e fatos confirmados).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from veratus_agents.listing_content import export_listings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/exportar_anuncios.py",
        description="Anúncios de marketplace do catálogo, para revisão.",
    )
    parser.add_argument("--saida", required=True, type=Path, help="arquivo JSON")
    args = parser.parse_args(argv)

    data = export_listings()
    output = args.saida
    output.parent.mkdir(parents=True, exist_ok=True)
    # Escrita atômica: um JSON pela metade nunca fica no lugar do anterior.
    partial = output.with_name(output.name + ".parcial")
    partial.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    partial.replace(output)

    print(f"{len(data['listings'])} anúncios em {output}")
    for item in data["summary"].values():
        reasons = ", ".join(
            f"{reason['code']} ({reason['count']})" for reason in item["reasons"][:4]
        )
        print(
            f"  {item['label']}: {item['ready']} prontos, {item['blocked']} bloqueados"
            + (f" — {reasons}" if reasons else "")
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
