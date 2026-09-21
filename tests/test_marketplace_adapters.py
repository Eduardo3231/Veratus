from decimal import Decimal

import pytest

from veratus_agents.marketplace_adapters import (
    DistributionDraftError,
    Marketplace,
    build_distribution_draft,
)
from veratus_agents.product_master import (
    EvidenceRef,
    ProductCreate,
    ProductStatus,
    SQLiteProductStore,
)


def approved_product(tmp_path):
    store = SQLiteProductStore(tmp_path / "operations.db")
    product = store.create(
        ProductCreate(
            name="Royal Blue",
            category="Relógios",
            category_code="REL",
            product_code="AUT",
            material="A confirmar com fornecedor",
            supplier_ref="supplier-royal-blue",
            availability="confirmar_com_equipe",
            images=["assets/catalog/royal-blue.webp"],
            cost_brl=Decimal("65.00"),
            price_brl=Decimal("289.90"),
            contribution_margin_brl=Decimal("150.00"),
            max_cpa_brl=Decimal("80.00"),
            evidence=[
                EvidenceRef(field="material", reference="ficha-fornecedor"),
                EvidenceRef(field="cost_brl", reference="nota-fornecedor"),
                EvidenceRef(field="availability", reference="confirmacao-equipe"),
            ],
        ),
        actor="test",
        event_id="create-marketplace-001",
    )
    store.transition(
        product["sku"],
        target=ProductStatus.ENRICHING,
        actor="test",
        event_id="transition-marketplace-001",
        reason="Preparar ficha para QA",
    )
    product, issues = store.transition(
        product["sku"],
        target=ProductStatus.APPROVED,
        actor="quality-agent",
        event_id="approve-marketplace-001",
        reason="Ficha conferida para rascunho",
    )
    assert not issues
    assert product is not None
    return product


@pytest.mark.parametrize("channel", list(Marketplace))
def test_builds_draft_for_every_marketplace(tmp_path, channel):
    draft = build_distribution_draft(approved_product(tmp_path), channel)

    assert draft["mode"] == "DRAFT"
    assert draft["channel"] == channel.value
    assert draft["external_write"] is False
    assert draft["payload"]["sku"].startswith("VRT-REL-AUT-")


def test_blocks_product_that_is_not_approved(tmp_path):
    store = SQLiteProductStore(tmp_path / "operations.db")
    product = store.create(
        ProductCreate(
            name="Incomplete",
            category="Relógios",
            category_code="REL",
            product_code="AUT",
        ),
        actor="test",
        event_id="create-marketplace-002",
    )

    with pytest.raises(DistributionDraftError, match="somente produtos APPROVED"):
        build_distribution_draft(product, Marketplace.META)


def test_blocks_unmapped_category(tmp_path):
    product = approved_product(tmp_path)
    product["category"] = "Categoria desconhecida"

    with pytest.raises(DistributionDraftError, match="missing category_mapping"):
        build_distribution_draft(product, Marketplace.SHOPEE)
