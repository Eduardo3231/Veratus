"""Importa correntes e joias de revenda para o catálogo Veratus.

Uso (a regra de preço vem de JEWELRY_PRICE_RULE, nunca do Git):

    JEWELRY_PRICE_RULE=limite:baixo:alto python scripts/importar_joias.py \\
        --url https://fornecedor/categoria/ [--url ...] [--limite 40] [--aplicar]
    python scripts/importar_joias.py --planilha pecas.csv [--aplicar]

Sem ``--aplicar`` só mostra o relatório. Com ele:
- fotos normalizadas em landing/assets/jewelry/ (renomeadas, sem metadados);
- peças em catalog/products.json (só dados públicos: nunca custo nem fornecedor);
- custo, preço, referência e endereço do fornecedor no Product Master (banco);
- relatório privado em runtime/joias/importacao.json (fora do Git);
- landing/catalog.json e a página refeitos.

A planilha (CSV) tem as colunas nome, preco, imagens (caminhos ou URLs
separados por "|"), url, sku e descricao.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from veratus_agents.jewelry_import import (
    RULE_ENV,
    PriceRule,
    SupplierItem,
    build_draft,
    extract_products,
    social_draft,
)

CATALOG = ROOT / "catalog" / "products.json"
LANDING = ROOT / "landing"
REPORT = ROOT / "runtime" / "joias" / "importacao.json"
SOCIAL = ROOT / "runtime" / "joias" / "calendario-correntes.json"
# Código do SKU por tipo de peça (VRT-JWL-<código>-0001).
PRODUCT_CODE = {"chain": "CHN", "necklace": "NCK", "bracelet": "BRC", "anklet": "ANK"}
RIGHTS = (
    "O fundador declarou em 19/10/2026 que o fornecedor autoriza o uso das fotos "
    "para revenda; guardar a autorização por escrito."
)


def _fetch(url: str) -> bytes:
    if not url.startswith(("http://", "https://")):
        return Path(url).read_bytes()
    import requests

    response = requests.get(
        url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (Veratus catálogo)"}
    )
    response.raise_for_status()
    return response.content


def from_urls(urls: list[str], limit: int) -> list[SupplierItem]:
    items: dict[str, SupplierItem] = {}
    pending = list(urls)
    seen: set[str] = set()
    while pending and len(items) < limit:
        url = pending.pop(0)
        if url in seen:
            continue
        seen.add(url)
        found, links = extract_products(_fetch(url).decode("utf-8", "replace"), url)
        for item in found:
            items.setdefault(item.url or item.name, item)
        pending.extend(link for link in links if link not in seen)
    return list(items.values())[:limit]


def from_sheet(path: Path) -> list[SupplierItem]:
    with path.open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    items = []
    for row in rows:
        price = Decimal(
            str(row["preco"]).replace("R$", "").replace(".", "").replace(",", ".")
        )
        images = [
            str((path.parent / part).resolve()) if not part.startswith("http") else part
            for part in (row.get("imagens") or "").split("|")
            if part.strip()
        ]
        items.append(
            SupplierItem(
                name=row["nome"].strip(),
                price=price,
                url=(row.get("url") or "").strip(),
                sku=(row.get("sku") or "").strip(),
                description=(row.get("descricao") or "").strip(),
                images=images,
            )
        )
    return items


def _store():
    from veratus_agents.config import AgentSettings
    from veratus_agents.product_master import make_product_repository

    settings = AgentSettings.from_env()
    return make_product_repository(settings.operations_db, settings.database_url)


def _master(store, item: SupplierItem, draft_public: dict, private: dict) -> str:
    """Cria (ou reencontra) a peça no Product Master; devolve o SKU."""
    from veratus_agents.product_master import EvidenceRef, ProductCreate

    key = hashlib.sha256((item.url or item.name).encode()).hexdigest()[:24]
    created = store.create(
        ProductCreate(
            name=draft_public["name"],
            category="jewelry_accessories",
            subcategory=draft_public["subcategory"],
            category_code="JWL",
            product_code=PRODUCT_CODE.get(draft_public["subcategory"], "CHN"),
            supplier_ref=private["supplier_ref"][:100],
            availability="sob consulta",
            inventory_mode="ON_DEMAND",
            channel_stock_cap=0,
            images=draft_public["images"][:12],
            cost_brl=Decimal(private["cost_brl"]),
            price_brl=Decimal(private["price_brl"]),
            evidence=[
                EvidenceRef(
                    field="fornecedor", reference=(item.url or item.name)[:300]
                ),
                EvidenceRef(field="fotos", reference=RIGHTS),
            ],
        ),
        actor="product-intake",
        event_id=f"joia_{key}",
    )
    return created["sku"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--planilha", type=Path)
    parser.add_argument("--limite", type=int, default=60)
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args(argv)
    rule = PriceRule.parse(os.getenv(RULE_ENV))
    items = (
        from_sheet(args.planilha) if args.planilha else from_urls(args.url, args.limite)
    )
    if not items:
        print("nenhuma peça encontrada")
        return 1

    store = _store() if args.aplicar else None
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {product["id"]: index for index, product in enumerate(catalog)}
    report, ready, imported = [], 0, []
    for position, item in enumerate(items, start=1):
        draft = build_draft(item, rule, _fetch, sku=f"VRT-JWL-NEW-P{position:03d}")
        if args.aplicar:
            sku = _master(store, item, draft.public, draft.private)
            draft = build_draft(item, rule, _fetch, sku=sku)
            for relative, data in draft.images.items():
                target = LANDING / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            if draft.public["id"] in by_id:
                catalog[by_id[draft.public["id"]]] = draft.public
            else:
                by_id[draft.public["id"]] = len(catalog)
                catalog.append(draft.public)
            imported.append(draft.public["id"])
        report.append(
            {
                "sku": draft.public["sku"],
                "nome": draft.public["name"],
                "preco": draft.public["sale_price"],
                "fotos": len(draft.images),
                "pendencias": draft.issues,
                # Só no relatório privado (runtime/, fora do Git):
                "custo": draft.private["cost_brl"],
                "origem": draft.private["supplier_url"],
            }
        )
        ready += not draft.issues

    print(
        json.dumps(
            [
                {k: v for k, v in line.items() if k not in ("custo", "origem")}
                for line in report
            ],
            ensure_ascii=False,
            indent=1,
        )
    )
    print(f"{len(report)} peças, {ready} sem pendência")
    if args.aplicar:
        CATALOG.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        from scripts import export_public_catalog

        export_public_catalog.main()
        # Um post por peça, às 19h, a partir de amanhã: tudo vai à sua aprovação.
        start = datetime.now(ZoneInfo("America/Sao_Paulo")).date() + timedelta(days=1)
        posts = [
            social_draft(product, f"{start + timedelta(days=index)}T19:00:00-03:00")
            for index, product in enumerate(
                product
                for product in catalog
                if product["id"] in imported
                and product.get("site_visibility") == "PREVIEW"
            )
        ]
        SOCIAL.write_text(
            json.dumps({"itens": posts}, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(
            f"{len(posts)} posts em {SOCIAL.relative_to(ROOT)} (aprovar com o agendador)"
        )
        print(f"relatório privado em {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
