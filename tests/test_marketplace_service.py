from __future__ import annotations

from unittest.mock import patch

from veratus_agents.marketplace_ops import ListingState, MarketplaceStore
from veratus_agents.marketplace_service import MarketplaceConnectionService


class MercadoClient:
    def account_identity(self):
        return {"id": 123, "site_id": "MLB", "status": "active"}

    def discover_categories(self, title, *, site_id):
        assert title and site_id == "MLB"
        return [
            {
                "external_category_id": "MLB-WATCH",
                "external_category_name": "Relógios",
                "domain_id": "MLB-WATCHES",
                "source": "Mercado Livre domain_discovery",
            }
        ]

    def discover_attributes(self, category_id):
        assert category_id == "MLB-WATCH"
        return [
            {
                "id": "MATERIAL",
                "name": "Material",
                "classification": "REQUIRED",
                "value_type": "string",
            }
        ]

    def lookup_listing(self, account_id, sku):
        return {
            "sku": sku,
            "remote_ids": ["MLB1", "MLB2"] if sku == "duplicate" else [],
            "total": 2 if sku == "duplicate" else 0,
        }


def test_read_only_service_persists_discovery_and_sync_conflicts(tmp_path):
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")
    store.upsert_listing("duplicate", "mercado-livre", ListingState.DRAFT)
    service = MarketplaceConnectionService(store, mercado_factory=MercadoClient)
    products = [{"sku": "duplicate", "name": "Duplicate"}]

    with patch.dict(
        "os.environ", {"MERCADO_LIVRE_ACCESS_TOKEN": "present"}, clear=False
    ):
        reports = service.inspect_all(products)
    conflicts = service.read_only_sync(products)

    assert reports["mercado-livre"]["readiness"] == "CONNECTED_READ_ONLY"
    assert (
        store.category_details("mercado-livre")[0]["external_category_id"]
        == "MLB-WATCH"
    )
    assert store.attributes("mercado-livre")[0]["classification"] == "REQUIRED"
    assert conflicts[0]["type"] == "DUPLICATE_REMOTE_LISTING"


def test_publish_candidate_requires_full_live_readiness(tmp_path):
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")
    store.upsert_listing("arctic-white", "mercado-livre", ListingState.DRAFT)
    service = MarketplaceConnectionService(store)

    assert service.first_publish_plan([{"sku": "arctic-white"}]) is None
    assert store.publish_plans() == []
