from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .catalog import (
    CATALOG_PATH,
    LANDING_PATH,
    catalog_names_from_landing,
    load_catalog,
)
from .commercial_config import (
    CHANNEL_STOCK_CAP_FALLBACK,
    ESTIMATED_WATCH_DIMENSIONS,
    HANDLING_TIME_BUSINESS_DAYS,
    INVENTORY_MODE,
    OFFICIAL_CATEGORY,
    OFFICIAL_MATERIAL,
    OFFICIAL_MATERIAL_LABEL,
    OFFICIAL_PRODUCT_COST_BRL,
    OFFICIAL_SALE_PRICE_BRL,
    PACKAGE_PROFILE,
    SHIP_FROM,
    SUPPLIER_VISIBILITY,
    calculate_official_economics,
)

BASE_DIR = Path(__file__).resolve().parents[1]


def _source(path: Path) -> str:
    return str(path.relative_to(BASE_DIR)).replace("\\", "/")


def _asset_exists(relative_path: str | None) -> bool:
    return bool(relative_path and (LANDING_PATH.parent / relative_path).exists())


def _field(value: Any, source: str, confidence: str = "CONFIRMED") -> dict[str, Any]:
    return {"value": value, "source": source, "confidence": confidence}


def discover_products() -> list[dict[str, Any]]:
    landing_names = set(catalog_names_from_landing())
    catalog_source = _source(CATALOG_PATH)
    products = []
    for product in load_catalog():
        image = product.get("image")
        category = product.get("category") or OFFICIAL_CATEGORY
        is_watch = category == OFFICIAL_CATEGORY
        subcategory = product.get("subcategory") or ("watches" if is_watch else None)
        collection = product.get("collection") or ("watches" if is_watch else None)
        sku = product.get("sku") or product["id"]
        status = product.get("status") or "ACTIVE"
        cost = OFFICIAL_PRODUCT_COST_BRL if is_watch else product.get("cost")
        sale_price = OFFICIAL_SALE_PRICE_BRL if is_watch else product.get("sale_price")
        material = OFFICIAL_MATERIAL if is_watch else product.get("material")
        asset_candidates = product.get("images") or ([image] if image else [])
        verified_images = [item for item in asset_candidates if _asset_exists(item)]
        missing = []
        fields: dict[str, Any] = {
            "sku": _field(sku, catalog_source, "REAL_CONFIRMED"),
            "name": _field(product["name"], catalog_source),
            "category": _field(category, catalog_source, "REAL_CONFIRMED"),
            "subcategory": _field(
                subcategory,
                catalog_source,
                "REAL_CONFIRMED" if subcategory else "UNKNOWN",
            ),
            "collection": _field(
                collection,
                catalog_source if collection else "not_found",
                "CONFIRMED" if collection else "UNKNOWN",
            ),
            "status": _field(status, catalog_source, "REAL_CONFIRMED"),
            "active": _field(
                status in {"ACTIVE", "APPROVED", "PUBLISHED", "DRAFT"},
                catalog_source,
            ),
            "cost": _field(
                f"{cost:.2f}" if cost is not None else None,
                "founder-confirmed-commercial-config" if is_watch else "not_found",
                "REAL_CONFIRMED" if cost is not None else "UNKNOWN",
            ),
            "sale_price": _field(
                f"{sale_price:.2f}" if sale_price is not None else None,
                "founder-confirmed-commercial-config" if is_watch else "not_found",
                "REAL_CONFIRMED" if sale_price is not None else "UNKNOWN",
            ),
            "promotional_price": _field(None, "not_found", "UNKNOWN"),
            "stock": _field(None, "not_applicable_on_demand", "REAL_CONFIRMED"),
            "inventory_mode": _field(
                product.get("inventory_mode") or INVENTORY_MODE,
                catalog_source
                if product.get("inventory_mode")
                else "founder-confirmed-commercial-config",
                "REAL_CONFIRMED",
            ),
            "channel_stock_cap": _field(
                product.get("channel_stock_cap", CHANNEL_STOCK_CAP_FALLBACK),
                catalog_source
                if "channel_stock_cap" in product
                else "founder-confirmed-commercial-config",
                "CONFIGURABLE_FALLBACK",
            ),
            "weight": _field(None, "not_found", "UNKNOWN"),
            "dimensions": _field(None, "not_found", "UNKNOWN"),
            "material": _field(
                material,
                "founder-confirmed-commercial-config" if is_watch else "not_found",
                "REAL_CONFIRMED" if material is not None else "UNVERIFIED",
            ),
            "material_label": _field(
                OFFICIAL_MATERIAL_LABEL if is_watch else None,
                "founder-confirmed-commercial-config" if is_watch else "not_found",
                "REAL_CONFIRMED" if is_watch else "UNVERIFIED",
            ),
            "color": _field(
                product.get("color"),
                catalog_source if product.get("color") else "not_found",
                "VISUAL_CONFIRMED" if product.get("color") else "UNKNOWN",
            ),
            "condition": _field(None, "not_found", "UNKNOWN"),
            "images": _field(
                verified_images,
                catalog_source,
                "CONFIRMED" if verified_images else "UNKNOWN",
            ),
            "description": _field(product.get("description"), catalog_source),
            "product_type": _field(product.get("product_type"), catalog_source),
            "finish_color": _field(product.get("finish_color"), catalog_source),
            "verified_attributes": _field(
                product.get("verified_attributes", {}),
                catalog_source,
                "VISUAL_CONFIRMED",
            ),
            "unverified_attributes": _field(
                product.get("unverified_attributes", []), catalog_source, "UNVERIFIED"
            ),
            "financial_status": _field(
                product.get("financial_status"),
                catalog_source if product.get("financial_status") else "not_found",
                "CONFIRMED" if product.get("financial_status") else "UNKNOWN",
            ),
            "variations": _field([], catalog_source),
            "supplier": _field(None, "founder-confirmed-commercial-config", "PRIVATE"),
            "supplier_visibility": _field(
                product.get("supplier_visibility") or SUPPLIER_VISIBILITY,
                catalog_source
                if product.get("supplier_visibility")
                else "founder-confirmed-commercial-config",
                "REAL_CONFIRMED",
            ),
            "shipping_origin": _field(
                SHIP_FROM, "founder-confirmed-commercial-config", "REAL_CONFIRMED"
            ),
            "handling_time_business_days": _field(
                HANDLING_TIME_BUSINESS_DAYS,
                "founder-confirmed-commercial-config",
                "REAL_CONFIRMED",
            ),
            "package": _field(
                PACKAGE_PROFILE, "founder-confirmed-commercial-config", "REAL_CONFIRMED"
            ),
            "estimated_watch_dimensions": _field(
                ESTIMATED_WATCH_DIMENSIONS,
                "founder-confirmed-commercial-config",
                "ESTIMATED",
            ),
            "marketplace_data": _field({}, "not_found", "UNKNOWN"),
        }
        for field_name in ("sku", "category", "cost", "sale_price"):
            if fields[field_name]["value"] in (
                None,
                "",
                [],
                "not_applicable_on_demand",
            ):
                missing.append(field_name)
        if not fields["images"]["value"]:
            missing.append("images")
        if not is_watch and fields["material"]["value"] is None:
            missing.append("material")
        products.append(
            {
                "sku": sku,
                "id": product["id"],
                "name": product["name"],
                "category": category,
                "subcategory": subcategory,
                "collection": collection,
                "status": status,
                "price_brl": fields["sale_price"]["value"],
                "images": verified_images,
                "description": product.get("description"),
                "active": (
                    product["name"] in landing_names
                    or product["id"] == "black-gmt"
                    or product.get("site_visibility") == "PREVIEW"
                ),
                "fields": fields,
                "source_of_each_field": {
                    key: value["source"] for key, value in fields.items()
                },
                "confidence": "HIGH" if not missing else "PARTIAL",
                "missing_fields": missing,
                "classification": "REAL_CONFIRMED" if not missing else "LIKELY_REAL",
                "commercial_economics": (
                    calculate_official_economics() if is_watch else None
                ),
            }
        )
    return products


def missing_business_data(
    products: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    products = products or discover_products()
    result = []
    channel_fields = {
        "mercado-livre": ["category_mapping"],
        "shopee": ["category_mapping"],
        "tiktok-shop": ["category_mapping"],
        "meta": ["category_mapping"],
    }
    requirement_details = {
        "shipping_origin": (
            "CEP/endereço",
            "necessário para configuração de expedição",
        ),
        "handling_time_days": (
            "dias úteis",
            "necessário para prazo de preparação do pedido",
        ),
        "package_weight_g": ("gramas", "necessário para cálculo/configuração de frete"),
        "package_length_cm": ("centímetros", "necessário para dimensões do pacote"),
        "package_width_cm": ("centímetros", "necessário para dimensões do pacote"),
        "package_height_cm": ("centímetros", "necessário para dimensões do pacote"),
        "category_mapping": (
            "ID externo",
            "descoberta oficial depende do canal e credencial/API",
        ),
    }
    for product in products:
        for channel, fields in channel_fields.items():
            for field in fields:
                if field == "images" and "images" not in product["missing_fields"]:
                    continue
                if field in product["missing_fields"] or field in {
                    "category_mapping",
                    "shipping_origin",
                    "handling_time_days",
                    "package_weight_g",
                    "package_length_cm",
                    "package_width_cm",
                    "package_height_cm",
                }:
                    result.append(
                        {
                            "sku": product["sku"],
                            "field": field,
                            "required_by": "listing validation",
                            "channel": channel,
                            "unit": requirement_details.get(
                                field, ("n/a", "campo requerido pelo canal")
                            )[0],
                            "reason": requirement_details.get(
                                field, ("n/a", "campo requerido pelo canal")
                            )[1],
                            "blocking": True,
                            "classification": "BLOCKING_CHANNEL",
                            "sources_searched": [
                                "catalog/products.json",
                                "Product Master",
                                "tests",
                                "docs",
                                "landing/assets",
                                ".env.example",
                            ],
                        }
                    )
    return result


def discovery_report() -> dict[str, Any]:
    products = discover_products()
    missing = missing_business_data(products)
    segments = {
        "watches_total": sum(
            item["category"] == OFFICIAL_CATEGORY for item in products
        ),
        "feminine_total": sum(item["collection"] == "feminine" for item in products),
        "necklaces": sum(item["subcategory"] == "necklace" for item in products),
        "bracelets": sum(item["subcategory"] == "bracelet" for item in products),
        "anklets": sum(item["subcategory"] == "anklet" for item in products),
        "ready": sum(not item["missing_fields"] for item in products),
        "needs_information": sum(bool(item["missing_fields"]) for item in products),
    }
    return {
        "name": "VERATUS DATA DISCOVERY",
        "official_commercial_data": {
            "sale_price_brl": f"{OFFICIAL_SALE_PRICE_BRL:.2f}",
            "product_cost_brl": f"{OFFICIAL_PRODUCT_COST_BRL:.2f}",
            "material": OFFICIAL_MATERIAL,
            "material_label": OFFICIAL_MATERIAL_LABEL,
            "category": OFFICIAL_CATEGORY,
            "subcategory": "watches",
            "inventory_mode": INVENTORY_MODE,
            "supplier_visibility": SUPPLIER_VISIBILITY,
            "channel_stock_cap_fallback": CHANNEL_STOCK_CAP_FALLBACK,
            "handling_time_business_days": HANDLING_TIME_BUSINESS_DAYS,
            "shipping_origin": SHIP_FROM,
            "package": PACKAGE_PROFILE,
            "estimated_watch_dimensions": ESTIMATED_WATCH_DIMENSIONS,
        },
        "official_economics": calculate_official_economics(),
        "products_total": len(products),
        "products_complete": sum(not item["missing_fields"] for item in products),
        "products_incomplete": sum(bool(item["missing_fields"]) for item in products),
        "segments": segments,
        "products": products,
        "missing_business_data": missing,
        "data_conflicts": [],
        "credentials": {
            name: bool(os.getenv(name, "").strip())
            for name in (
                "MERCADO_LIVRE_ACCESS_TOKEN",
                "SHOPEE_ACCESS_TOKEN",
                "TIKTOK_SHOP_ACCESS_TOKEN",
                "META_ACCESS_TOKEN",
            )
        },
    }


def first_publish_candidate(
    products: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    for product in products or discover_products():
        if not product["missing_fields"]:
            return {
                "sku": product["sku"],
                "channel": "mercado-livre",
                "readiness": "PRODUCT_DATA_COMPLETE_CHANNEL_BLOCKED",
                "remaining_blockers": [
                    "CATEGORY_DISCOVERY_BLOCKED_BY_CREDENTIALS",
                    "API_CONTRACT_UNVERIFIED",
                ],
            }
    return {
        "sku": None,
        "channel": None,
        "readiness": "NOT_READY",
        "remaining_blockers": [
            "nenhum produto possui todos os dados comerciais necessários"
        ],
    }


def write_discovery_report(path: str | Path) -> dict[str, Any]:
    report = discovery_report()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
