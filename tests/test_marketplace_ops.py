from unittest.mock import patch

from veratus_agents.marketplace_adapters import Marketplace, build_distribution_draft
from veratus_agents.marketplace_ops import (
    CategoryMappingStatus,
    ChannelState,
    CredentialStatus,
    ExternalWriteGuard,
    ListingState,
    MarketplaceStore,
    Readiness,
    SyncConflict,
    apply_founder_confirmed_defaults,
    channel_readiness,
    make_marketplace_store,
    prepare_listing,
)


def approved_product():
    return {
        "sku": "VRT-REL-AUT-0001",
        "name": "Royal Blue",
        "category": "Relógios",
        "status": "APPROVED",
        "material": "confirmar com fornecedor",
        "supplier_ref": "supplier-royal-blue",
        "cost_brl": "65.00",
        "contribution_margin_brl": "150.00",
        "max_cpa_brl": "80.00",
        "price_brl": "289.90",
        "availability": "confirmar_com_equipe",
        "images": ["royal-blue.webp"],
        "variants": [],
        "evidence": [
            {"field": "material", "reference": "supplier-sheet"},
            {"field": "cost_brl", "reference": "supplier-invoice"},
            {"field": "availability", "reference": "team-confirmation"},
        ],
        "updated_at": "v1",
    }


def test_channel_registry_starts_disabled_and_readiness_is_draft(tmp_path):
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")
    channels = store.channels()

    assert len(channels) == 5
    assert all(item["state"] == ChannelState.DISABLED.value for item in channels)
    assert (
        channel_readiness(store.channel("shopee"), approved_product())
        is Readiness.NOT_READY
    )


def test_listing_draft_is_idempotent_and_survives_restart(tmp_path, filled_watch_sheet):
    # Relógio com foto real e ficha preenchida: o rascunho sai completo.
    path = tmp_path / "marketplace.sqlite3"
    store = MarketplaceStore(path)
    product = {**approved_product(), "image_status": "REAL_PHOTO"}
    payload = build_distribution_draft(product, Marketplace.SHOPEE)["payload"]

    first = prepare_listing(store, product, Marketplace.SHOPEE, payload)
    second = prepare_listing(store, product, Marketplace.SHOPEE, payload)
    restarted = MarketplaceStore(path)

    assert first["id"] == second["id"]
    assert len(restarted.listings("shopee")) == 1
    assert (
        restarted.listing("shopee", product["sku"])["state"] == ListingState.DRAFT.value
    )


def test_publish_guard_blocks_without_flags_and_approval(tmp_path):
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")
    allowed, reasons = ExternalWriteGuard(store).check("shopee", approved_product())

    assert allowed is False
    assert "GLOBAL_PUBLISH_DISABLED" in reasons
    assert "MARKETPLACE_MODE_LOCAL" in reasons
    assert "CHANNEL_DISABLED" in reasons
    assert "PUBLISH_APPROVAL_REQUIRED" in reasons


def test_ceo_override_is_scoped_to_sku_and_channel(tmp_path):
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")
    store.override(
        "VRT-REL-AUT-0001", "shopee", "PUBLISH", "Não publicar este SKU na Shopee"
    )

    assert store.is_overridden("VRT-REL-AUT-0001", "shopee", "PUBLISH")
    assert not store.is_overridden("VRT-REL-AUT-0001", "meta", "PUBLISH")


def test_ceo_override_blocks_draft_for_only_selected_channel(tmp_path):
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")
    product = approved_product()
    store.override(product["sku"], "shopee", "PUBLISH", "Decisão do CEO")
    payload = build_distribution_draft(product, Marketplace.SHOPEE)["payload"]

    blocked = prepare_listing(store, product, Marketplace.SHOPEE, payload)

    assert blocked["state"] == ListingState.BLOCKED.value
    assert blocked["last_error"] == "BLOCKED_BY_CEO"
    assert store.listing("meta", product["sku"]) is None


def test_account_reports_status_without_exposing_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOPEE_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("SHOPEE_ACCOUNT_ID", "account-123")
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")

    account = store.account("shopee")

    assert account.account_id == "PRESENT"
    assert account.credential_reference == "SHOPEE_ACCESS_TOKEN"
    assert account.credential_status is CredentialStatus.NOT_TESTED
    assert account.connected is False


def test_category_mapping_and_sync_conflict_survive_restart(tmp_path):
    path = tmp_path / "marketplace.sqlite3"
    store = MarketplaceStore(path)
    mapping = store.upsert_category_mapping("shopee", "accessories/watches")
    conflict = SyncConflict(
        type="PRICE_CONFLICT",
        sku="VRT-REL-AUT-0001",
        channel="shopee",
        local_value="289.90",
        remote_value="299.90",
        detected_at="2026-09-20T00:00:00+00:00",
        recommendation="revisar preço remoto",
    )
    store.record_sync_conflict(conflict)
    restarted = MarketplaceStore(path)

    assert (
        mapping["status"]
        == CategoryMappingStatus.CATEGORY_DISCOVERY_BLOCKED_BY_CREDENTIALS.value
    )
    assert restarted.category_mappings("shopee")[0]["external_category"] is None
    assert restarted.sync_conflicts()[0]["type"] == "PRICE_CONFLICT"


def test_founder_defaults_preserve_confirmed_sku_data_and_mark_estimates():
    merged, conflicts = apply_founder_confirmed_defaults(
        {
            "sku": "VRT-REL-AUT-0001",
            "price_brl": "319.90",
            "field_sources": {"price_brl": "FOUNDER_CONFIRMED"},
        }
    )

    assert merged["price_brl"] == "319.90"
    assert merged["cost_brl"] == "65.00"
    assert merged["product_length_cm"] == 4.8
    assert merged["dimensions_status"] == "ESTIMATED"
    assert merged["physical_verification_status"] == "NOT_PHYSICALLY_VERIFIED"
    assert conflicts == [
        {
            "type": "DATA_CONFLICT",
            "field": "price_brl",
            "existing": "319.90",
            "shared_default": "289.90",
        }
    ]


def test_marketplace_store_factory_prefers_postgres_when_configured(tmp_path):
    marker = object()
    with patch(
        "veratus_agents.postgres_marketplace.PostgresMarketplaceStore",
        return_value=marker,
    ) as postgres:
        store = make_marketplace_store(
            tmp_path / "marketplace.sqlite3", "postgresql://example"
        )

    assert store is marker
    postgres.assert_called_once_with("postgresql://example")


def test_watch_draft_is_prepared_and_lists_what_is_missing(tmp_path):
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")
    product = approved_product()
    payload = build_distribution_draft(product, Marketplace.MERCADO_LIVRE)["payload"]

    listing = prepare_listing(store, product, Marketplace.MERCADO_LIVRE, payload)

    # O relógio sobe: o rascunho existe e espera só o que o fundador preenche.
    assert listing["state"] == ListingState.DRAFT.value
    assert listing["last_error"].startswith("PENDENTE:")
    pending = listing["payload"]["pending_info"]
    assert pending[:2] == ["NEEDS_REAL_PHOTO", "ORIGIN_UNCONFIRMED"]
    assert "FICHA:movimento" in pending
    assert "FICHA:royal-blue.estoque" in pending
    allowed, reasons = ExternalWriteGuard(store).check("mercado-livre", product)
    assert not allowed
    assert "WATCH_INFO_PENDING:NEEDS_REAL_PHOTO" in reasons


def test_real_photo_alone_still_waits_for_the_sheet(tmp_path):
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")
    product = {**approved_product(), "image_status": "REAL_PHOTO"}
    payload = build_distribution_draft(product, Marketplace.SHOPEE)["payload"]

    listing = prepare_listing(store, product, Marketplace.SHOPEE, payload)

    assert listing["state"] == ListingState.DRAFT.value
    assert "NEEDS_REAL_PHOTO" not in listing["payload"]["pending_info"]
    assert listing["payload"]["pending_info"][0] == "ORIGIN_UNCONFIRMED"


def test_filled_sheet_releases_the_watch_draft(tmp_path, filled_watch_sheet):
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")
    product = {**approved_product(), "image_status": "REAL_PHOTO"}
    payload = build_distribution_draft(product, Marketplace.AMAZON)["payload"]
    pending_first = prepare_listing(
        MarketplaceStore(tmp_path / "other.sqlite3"),
        approved_product(),
        Marketplace.AMAZON,
        payload,
    )

    listing = prepare_listing(store, product, Marketplace.AMAZON, payload)

    assert pending_first["payload"]["pending_info"] == ["NEEDS_REAL_PHOTO"]
    assert listing["state"] == ListingState.DRAFT.value
    assert listing["last_error"] is None
    assert listing["payload"]["ficha"]["movimento"] == "quartzo"
    assert listing["payload"]["ficha"]["gtin"] == "ISENTO"
    _, reasons = ExternalWriteGuard(store).check("amazon", product)
    assert not any(reason.startswith("WATCH_INFO_PENDING") for reason in reasons)


def test_publish_guard_requires_a_registered_agent_with_publish(tmp_path, monkeypatch):
    # Tudo o mais aberto (só no processo do teste): só o agente segura a publicação.
    for name in (
        "PUBLISH_ENABLED",
        "SHOPEE_ENABLED",
        "SHOPEE_PUBLISH_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("MARKETPLACE_MODE", "LIVE")
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")
    product = {"sku": "VRT-JOI-TESTE", "category": "Colares", "status": "APPROVED"}
    approval = type("Approval", (), {"status": "APPROVED"})()
    guard = ExternalWriteGuard(store)

    # Padrão (agente do canal), papel do registro sem PUBLISH e ids fora do registro.
    for agent in (None, "shopee-agent", "marketplace-agent", "agente-inventado"):
        allowed, reasons = guard.check(
            "shopee", product, approval=approval, agent=agent, publish_enabled=True
        )
        assert allowed is False
        assert reasons == ["AGENT_PUBLISH_PERMISSION_REQUIRED"]
