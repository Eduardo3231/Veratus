"""Decisões do fundador de 26/09/2026 registradas no Product Master."""

from __future__ import annotations

import json
from pathlib import Path

from veratus_agents import commercial_config as config
from veratus_agents.catalog import public_catalog
from veratus_agents.data_discovery import discover_products, discovery_report
from veratus_agents.paid_media import calculate_economics

UNVERIFIED_COSTS = ["shipping_cost_paid_by_veratus", "payment_fee", "tax"]


def test_watch_economics_snapshot_is_incomplete_on_exactly_three_costs() -> None:
    fields = config.WATCH_ECONOMICS_SNAPSHOT["fields"]
    unverified = [name for name, item in fields.items() if item["value"] is None]

    assert unverified == UNVERIFIED_COSTS
    assert all(fields[name]["status"] == "UNVERIFIED" for name in unverified)
    assert fields["price"]["value"] == "289.90"
    assert fields["unit_cost"]["value"] == "65.00"
    assert fields["customer_shipping"]["value"] == "0.00"
    assert fields["customer_fees"]["value"] == "0.00"

    for product in discover_products():
        if product["fields"]["category"]["value"] != config.OFFICIAL_CATEGORY:
            continue
        economics = calculate_economics(product)
        assert economics["status"] == "INCOMPLETE"
        assert economics["missing_fields"] == UNVERIFIED_COSTS
        assert economics["break_even_cpa"] is None
        assert economics["break_even_cpa_final"] is False


def test_supplier_identity_never_leaves_the_product_master() -> None:
    assert config.SHIP_FROM == {"visibility": "PRIVATE"}
    outputs = json.dumps(
        [discovery_report(), public_catalog()], ensure_ascii=False
    ).casefold()

    for key in ("establishment", "postal_code", "ship_from", '"address"'):
        assert key not in outputs


def test_delivery_promise_and_registered_origin_stay_an_open_question() -> None:
    question = config.FOUNDER_OPEN_QUESTIONS[0]

    assert config.DELIVERY_MAX_DAYS == 7
    assert config.FREE_SHIPPING is True
    assert config.CUSTOMER_EXTRA_FEES_BRL == 0
    assert question["status"] == "PENDING_FOUNDER"
    assert "PRIVATE" in question["question"]


def test_watch_storefront_changes_wait_for_real_photos() -> None:
    discovered = {item["sku"]: item for item in discover_products()}
    blocker = config.STOREFRONT_BLOCKERS[0]
    projection = public_catalog()
    watches = [item for item in projection if item["collection"] == "watches"]

    # Black GMT stays active for the agents; the storefront waits for real
    # photos of the exact item (the physical pieces carry no third-party mark).
    assert discovered["black-gmt"]["active"] is True
    assert "watch_physical_marks" in config.FOUNDER_DECISIONS
    assert blocker["id"] == "watch-real-photos-pending"
    assert blocker["status"] == "BLOCKING"
    assert {"watch_public_price", "black_gmt_storefront"} <= set(blocker["blocks"])
    assert "black-gmt" not in {item["id"] for item in projection}
    assert all("price_brl" not in item for item in projection)
    assert watches and all(item["image"] is None for item in watches)
    assert {item["image_status"] for item in watches} == {"NEEDS_REAL_PHOTO"}


def test_home_purchase_journey_matches_confirmed_terms() -> None:
    landing = Path(__file__).resolve().parents[1] / "landing"
    home = (landing / "index.html").read_text(encoding="utf-8")
    terms = (landing / "condicoes-de-compra.html").read_text(encoding="utf-8")

    assert config.FREE_SHIPPING and config.CUSTOMER_EXTRA_FEES_BRL == 0
    assert "Frete grátis" in home and "sem taxas adicionais" in home
    assert f"entrega em até {config.DELIVERY_MAX_DAYS} dias" in home
    assert "desistir em até 7 dias após o recebimento" in home
    assert 'href="condicoes-de-compra.html"' in home and 'href="#como-comprar"' in home
    # Freight and delivery are published now; nothing says "will be informed".
    for page in (home, terms):
        assert "informad" not in page.casefold()
