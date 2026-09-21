from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
CATALOG_PATH = BASE_DIR / "catalog" / "products.json"
LANDING_PATH = BASE_DIR / "landing" / "index.html"
PUBLIC_CATALOG_PATH = BASE_DIR / "landing" / "catalog.json"


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def load_catalog() -> list[dict[str, Any]]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise TypeError("catalog/products.json deve conter uma lista")
    ids = [item.get("id") for item in data if isinstance(item, dict)]
    names = [item.get("name") for item in data if isinstance(item, dict)]
    if len(ids) != len(data) or any(
        not isinstance(value, str) or not value for value in ids + names
    ):
        raise ValueError("Catálogo contém produto sem ID ou nome válido")
    if len(ids) != len(set(ids)) or len(names) != len(set(names)):
        raise ValueError("Catálogo contém ID ou nome duplicado")
    return data


def list_products(style: str | None = None) -> list[dict[str, Any]]:
    products = load_catalog()
    if not style:
        return products
    normalized = _normalize(style)
    return [
        product
        for product in products
        if normalized in {_normalize(item) for item in product.get("styles", [])}
    ]


def find_product(query: str) -> dict[str, Any] | None:
    normalized = _normalize(query)
    if not normalized:
        return None
    products = load_catalog()
    matches = []
    for product in products:
        if normalized in {_normalize(product["id"]), _normalize(product["name"])}:
            return product
    for product in products:
        haystack = " ".join(
            [
                product.get("name", ""),
                product.get("eyebrow", ""),
                product.get("description", ""),
                " ".join(product.get("styles", [])),
            ]
        )
        if normalized in _normalize(haystack):
            matches.append(product)
    return matches[0] if len(matches) == 1 else None


def public_catalog(
    products: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return the customer-safe projection consumed by the storefront.

    Black GMT remains in the Product Master and outside the public projection,
    preserving its existing operational rule. Feminine DRAFT items are exposed
    only as an editorial preview, without cost, price, material or supplier data.
    """

    safe_fields = (
        "id",
        "sku",
        "name",
        "editorial_name",
        "product_type",
        "category",
        "subcategory",
        "collection",
        "status",
        "site_visibility",
        "eyebrow",
        "short_description",
        "description",
        "image",
        "images",
        "primary_image",
        "alt",
        "styles",
        "finish_color",
        "color",
    )
    projection = []
    for product in products or load_catalog():
        if product["id"] == "black-gmt":
            continue
        collection = product.get("collection") or "watches"
        if collection == "feminine" and product.get("site_visibility") != "PREVIEW":
            continue
        item = {field: product.get(field) for field in safe_fields if field in product}
        item.setdefault("collection", collection)
        item.setdefault("category", "watches")
        item.setdefault("subcategory", "watch")
        item.setdefault("status", "ACTIVE")
        item.setdefault("alt", f"{product['name']} da coleção Veratus")
        projection.append(item)
    return projection


def catalog_names_from_landing() -> list[str]:
    if PUBLIC_CATALOG_PATH.exists():
        data = json.loads(PUBLIC_CATALOG_PATH.read_text(encoding="utf-8"))
        return [item["name"] for item in data]
    html = LANDING_PATH.read_text(encoding="utf-8")
    return re.findall(r'class="product-card"[^>]*data-name="([^"]+)"', html)


def validate_catalog_sync() -> tuple[bool, dict[str, list[str]]]:
    landing_names = catalog_names_from_landing()
    catalog_names = [item["name"] for item in load_catalog()]
    missing_in_json = sorted(set(landing_names) - set(catalog_names))
    expected_public = [item["name"] for item in public_catalog()]
    missing_in_landing = sorted(set(expected_public) - set(landing_names))
    # O Product Master pode conter itens internos ou pausados que não estão na vitrine.
    # O risco que bloqueamos é a landing anunciar um nome ausente no catálogo.
    return not missing_in_json, {
        "missing_in_json": missing_in_json,
        "missing_in_landing": missing_in_landing,
    }
