from __future__ import annotations

from pathlib import Path

from veratus_agents.catalog import (
    load_catalog,
    public_catalog,
    validate_catalog_sync,
)
from veratus_agents.data_discovery import discover_products
from veratus_agents.marketplace_adapters import CATEGORY_MAPPINGS, Marketplace
from veratus_agents.operations import OperationalRuntime

ROOT = Path(__file__).resolve().parents[1]


def feminine_products() -> list[dict]:
    return [item for item in load_catalog() if item.get("collection") == "feminine"]


def test_feminine_product_identity_and_claim_safety() -> None:
    products = feminine_products()
    forbidden_claims = {
        "diamante",
        "esmeralda natural",
        "ouro 18k",
        "swarovski",
        "zircônia",
        "ônix",
        "safira",
    }

    assert len(products) == 9
    assert len({item["sku"] for item in products}) == 9
    assert {item["subcategory"] for item in products} == {
        "necklace",
        "bracelet",
        "anklet",
    }
    assert all(item["status"] == "DRAFT" for item in products)
    assert all(item["material"] is None for item in products)
    assert all(item["cost"] is None and item["sale_price"] is None for item in products)
    assert all(item["financial_status"] == "NEEDS_PRICING" for item in products)
    assert not any(
        claim in item["description"].casefold()
        for item in products
        for claim in forbidden_claims
    )


def test_feminine_assets_are_unique_and_exist() -> None:
    products = feminine_products()
    primary_images = [item["primary_image"] for item in products]

    assert len(primary_images) == len(set(primary_images)) == 9
    assert all((ROOT / "landing" / image).exists() for image in primary_images)
    assert all(item["gallery_images"] == [] for item in products)


def test_public_catalog_is_derived_and_private_fields_are_absent() -> None:
    projection = public_catalog()

    assert len(projection) == 17
    assert "black-gmt" not in {item["id"] for item in projection}
    assert sum(item["collection"] == "feminine" for item in projection) == 9
    assert all("supplier_visibility" not in item for item in projection)
    assert all("cost" not in item and "sale_price" not in item for item in projection)
    assert validate_catalog_sync()[0] is True


def test_marketplace_taxonomy_is_specific_to_jewelry_subcategory() -> None:
    for channel in Marketplace:
        mappings = CATEGORY_MAPPINGS[channel]
        assert "jewelry_accessories/necklace" in mappings
        assert "jewelry_accessories/bracelet" in mappings
        assert "jewelry_accessories/anklet" in mappings


def test_feminine_commands_run_pipeline_and_block_unpriced_distribution(
    tmp_path: Path,
) -> None:
    runtime = OperationalRuntime(
        str(tmp_path / "feminine-runtime.sqlite3"),
        product_source=discover_products,
    )
    intake = runtime.execute(
        "Gerente, cadastre a nova coleção feminina da Veratus e prepare os produtos para revisão.",
        idempotency_key="phase4-intake-test",
    )
    distribution = runtime.execute(
        "Gerente, prepare os produtos femininos aprovados para os marketplaces.",
        idempotency_key="phase4-distribution-test",
    )

    assert intake["status"] == "COMPLETED"
    assert {task["agent"] for task in intake["tasks"]} >= {
        "product-intake",
        "copy-agent",
        "creative-agent",
        "pricing-agent",
        "quality-agent",
    }
    quality = next(task for task in intake["tasks"] if task["agent"] == "quality-agent")
    assert len(quality["result"]["needs_information"]) == 9
    assert quality["result"]["false_gemstone_claims"] == 0

    assert distribution["status"] == "COMPLETED"
    channel_tasks = [
        task
        for task in distribution["tasks"]
        if task["action"] == "PREPARE_FEMININE_DRAFTS"
    ]
    assert len(channel_tasks) == 4
    assert all(not task["result"]["drafts"] for task in channel_tasks)
    assert all(len(task["result"]["blocked"]) == 9 for task in channel_tasks)
    assert all(
        item["reason"] == "BLOCKED_NEEDS_PRICING"
        for task in channel_tasks
        for item in task["result"]["blocked"]
    )
