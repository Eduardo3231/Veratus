"""Feed do catálogo da Meta (Commerce Manager) a partir dos anúncios, para revisão.

A Meta não tem API pública de anúncio no Facebook Marketplace no Brasil; o
caminho oficial é o catálogo do Commerce Manager (Loja do Facebook e Instagram
Shopping) alimentado por um feed CSV. Este módulo monta esse feed a partir do
anúncio ``facebook`` de ``listing_content`` e dos campos públicos do catálogo:

- ``feed_rows()``: uma linha por produto, com ``ready`` e ``blockers``;
- ``feed_csv(rows)``: CSV UTF-8 com cabeçalho, só com as linhas prontas;
- ``catalog_report()``: quantas prontas, quantas bloqueadas e por quê.

Regras:

- preço só o público (``catalog.public_price``), no formato "289.90 BRL";
- disponibilidade só com estoque CONFIRMADO na ficha dos relógios; sem
  evidência de estoque, a linha fica ``AVAILABILITY_UNCONFIRMED``;
- link e foto só do site público; foto só de ``landing/assets`` e nunca da
  quarentena;
- descrição em texto puro, passando no gate (``hard_review_customer_reply``,
  sem canal: o catálogo leva ao site) e sem palavra proibida, fornecedor,
  custo, margem, dado de cliente ou segredo;
- linha com qualquer bloqueio não vai para o CSV.

Nada é enviado à Meta. ``python scripts/exportar_catalogo_meta.py --saida
catalogo.csv`` gera o arquivo.
"""

from __future__ import annotations

import csv
import io
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .catalog import BASE_DIR, load_catalog, public_price
from .listing_content import (
    CHANNEL_LABELS,
    DESCRIPTION_LIMITS,
    TITLE_LIMITS,
    TITLE_LIMITS_STATUS,
    blocker_text,
    listing_copy,
)
from .meta_webhooks import normalize_text
from .policy import hard_review_customer_reply

CHANNEL = "facebook"
FEED_COLUMNS = (
    "id",
    "title",
    "description",
    "availability",
    "condition",
    "price",
    "link",
    "image_link",
    "brand",
)
BRAND = "Veratus"
CONDITION = "new"
TITLE_LIMIT = TITLE_LIMITS[CHANNEL]
DESCRIPTION_LIMIT = DESCRIPTION_LIMITS[CHANNEL]
LANDING_DIR = BASE_DIR / "landing"
ASSETS_DIR = LANDING_DIR / "assets"
# Endereço público do site: PUBLIC_BASE_URL (render.yaml) ou, sem ela, o mesmo
# endereço que o agendador de posts usa para a mídia.
BASE_URL_ENV = "PUBLIC_BASE_URL"

# Bloqueios do anúncio que tiram o produto do feed sem montar texto.
_EXCLUDED = frozenset({"ON_HOLD", "NOT_PUBLIC"})

# Texto sem acento e minúsculo (normalize_text).
_FORBIDDEN = re.compile(
    r"\bouro\b|\bprata\b|\bbanh(?:o|ad[oa]s?)\b|\bfolhead|\bsemi[- ]?joia|"
    r"\bzirconi|\bhipoalerg|\bultim[oa]s?\b|\bso hoje\b|\bacaba (?:hoje|logo)\b|"
    r"\bcorr[ae]\b|\blimitad|\bimperd|\besgot|\bpoucas (?:unidades|pecas)\b|"
    r"\brelampago\b|\bnao perca\b|\bagora ou nunca\b|\bpor tempo\b|\bqueima\b"
)
_PRIVATE = re.compile(
    r"\bfornecedor|\bsupplier|\bcusto|\bcost\b|\bmargem|\bmargin|\bcpa\b|"
    r"\btoken\b|\bsecret|\bsenha|\bcpf\b|\bcnpj\b|[^\s@]+@[^\s@]+|"
    r"\(?\b\d{2}\)?\s*9?\d{4}[\s.-]?\d{4}\b|\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"
)
# Campos do produto cujo valor nunca pode aparecer no feed.
_PRIVATE_KEYS = re.compile(r"supplier|fornecedor|cost|custo|margin|margem|cpa")

BLOCKER_TEXTS = {
    "AVAILABILITY_UNCONFIRMED": (
        "Sem estoque confirmado: o feed não diz 'in stock' sem evidência "
        "(relógio: estoque CONFIRMADO na ficha do modelo)."
    ),
    "LINK_MISSING": (
        "Sem endereço público do site (PUBLIC_BASE_URL com https): o item fica "
        "sem link."
    ),
    "IMAGE_NOT_FOUND": "A foto liberada não existe em landing/assets.",
    "TEXT_MISSING": "Título ou descrição vazio.",
    "TITLE_TOO_LONG": f"Título acima de {TITLE_LIMIT} caracteres.",
    "DESCRIPTION_TOO_LONG": f"Descrição acima de {DESCRIPTION_LIMIT} caracteres.",
    "FORBIDDEN_WORDS": (
        "Palavra proibida no texto (material de joia, urgência ou escassez)."
    ),
    "PRIVATE_DATA": (
        "Dado privado no texto (fornecedor, custo, margem, contato ou segredo)."
    ),
}
NOTES = (
    (
        f"Limites de título ({TITLE_LIMIT}) e descrição ({DESCRIPTION_LIMIT}) da "
        "especificação do feed da Meta: SUPOSIÇÃO, conferir no Commerce Manager."
    ),
    "Joias não têm página própria no site: o link leva à seção de joias (#joias).",
    (
        "As fotos do site são WebP e a descrição tem parágrafos (quebra de linha "
        "dentro de aspas no CSV): conferir na primeira carga do Commerce Manager."
    ),
)


def feed_blocker_text(code: str) -> str:
    return BLOCKER_TEXTS.get(code) or blocker_text(code)


def site_base_url() -> str | None:
    """Endereço público do site, sem barra no fim; ``None`` se não for https."""
    from .social_scheduler import PUBLIC_BASE

    value = (os.getenv(BASE_URL_ENV) or PUBLIC_BASE or "").strip().rstrip("/")
    return value if value.startswith("https://") else None


def _quarantined() -> set[str]:
    from integrations.meta_content_publisher import quarantined_media_paths

    return quarantined_media_paths()


def _product_link(product_id: str, collection: str, base: str) -> str:
    # O site abre o relógio por ?relogio=<id> (landing/site.js).
    if collection == "watches":
        return f"{base}/?relogio={quote(product_id)}"
    return f"{base}/#joias"


def _image_link(photos: list[str], base: str) -> str | None:
    """Primeira foto liberada que existe em landing/assets e não está na quarentena."""
    quarantined = _quarantined()
    assets = ASSETS_DIR.resolve()
    for photo in photos:
        clean = str(photo).lstrip("/")
        path = (LANDING_DIR / clean).resolve()
        if "/" + clean in quarantined or not path.is_relative_to(assets):
            continue
        if path.is_file():
            return f"{base}/{quote(clean)}"
    return None


def _availability(product: dict[str, Any], collection: str) -> str | None:
    """'in stock'/'out of stock' só com estoque CONFIRMADO na ficha."""
    if collection != "watches":
        return None
    from .watch_sheet import default_sheet, model_id

    sheet = default_sheet()
    stock = sheet.confirmed("estoque", model_id(product)) if sheet else None
    if stock is None:
        return None
    return "in stock" if stock > 0 else "out of stock"


def _plain_text(text: str) -> str:
    """Sem HTML nem espaço sobrando; parágrafos separados por linha em branco."""
    text = re.sub(r"<[^>]*>", " ", text or "")
    paragraphs = (" ".join(part.split()) for part in text.split("\n\n"))
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)


def _private_values(product: dict[str, Any]) -> list[str]:
    values = []
    for key, value in product.items():
        if _PRIVATE_KEYS.search(key.casefold()) and value not in (None, ""):
            text = str(value).strip().casefold()
            if len(text) >= 3 and text not in ("private", "privado"):
                values.append(text)
    return values


def _text_codes(
    product: dict[str, Any], collection: str, title: str, description: str
) -> tuple[list[str], list[str]]:
    codes, issues = [], []
    if not title or not description:
        codes.append("TEXT_MISSING")
    if len(title) > TITLE_LIMIT:
        codes.append("TITLE_TOO_LONG")
    if len(description) > DESCRIPTION_LIMIT:
        codes.append("DESCRIPTION_TOO_LONG")
    texts = (("title", title), ("description", description))
    if any(_FORBIDDEN.search(normalize_text(text)) for _, text in texts):
        codes.append("FORBIDDEN_WORDS")
    private = _private_values(product)
    for _, text in texts:
        lowered = text.casefold()
        if _PRIVATE.search(normalize_text(text)) or any(v in lowered for v in private):
            codes.append("PRIVATE_DATA")
            break
    for field, text in texts:
        issues += [
            f"{field}: {issue}"
            for issue in hard_review_customer_reply(text, product_collection=collection)
        ]
    if issues:
        codes.append("GATE_ISSUES")
    return codes, issues


def _row(product: dict[str, Any], base: str | None) -> dict[str, Any]:
    listing = listing_copy(product, CHANNEL)
    codes = [blocker["code"] for blocker in listing["blockers"]]
    collection = str(listing.get("collection") or "")
    row: dict[str, Any] = dict.fromkeys(FEED_COLUMNS, "")
    row.update(id=str(product.get("id") or ""), name=listing.get("name"))
    row["collection"] = collection
    issues = list(listing["issues"])
    if _EXCLUDED & set(codes):
        # Fora do site ou do catálogo público: nenhum texto vai para o feed.
        return _finish(row, codes, issues)

    title = " ".join(listing["title"].split())
    description = _plain_text(listing["description"])
    availability = _availability(product, collection)
    price = public_price(product)
    row.update(
        title=title,
        description=description,
        availability=availability or "",
        condition=CONDITION,
        price=f"{price} BRL" if price else "",
        brand=BRAND,
    )
    if availability is None:
        codes.append("AVAILABILITY_UNCONFIRMED")
    if base:
        row["link"] = _product_link(row["id"], collection, base)
        image = _image_link(listing["photos"], base)
        row["image_link"] = image or ""
        if listing["photos"] and not image:
            codes.append("IMAGE_NOT_FOUND")
    else:
        codes.append("LINK_MISSING")
    text_codes, text_issues = _text_codes(product, collection, title, description)
    return _finish(row, codes + text_codes, issues + text_issues)


def _finish(row: dict[str, Any], codes: list[str], issues: list[str]) -> dict[str, Any]:
    codes = list(dict.fromkeys(codes))
    row["blockers"] = [
        {"code": code, "text": feed_blocker_text(code)} for code in codes
    ]
    row["issues"] = list(dict.fromkeys(issues))
    row["ready"] = not codes
    return row


def feed_rows(products: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Uma linha do feed por produto do catálogo, com ``ready`` e ``blockers``."""
    products = load_catalog() if products is None else products
    base = site_base_url()
    return [_row(product, base) for product in products]


def feed_csv(rows: list[dict[str, Any]]) -> str:
    """CSV do Commerce Manager (UTF-8, com cabeçalho), só com as linhas prontas."""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=FEED_COLUMNS, extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        if row.get("ready") is True and not row.get("blockers"):
            writer.writerow({column: row.get(column) or "" for column in FEED_COLUMNS})
    return buffer.getvalue()


def catalog_report(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Quantas linhas prontas, quantas bloqueadas e por quê (só leitura)."""
    rows = feed_rows() if rows is None else rows
    reasons: dict[str, dict[str, Any]] = {}
    for row in rows:
        for blocker in row["blockers"]:
            reason = reasons.setdefault(
                blocker["code"],
                {"code": blocker["code"], "count": 0, "text": blocker["text"]},
            )
            reason["count"] += 1
    ready = sum(1 for row in rows if row["ready"])
    return {
        "schema": "veratus.meta_catalog_feed/v1",
        "mode": "REVIEW",
        "external_write": False,
        "channel": CHANNEL,
        "label": CHANNEL_LABELS[CHANNEL],
        "source": (
            "listing_content (canal facebook), campos públicos do catálogo e "
            "fatos CONFIRMED"
        ),
        "columns": list(FEED_COLUMNS),
        "limits": {
            "title": TITLE_LIMIT,
            "description": DESCRIPTION_LIMIT,
            "status": TITLE_LIMITS_STATUS,
        },
        "site": site_base_url(),
        "total": len(rows),
        "ready": ready,
        "blocked": len(rows) - ready,
        "exported": ready,
        # Mais frequente primeiro; no empate, a ordem em que apareceu.
        "reasons": sorted(reasons.values(), key=lambda reason: -reason["count"]),
        "products": [
            {
                "id": row["id"],
                "name": row.get("name"),
                "ready": row["ready"],
                "blockers": [blocker["code"] for blocker in row["blockers"]],
                "issues": row["issues"],
            }
            for row in rows
        ],
        "notes": list(NOTES),
    }


def write_atomic(path: Path, text: str) -> None:
    """Escreve por um arquivo parcial: um feed pela metade nunca fica no lugar."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".parcial")
    partial.write_text(text, encoding="utf-8", newline="")
    partial.replace(path)
