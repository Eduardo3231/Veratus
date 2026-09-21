from decimal import Decimal

OFFICIAL_SALE_PRICE_BRL = Decimal("289.90")
OFFICIAL_PRODUCT_COST_BRL = Decimal("65.00")
OFFICIAL_MATERIAL = "stainless_steel"
OFFICIAL_MATERIAL_LABEL = "Aço inoxidável"
OFFICIAL_CATEGORY = "accessories"
OFFICIAL_SUBCATEGORY = "watches"
INVENTORY_MODE = "ON_DEMAND"
SUPPLIER_VISIBILITY = "PRIVATE"
DEFAULT_CHANNEL_STOCK_CAP = None
CHANNEL_STOCK_CAP_FALLBACK = 20
HANDLING_TIME_BUSINESS_DAYS = 2
SHIP_FROM = {
    "establishment": "Galerie Inauen",
    "address": "Schifflände 12",
    "postal_code": "8001",
    "city": "Zürich",
    "country": "Switzerland",
    "country_code": "CH",
}
PACKAGE_PROFILE = {
    "package_weight_g": 350,
    "package_length_cm": 18,
    "package_width_cm": 14,
    "package_height_cm": 10,
}
ESTIMATED_WATCH_DIMENSIONS = {
    "watch_case_diameter_cm": 4.1,
    "watch_case_thickness_cm": 1.25,
    "watch_lug_to_lug_cm": 4.8,
    "watch_bracelet_width_cm": 2.0,
    "watch_open_length_cm": 21.0,
    "product_length_cm": 4.8,
    "product_width_cm": 4.1,
    "product_height_cm": 1.25,
    "status": "ESTIMATED",
    "physically_verified": False,
}


def calculate_official_economics() -> dict[str, str]:
    gross_profit = OFFICIAL_SALE_PRICE_BRL - OFFICIAL_PRODUCT_COST_BRL
    gross_margin = gross_profit / OFFICIAL_SALE_PRICE_BRL * 100
    markup = gross_profit / OFFICIAL_PRODUCT_COST_BRL * 100
    return {
        "sale_price_brl": f"{OFFICIAL_SALE_PRICE_BRL:.2f}",
        "product_cost_brl": f"{OFFICIAL_PRODUCT_COST_BRL:.2f}",
        "gross_profit_brl": f"{gross_profit:.2f}",
        "gross_margin_percent": f"{gross_margin:.2f}",
        "markup_percent": f"{markup:.2f}",
        "channel_fee_estimate": "NOT_AVAILABLE",
        "contribution_margin": "NOT_CALCULATED_WITHOUT_CHANNEL_COSTS",
        "maximum_sustainable_cpa": "NOT_CALCULATED_WITHOUT_OPERATIONAL_COSTS",
    }
