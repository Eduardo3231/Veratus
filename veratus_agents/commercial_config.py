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
# Origem, nome e endereço do fornecedor ficam fora do repositório, do site, dos
# marketplaces, da copy, de metadados e de logs (FOUNDER_CONFIRMED 2026-09-26).
SHIP_FROM = {"visibility": SUPPLIER_VISIBILITY}
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

# --- Decisões do fundador: FOUNDER_CONFIRMED 2026-09-26 -----------------------
FOUNDER_CONFIRMED_AT = "2026-09-26"
DELIVERY_MAX_DAYS = 7
FREE_SHIPPING = True
CUSTOMER_SHIPPING_BRL = Decimal("0.00")
CUSTOMER_EXTRA_FEES_BRL = Decimal("0.00")
SELLER = {
    "name": "Veratus",
    "city": "São Paulo/SP",
    "email": "veratus.ltda@gmail.com",
    "whatsapp": "(11) 95832-3612",
    # O CPF autorizado só existe na variável de ambiente, nunca no repositório.
    "document_env": "VERATUS_SELLER_DOCUMENT",
}
FOUNDER_DECISIONS = {
    "black_gmt_storefront": (
        "Black GMT volta à vitrine e segue ativo no Product Master, API e agentes."
    ),
    "seller_identification": (
        "São Paulo/SP, veratus.ltda@gmail.com e (11) 95832-3612; "
        "CPF do vendedor via VERATUS_SELLER_DOCUMENT."
    ),
    "supplier": (
        "PRIVATE: origem, nome e endereço nunca aparecem em site, marketplace, "
        "copy, metadados ou logs."
    ),
    "delivery": f"Entrega em até {DELIVERY_MAX_DAYS} dias para todos os produtos.",
    "shipping": "Frete grátis para o cliente; nenhuma taxa extra cobrada do cliente.",
    "prices": (
        "Preço de todos os produtos no site; relógios R$ 289,90. Joias só com "
        "preço de fonte real, senão NEEDS_PRICING."
    ),
    "jewelry_material": "UNVERIFIED: a copy usa apenas tom dourado ou tom prateado.",
    # FOUNDER_CONFIRMED 2026-09-27.
    "watch_physical_marks": (
        "As peças físicas dos relógios não levam marca de terceiros; a marca está "
        "só nas fotos do fornecedor, retiradas do site."
    ),
}
FOUNDER_OPEN_QUESTIONS = (
    {
        "id": "delivery-vs-registered-origin",
        "status": "PENDING_FOUNDER",
        "question": (
            "O Product Master registra origem internacional (PRIVATE) e manuseio "
            f"de {HANDLING_TIME_BUSINESS_DAYS} dias úteis; o fundador confirmou "
            f"entrega em até {DELIVERY_MAX_DAYS} dias. A entrega porta a porta em "
            f"{DELIVERY_MAX_DAYS} dias é viável a partir da origem registrada?"
        ),
    },
)

# Verificado em 2026-09-26: as fotos dos relógios (catálogo, vídeo do hero,
# campanha e social) mostram marca de terceiro. Em 2026-09-27 o fundador
# confirmou que as peças físicas não levam marca; as fotos foram para
# quarantine/third-party-marks/. Sem foto real, o relógio fica na vitrine sem
# imagem, sem preço e sem botão de pedido.
STOREFRONT_BLOCKERS = (
    {
        "id": "watch-real-photos-pending",
        "status": "BLOCKING",
        "scope": "watches",
        "blocks": (
            "watch_public_price",
            "watch_order_cta",
            "black_gmt_storefront",
            "paid_media_launch",
        ),
        "evidence": (
            "fotos do fornecedor com marca de terceiro em quarentena "
            "(quarantine/third-party-marks/manifest.json)"
        ),
        "resolved": "peças físicas sem marca de terceiros (FOUNDER_CONFIRMED 2026-09-27)",
        "unblock": "fotos reais do item exato, sem marca de terceiros, com fonte",
    },
)

# EconomicsSnapshot dos relógios. Os três custos UNVERIFIED mantêm o status
# INCOMPLETE; nenhum CPA de equilíbrio é final até que tenham fonte.
WATCH_ECONOMICS_SNAPSHOT = {
    "scope": "watches",
    "recorded_at": FOUNDER_CONFIRMED_AT,
    "fields": {
        "price": {"value": f"{OFFICIAL_SALE_PRICE_BRL:.2f}", "status": "CONFIRMED"},
        "unit_cost": {
            "value": f"{OFFICIAL_PRODUCT_COST_BRL:.2f}",
            "status": "CONFIRMED",
        },
        "customer_shipping": {
            "value": f"{CUSTOMER_SHIPPING_BRL:.2f}",
            "status": "FOUNDER_CONFIRMED",
        },
        "customer_fees": {
            "value": f"{CUSTOMER_EXTRA_FEES_BRL:.2f}",
            "status": "FOUNDER_CONFIRMED",
        },
        "shipping_cost_paid_by_veratus": {"value": None, "status": "UNVERIFIED"},
        "payment_fee": {"value": None, "status": "UNVERIFIED"},
        "tax": {"value": None, "status": "UNVERIFIED"},
    },
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
