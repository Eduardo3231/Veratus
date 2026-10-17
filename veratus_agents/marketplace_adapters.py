from __future__ import annotations

from enum import StrEnum
from typing import Any

from .product_master import ProductStatus, qa_issues


class Marketplace(StrEnum):
    MERCADO_LIVRE = "mercado-livre"
    SHOPEE = "shopee"
    TIKTOK_SHOP = "tiktok-shop"
    META = "meta"
    AMAZON = "amazon"


class DistributionDraftError(ValueError):
    """Raised when a product cannot become a marketplace draft."""


CHANNEL_ATTRIBUTE_MAPPINGS: dict[Marketplace, dict[str, str]] = {
    Marketplace.MERCADO_LIVRE: {"color": "ML_COLOR", "material": "ML_MATERIAL"},
    Marketplace.SHOPEE: {"color": "SHOPEE_COLOR", "material": "SHOPEE_MATERIAL"},
    Marketplace.TIKTOK_SHOP: {"color": "TIKTOK_COLOR", "material": "TIKTOK_MATERIAL"},
    Marketplace.META: {"color": "META_COLOR", "material": "META_MATERIAL"},
    Marketplace.AMAZON: {"color": "AMAZON_COLOR", "material": "AMAZON_MATERIAL"},
}

CATEGORY_MAPPINGS: dict[Marketplace, dict[str, str]] = {
    Marketplace.MERCADO_LIVRE: {
        "Relógios": "PENDING_ML_WATCH_CATEGORY",
        "jewelry_accessories/necklace": "PENDING_ML_NECKLACE_CATEGORY",
        "jewelry_accessories/bracelet": "PENDING_ML_BRACELET_CATEGORY",
        "jewelry_accessories/anklet": "PENDING_ML_ANKLET_CATEGORY",
    },
    Marketplace.SHOPEE: {
        "Relógios": "PENDING_SHOPEE_WATCH_CATEGORY",
        "jewelry_accessories/necklace": "PENDING_SHOPEE_NECKLACE_CATEGORY",
        "jewelry_accessories/bracelet": "PENDING_SHOPEE_BRACELET_CATEGORY",
        "jewelry_accessories/anklet": "PENDING_SHOPEE_ANKLET_CATEGORY",
    },
    Marketplace.TIKTOK_SHOP: {
        "Relógios": "PENDING_TIKTOK_WATCH_CATEGORY",
        "jewelry_accessories/necklace": "PENDING_TIKTOK_NECKLACE_CATEGORY",
        "jewelry_accessories/bracelet": "PENDING_TIKTOK_BRACELET_CATEGORY",
        "jewelry_accessories/anklet": "PENDING_TIKTOK_ANKLET_CATEGORY",
    },
    Marketplace.META: {
        "Relógios": "PENDING_META_WATCH_CATEGORY",
        "jewelry_accessories/necklace": "PENDING_META_NECKLACE_CATEGORY",
        "jewelry_accessories/bracelet": "PENDING_META_BRACELET_CATEGORY",
        "jewelry_accessories/anklet": "PENDING_META_ANKLET_CATEGORY",
    },
    Marketplace.AMAZON: {
        "Relógios": "PENDING_AMAZON_WATCH_PRODUCT_TYPE",
        "jewelry_accessories/necklace": "PENDING_AMAZON_NECKLACE_PRODUCT_TYPE",
        "jewelry_accessories/bracelet": "PENDING_AMAZON_BRACELET_PRODUCT_TYPE",
        "jewelry_accessories/anklet": "PENDING_AMAZON_ANKLET_PRODUCT_TYPE",
    },
}


class MarketplaceValidationError(DistributionDraftError):
    def __init__(self, channel: Marketplace, sku: str, missing_fields: list[str]):
        self.channel = channel.value
        self.sku = sku
        self.missing_fields = missing_fields
        super().__init__(f"{channel.value}:{sku}: missing {', '.join(missing_fields)}")


def build_canonical_listing(
    product: dict[str, Any], channel: Marketplace | None = None
) -> dict[str, Any]:
    title, description = _copy(product, channel)
    return {
        "sku": product["sku"],
        "title": title,
        "description": description,
        "category": product.get("category"),
        "subcategory": product.get("subcategory"),
        "price": product.get("price_brl"),
        "promotional_price": product.get("promotional_price_brl"),
        "stock": product.get("availability"),
        "images": product.get("images", []),
        "condition": "new",
        "attributes": {
            "material": product.get("material"),
            "color": product.get("color"),
        },
        "variations": product.get("variants", []),
        "shipping": product.get("shipping"),
    }


def validate_listing(channel: Marketplace, listing: dict[str, Any]) -> None:
    missing = [
        field
        for field in ("sku", "title", "category", "price", "images")
        if not listing.get(field)
    ]
    if missing:
        raise MarketplaceValidationError(
            channel, listing.get("sku", "unknown"), missing
        )
    category_key = _category_mapping_key(listing)
    if category_key not in CATEGORY_MAPPINGS[channel]:
        raise MarketplaceValidationError(channel, listing["sku"], ["category_mapping"])


def _category_mapping_key(product: dict[str, Any]) -> str:
    category = str(product.get("category") or "")
    if category == "jewelry_accessories":
        return f"{category}/{product.get('subcategory') or 'other'}"
    return category


def _description(product: dict[str, Any]) -> str:
    name = product["name"]
    material = product.get("material") or "Material a confirmar"
    category = product.get("category") or "Produto Veratus"
    return (
        f"{name}. {category} Veratus com {material}. "
        "Consulte disponibilidade, valor e condições antes da compra."
    )


def _copy(product: dict[str, Any], channel: Marketplace | None) -> tuple[str, str]:
    """Título e descrição de listing_content para o marketplace.

    Vale para produto do catálogo nos quatro marketplaces e só com texto limpo
    no gate; a Meta e o produto fora do catálogo seguem com o texto antigo.
    """
    fallback = product["name"], _description(product)
    if channel is None or channel is Marketplace.META:
        return fallback
    from .listing_content import find_catalog_entry, listing_copy

    entry = find_catalog_entry(product)
    if entry is None:
        return fallback
    copy = listing_copy(entry, channel)
    if copy["issues"] or not copy["title"]:
        return fallback
    return copy["title"], copy["description"]


def _base_payload(product: dict[str, Any], canonical: dict[str, Any]) -> dict[str, Any]:
    return {
        "sku": product["sku"],
        "title": canonical["title"],
        "description": canonical["description"],
        "price_brl": product["price_brl"],
        "availability": product["availability"],
        "images": product["images"],
        "variants": product.get("variants", []),
        "external_write": False,
    }


def build_distribution_draft(
    product: dict[str, Any], channel: Marketplace
) -> dict[str, Any]:
    if product.get("status") != ProductStatus.APPROVED.value:
        raise DistributionDraftError(
            "somente produtos APPROVED podem gerar rascunho de distribuição"
        )

    issues = qa_issues(product)
    if issues:
        raise DistributionDraftError("produto reprovado no QA: " + " ".join(issues))

    canonical = build_canonical_listing(product, channel)
    validate_listing(channel, canonical)
    base = _base_payload(product, canonical)
    base["canonical"] = canonical
    base["attribute_mapping"] = CHANNEL_ATTRIBUTE_MAPPINGS[channel]
    if channel is Marketplace.MERCADO_LIVRE:
        base.update(
            {
                "category_id": CATEGORY_MAPPINGS[channel][
                    _category_mapping_key(product)
                ],
                "condition": "new",
                "listing_type_id": "CONFIRMAR_COM_CONTA",
            }
        )
    elif channel is Marketplace.SHOPEE:
        base.update(
            {
                "category_id": CATEGORY_MAPPINGS[channel][
                    _category_mapping_key(product)
                ],
                "logistics": "CONFIRMAR_COM_CONTA",
                "pre_order": False,
            }
        )
    elif channel is Marketplace.TIKTOK_SHOP:
        base.update(
            {
                "category_id": CATEGORY_MAPPINGS[channel][
                    _category_mapping_key(product)
                ],
                "warehouse": "CONFIRMAR_COM_CONTA",
                "package": "CONFIRMAR_MEDIDAS_E_PESO",
            }
        )
    elif channel is Marketplace.META:
        base.update(
            {
                "availability": "in stock",
                "brand": "Veratus",
                "google_product_category": CATEGORY_MAPPINGS[channel][
                    _category_mapping_key(product)
                ],
            }
        )
    elif channel is Marketplace.AMAZON:
        base.update(
            {
                "product_type": CATEGORY_MAPPINGS[channel][
                    _category_mapping_key(product)
                ],
                "marketplace_id": "A2Q3Y263D00KWC",
                "condition_type": "new_new",
                "fulfillment": "CONFIRMAR_COM_CONTA",
                "product_identifier": "CONFIRMAR_GTIN_OU_ISENCAO",
            }
        )
    else:
        raise DistributionDraftError("canal de distribuição não suportado")

    return {
        "mode": "DRAFT",
        "channel": channel.value,
        "sku": product["sku"],
        "payload": base,
        "external_write": False,
        "next_action": "revisar campos obrigatórios e confirmar adaptador oficial",
    }
