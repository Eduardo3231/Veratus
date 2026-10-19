from __future__ import annotations

from unittest.mock import patch

from veratus_agents.channel_adapters import MercadoLivreConversationAdapter
from veratus_agents.marketplace_ops import ListingState, MarketplaceStore
from veratus_agents.marketplace_service import MarketplaceConnectionService, listing_sku


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
    # O id remoto fica no listing: a pergunta do anúncio chega com o SKU.
    assert store.listing("mercado-livre", "duplicate")["external_listing_id"] == "MLB1"
    assert listing_sku(store, "mercado-livre", "MLB2") == "duplicate"
    assert listing_sku(store, "mercado-livre", "MLB9") is None


class _Response:
    def __init__(self, status, data):
        self.status_code, self.ok, self._data = status, status < 400, data

    def json(self):
        return self._data


def test_mercado_livre_question_reads_retry_once_after_refresh_and_find_the_sku():
    tokens, calls = ["velho"], []

    class Session:
        def get(self, url, headers, params, timeout):
            calls.append((url.rsplit("/", 1)[-1], headers["Authorization"]))
            if headers["Authorization"] == "Bearer velho":
                return _Response(401, {})
            if url.endswith("/items/MLB123"):
                attributes = [{"id": "SELLER_SKU", "value_name": "royal-blue"}]
                return _Response(200, {"attributes": attributes})
            return _Response(200, {"questions": [{"id": 7, "item_id": "MLB123"}]})

    adapter = MercadoLivreConversationAdapter(
        token_provider=lambda: tokens[-1],
        seller_id=None,
        session=Session(),
        token_refresher=lambda: tokens.append("novo"),
    )

    [question] = adapter.list_unanswered_questions()
    assert question["item_id"] == "MLB123" and len(calls) == 2
    assert adapter.item_sku(question["item_id"]) == "royal-blue"
    assert adapter.item_sku("../users/me") is None and len(calls) == 3


def test_publish_candidate_requires_full_live_readiness(tmp_path):
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")
    store.upsert_listing("arctic-white", "mercado-livre", ListingState.DRAFT)
    service = MarketplaceConnectionService(store)

    assert service.first_publish_plan([{"sku": "arctic-white"}]) is None
    assert store.publish_plans() == []
