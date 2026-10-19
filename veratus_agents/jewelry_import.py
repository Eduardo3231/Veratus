"""Correntes e joias de revenda: do anúncio do fornecedor ao rascunho Veratus.

O fornecedor é privado. Nome, endereço, SKU dele, custo e a regra de preço ficam
só no Product Master (banco) e na variável ``JEWELRY_PRICE_RULE``; o repositório
é público. O que vai para o Git, o site e os marketplaces: nome Veratus, medidas,
tom (só "tom prateado" ou "tom dourado"), fotos renomeadas e sem metadados e o
preço público.

Nada do texto do fornecedor chega ao cliente: o nome e a descrição Veratus são
montados só a partir das medidas e do tipo de elo. "Prata 925", "banho" e afins
ficam de fora (``policy.hard_review_customer_reply``).
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import math
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

RULE_ENV = "JEWELRY_PRICE_RULE"
IMAGE_SIZE = (1122, 1402)  # o retrato 4:5 dos cards de joia do site
MAX_IMAGES = 4

_LD_JSON = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_META = re.compile(
    r"<meta\s+[^>]*(?:property|name)=[\"']([^\"']+)[\"'][^>]*content=[\"']([^\"']*)[\"']",
    re.IGNORECASE,
)
_LENGTH = re.compile(r"(\d{2,3})\s*(?:cm|centimetros|centímetros)\b", re.IGNORECASE)
_WIDTH = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*mm\b", re.IGNORECASE)
_WEIGHT = re.compile(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:g|gr|gramas)\b", re.IGNORECASE)
# Tipos de elo, do mais específico ao mais genérico.
LINKS: tuple[tuple[str, str], ...] = (
    ("cordao baiano", "cordão baiano"),
    ("baiano", "cordão baiano"),
    ("rabo de rato", "rabo de rato"),
    ("elo portugues", "elo português"),
    ("portugues", "elo português"),
    # "Cartier" é marca registrada: o elo só ganha nome pelos números (3x1, 1x1).
    ("groumet", "elo grumet"),
    ("grumet", "elo grumet"),
    ("veneziana", "elo veneziana"),
    ("singapura", "elo singapura"),
    ("piastrine", "elo piastrine"),
    ("figaro", "elo fígaro"),
    ("3x1", "elo 3x1"),
    ("1x1", "elo 1x1"),
    ("bolinha", "elo bolinha"),
    ("torcida", "elo torcido"),
    ("corda", "elo corda"),
    ("malha", "malha"),
)
CLASPS = (
    ("lagosta", "fecho lagosta"),
    ("boia", "fecho boia"),
    ("gaveta", "fecho gaveta"),
)
SUBCATEGORIES = (
    ("pulseira", "bracelet"),
    ("tornozeleira", "anklet"),
    ("colar", "necklace"),
    ("pingente", "necklace"),
    ("corrente", "chain"),
    ("cordao", "chain"),
)
KIND = {
    "chain": "Corrente",
    "bracelet": "Pulseira",
    "anklet": "Tornozeleira",
    "necklace": "Colar",
}


@dataclass
class SupplierItem:
    """Um anúncio do fornecedor. Só vive em memória e no Product Master."""

    name: str
    price: Decimal
    url: str = ""
    sku: str = ""
    description: str = ""
    images: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PriceRule:
    """Até ``limit`` de custo, ``low``× o custo; acima, ``high``× (sem inverter)."""

    limit: Decimal
    low: Decimal
    high: Decimal

    @classmethod
    def parse(cls, text: str | None) -> PriceRule:
        parts = [part.strip() for part in (text or "").split(":")]
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                f"{RULE_ENV} precisa ser 'limite:multiplicador_baixo:multiplicador_alto'"
            )
        limit, low, high = (Decimal(part.replace(",", ".")) for part in parts)
        if limit <= 0 or low < 1 or high < 1:
            raise ValueError(f"{RULE_ENV} com valores inválidos")
        return cls(limit, low, high)

    def price(self, cost: Decimal) -> Decimal:
        """Preço público terminado em ,90; uma peça mais cara nunca sai mais barata."""
        if cost <= 0:
            raise ValueError("custo precisa ser positivo")
        raw = (
            cost * self.low
            if cost <= self.limit
            else max(cost * self.high, self.limit * self.low)
        )
        return Decimal(math.ceil(raw / 10) * 10) - Decimal("0.10")


# -- leitura da página ----------------------------------------------------------------
def _money(value: Any) -> Decimal | None:
    text = str(value or "").strip().replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number > 0 else None


def _images(value: Any, base: str) -> list[str]:
    values = value if isinstance(value, list) else [value]
    urls = []
    for item in values:
        if isinstance(item, dict):
            item = item.get("url") or item.get("contentUrl")
        if isinstance(item, str) and item.strip():
            urls.append(urljoin(base, item.strip()))
    return list(dict.fromkeys(urls))


def _offer_price(offers: Any) -> Decimal | None:
    for offer in offers if isinstance(offers, list) else [offers]:
        if not isinstance(offer, dict):
            continue
        for key in ("price", "lowPrice"):
            price = _money(offer.get(key))
            if price is not None:
                return price
        spec = offer.get("priceSpecification")
        if isinstance(spec, dict) and _money(spec.get("price")) is not None:
            return _money(spec.get("price"))
    return None


def _walk(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            if key in node:
                yield from _walk(node[key])


def _is(node: dict[str, Any], kind: str) -> bool:
    types = node.get("@type")
    return kind in (types if isinstance(types, list) else [types])


def extract_products(page: str, page_url: str) -> tuple[list[SupplierItem], list[str]]:
    """Produtos (schema.org) e links de produto de uma página de categoria ou produto."""
    items: list[SupplierItem] = []
    links: list[str] = []
    for block in _LD_JSON.findall(page):
        try:
            data = json.loads(html.unescape(block.strip()))
        except ValueError:
            continue
        for node in _walk(data):
            if _is(node, "Product"):
                price = _offer_price(node.get("offers"))
                if price is None or not node.get("name"):
                    continue
                items.append(
                    SupplierItem(
                        name=str(node["name"]).strip(),
                        price=price,
                        url=urljoin(page_url, str(node.get("url") or page_url)),
                        sku=str(node.get("sku") or "").strip(),
                        description=str(node.get("description") or "").strip(),
                        images=_images(node.get("image"), page_url),
                    )
                )
            elif _is(node, "ListItem") and isinstance(node.get("url"), str):
                links.append(urljoin(page_url, node["url"]))
    if not items:
        meta = {key.lower(): value for key, value in _META.findall(page)}
        price = _money(meta.get("product:price:amount") or meta.get("og:price:amount"))
        if meta.get("og:title") and price is not None:
            items.append(
                SupplierItem(
                    name=html.unescape(meta["og:title"]),
                    price=price,
                    url=page_url,
                    description=html.unescape(meta.get("og:description", "")),
                    images=_images(meta.get("og:image"), page_url),
                )
            )
    unique = {item.url or item.name: item for item in items}
    return list(unique.values()), list(dict.fromkeys(links))


# -- o modelo Veratus para joias e correntes --------------------------------------------
def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def attributes(item: SupplierItem) -> dict[str, Any]:
    """Medidas, elo, fecho e tom tirados do anúncio; o material fica de fora."""
    text = f"{item.name} {item.description}"
    plain = _plain(text)
    found: dict[str, Any] = {
        "subcategory": next(
            (sub for key, sub in SUBCATEGORIES if key in plain), "chain"
        )
    }
    if match := _LENGTH.search(text):
        found["comprimento_cm"] = int(match.group(1))
    if match := _WIDTH.search(text):
        found["espessura_mm"] = match.group(1).replace(".", ",")
    if match := _WEIGHT.search(text):
        found["peso_g"] = match.group(1).replace(".", ",")
    found["elo"] = next((name for key, name in LINKS if key in plain), None)
    found["fecho"] = next((name for key, name in CLASPS if key in plain), None)
    gold = "dourad" in plain or "ouro" in plain
    silver = "prat" in plain or "mista" in plain or not gold
    if gold and silver:
        found["finish_color"], found["tom"] = (
            "silver_tone",
            "tom prateado com detalhes em tom dourado",
        )
    elif gold:
        found["finish_color"], found["tom"] = "gold_tone", "tom dourado"
    else:
        found["finish_color"], found["tom"] = "silver_tone", "tom prateado"
    return found


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _plain(text)).strip("-")[:60]


def veratus_copy(attrs: dict[str, Any], ref: str) -> dict[str, Any]:
    """Nome, textos e SEO no padrão Veratus, só com o que a foto mostra.

    Medidas (comprimento, espessura, peso) ficam em ``listing_attributes`` até o
    fundador confirmá-las: o filtro de afirmações barra medida não validada.
    """
    kind = KIND[attrs["subcategory"]]
    link = attrs.get("elo")
    name = f"{kind} {link}" if link else kind
    name = f"{name}, {attrs['tom']}, ref. {ref}"
    details = ", ".join(filter(None, [link, attrs["tom"], attrs.get("fecho")]))
    short = f"{kind} {details}.".replace(f"{kind} elo", f"{kind} de elo")
    if not link:
        short = short.replace(f"{kind} {attrs['tom']}", f"{kind} em {attrs['tom']}")
    description = (
        f"{short} O acabamento é descrito pela cor, e as medidas da peça são "
        "informadas na conversa."
    )
    return {
        "name": name,
        "editorial_name": name,
        "product_type": f"{kind} em {attrs['tom']}",
        "eyebrow": f"Veratus Joias / {kind}",
        "short_description": short,
        "description": description,
        "visual_description": short,
        "alt": f"{kind} {details}".strip(),
        "seo_title": f"{name} | Veratus Joias",
        "seo_description": f"{kind} {details}. Atendimento pelo WhatsApp da Veratus.",
        "styles": list(filter(None, [kind.lower(), _slug(link) if link else None])),
        "color": attrs["tom"],
    }


def gate_issues(copy: dict[str, Any]) -> list[str]:
    """O mesmo filtro das respostas ao cliente, na coleção de joias."""
    from .policy import hard_review_customer_reply

    text = " ".join(
        str(copy[key])
        for key in (
            "name",
            "short_description",
            "description",
            "alt",
            "seo_description",
        )
    )
    return list(hard_review_customer_reply(text, [], product_collection="feminine"))


def normalize_image(content: bytes) -> tuple[bytes, str]:
    """WebP 4:5 sem metadados, a foto inteira centrada (nada é recortado ou apagado)."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(content)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail(IMAGE_SIZE)
    corner = image.getpixel((0, 0))
    canvas = Image.new("RGB", IMAGE_SIZE, corner)
    canvas.paste(
        image,
        ((IMAGE_SIZE[0] - image.width) // 2, (IMAGE_SIZE[1] - image.height) // 2),
    )
    buffer = io.BytesIO()
    canvas.save(buffer, "WEBP", quality=86, method=6)
    data = buffer.getvalue()
    return data, hashlib.sha256(data).hexdigest()


@dataclass
class Draft:
    """Uma peça pronta para o catálogo (público) e o Product Master (privado)."""

    public: dict[str, Any]
    private: dict[str, Any]
    images: dict[str, bytes]
    issues: list[str]


def build_draft(
    item: SupplierItem,
    rule: PriceRule,
    fetch: Callable[[str], bytes],
    *,
    sku: str,
    asset_dir: str = "assets/jewelry",
) -> Draft:
    """Rascunho de uma peça: preço pela regra, texto Veratus e fotos normalizadas."""
    attrs = attributes(item)
    ref = sku.rsplit("-", 1)[-1]
    copy = veratus_copy(attrs, ref)
    issues = gate_issues(copy)
    slug = _slug(copy["name"].split(", ref.")[0]) + "-" + ref.lower()
    images: dict[str, bytes] = {}
    for index, url in enumerate(item.images[:MAX_IMAGES], start=1):
        try:
            data, _ = normalize_image(fetch(url))
        except Exception:  # noqa: BLE001 - foto que não abre fica de fora
            issues.append(f"foto {index} não abriu")
            continue
        images[f"{asset_dir}/{slug}-{index}.webp"] = data
    if not images:
        issues.append("sem foto")
    paths = list(images)
    price = rule.price(item.price)
    measures = {
        key: attrs[key]
        for key in ("comprimento_cm", "espessura_mm", "peso_g", "elo", "fecho")
        if attrs.get(key)
    }
    public = {
        "collection": "feminine",
        "category": "jewelry_accessories",
        "subcategory": attrs["subcategory"],
        "status": "DRAFT",
        "site_visibility": "PREVIEW" if not issues else "HIDDEN",
        "material": None,
        "material_status": "UNVERIFIED",
        "cost": None,
        "sale_price": f"{price:.2f}",
        "price_source": "regra de preço do fundador sobre o custo (custo no Product Master)",
        "financial_status": "PRICED",
        "inventory_mode": "ON_DEMAND",
        "channel_stock_cap": 0,
        "supplier_visibility": "PRIVATE",
        "marketplace_status": "DRAFT",
        "verified_attributes": {
            "subcategory": attrs["subcategory"],
            "finish_color": attrs["finish_color"],
        },
        "listing_attributes": measures,
        "unverified_attributes": [
            "material",
            "medidas da ficha de origem",
            "estoque e prazo do fornecedor",
        ],
        "gallery_images": paths[1:],
        "creative_status": "SUPPLIER_MEDIA_AUTHORIZED",
        "quality_status": "NEEDS_REVIEW" if issues else "READY_FOR_REVIEW",
        "id": slug,
        "sku": sku,
        **copy,
        "finish_color": attrs["finish_color"],
        "image": paths[0] if paths else None,
        "images": paths,
        "primary_image": paths[0] if paths else None,
    }
    private = {
        "sku": sku,
        "cost_brl": f"{item.price:.2f}",
        "price_brl": f"{price:.2f}",
        "supplier_ref": item.sku or hashlib.sha256(item.url.encode()).hexdigest()[:16],
        "supplier_url": item.url,
        "supplier_name": item.name,
    }
    return Draft(public, private, images, issues)


def social_draft(public: dict[str, Any], when: str) -> dict[str, Any]:
    """Post da peça para o agendador (vai à aprovação do fundador, nunca direto)."""
    media = [path for path in public.get("images") or [] if path]
    return {
        "id": f"joia-{public['id']}",
        "formato": "CAROUSEL" if len(media) > 1 else "IMAGE",
        "canais": ["instagram", "facebook"],
        "data_hora": when,
        "midias": media,
        "legenda": (
            f"{public['short_description']}\n\n"
            "Pedido e atendimento pelo WhatsApp da Veratus, link na bio.\n\n"
            "#correntes #joias #veratus"
        ),
        "alt_text": public.get("alt", ""),
        "produtos": [public["id"]],
    }
