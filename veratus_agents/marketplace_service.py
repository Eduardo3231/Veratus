from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .marketplace_clients import (
    MarketplaceApiError,
    MercadoLivreOAuthTokenManager,
    MercadoLivreReadOnlyClient,
    TikTokShopReadOnlyClient,
    connection_status,
)
from .marketplace_ops import (
    CategoryMappingStatus,
    MarketplaceStore,
    SyncConflict,
    _now,
)


class MarketplaceConnectionService:
    """Read-only orchestration layer used by marketplace agents."""

    def __init__(
        self,
        store: MarketplaceStore,
        *,
        mercado_factory: Callable[[], Any] | None = None,
        tiktok_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.store = store
        self.mercado_factory = mercado_factory or self._mercado_client
        self.tiktok_factory = tiktok_factory or self._tiktok_client

    @staticmethod
    def _mercado_client() -> MercadoLivreReadOnlyClient:
        try:
            from .mercado_livre_oauth import (
                MercadoLivreOAuthStore,
                OAuthConfigurationError,
                TokenStorageError,
            )

            oauth_store = MercadoLivreOAuthStore.from_env()
            if oauth_store.has_tokens():
                return MercadoLivreOAuthTokenManager.from_env(oauth_store).client()
        except (OAuthConfigurationError, TokenStorageError):
            pass
        return MercadoLivreReadOnlyClient(os.environ["MERCADO_LIVRE_ACCESS_TOKEN"])

    @staticmethod
    def _tiktok_client() -> TikTokShopReadOnlyClient:
        return TikTokShopReadOnlyClient(
            os.environ["TIKTOK_SHOP_APP_KEY"],
            os.environ["TIKTOK_SHOP_APP_SECRET"],
            os.environ["TIKTOK_SHOP_ACCESS_TOKEN"],
            shop_cipher=os.getenv("TIKTOK_SHOP_SHOP_CIPHER"),
        )

    def inspect_all(self, products: list[dict[str, Any]]) -> dict[str, Any]:
        static = connection_status()
        try:
            from .mercado_livre_oauth import persisted_credentials_available

            if persisted_credentials_available():
                static["mercado-livre"].update(
                    credentials="PRESENT",
                    auth="NOT_TESTED",
                    category_discovery="NOT_TESTED",
                )
        except (ImportError, RuntimeError):
            pass
        reports: dict[str, Any] = {}
        for channel in ("mercado-livre", "shopee", "tiktok-shop", "meta"):
            if static[channel]["credentials"] != "PRESENT":
                reports[channel] = self.store.save_connection_check(
                    channel, {**static[channel], "channel": channel, "api_reads": []}
                )
                continue
            if channel == "mercado-livre":
                reports[channel] = self._inspect_mercado(products, static[channel])
            elif channel == "tiktok-shop":
                reports[channel] = self._inspect_tiktok(products, static[channel])
            else:
                reports[channel] = self.store.save_connection_check(
                    channel,
                    {
                        **static[channel],
                        "channel": channel,
                        "auth": "NOT_TESTED",
                        "readiness": "NOT_READY",
                        "api_reads": [],
                        "errors": ["API_CONTRACT_UNVERIFIED"],
                    },
                )
        return reports

    def inspect_mercado(self, products: list[dict[str, Any]]) -> dict[str, Any]:
        base = connection_status()["mercado-livre"]
        try:
            from .mercado_livre_oauth import persisted_credentials_available

            if persisted_credentials_available():
                base.update(
                    credentials="PRESENT",
                    auth="NOT_TESTED",
                    category_discovery="NOT_TESTED",
                )
        except (ImportError, RuntimeError):
            pass
        if base["credentials"] != "PRESENT":
            return self.store.save_connection_check(
                "mercado-livre",
                {**base, "channel": "mercado-livre", "api_reads": []},
            )
        return self._inspect_mercado(products, base)

    def _inspect_mercado(
        self, products: list[dict[str, Any]], base: dict[str, Any]
    ) -> dict[str, Any]:
        report = {**base, "channel": "mercado-livre", "api_reads": [], "errors": []}
        try:
            client = self.mercado_factory()
            account = client.account_identity()
            report.update(auth="HEALTHY", account="HEALTHY")
            report["api_reads"].append("GET /users/me")
            categories = client.discover_categories(
                "Relógio automático Veratus", site_id=account.get("site_id") or "MLB"
            )
            report["api_reads"].append("GET /sites/{site_id}/domain_discovery/search")
            if not categories:
                report["category_discovery"] = "NOT_FOUND"
                report["readiness"] = "NOT_READY"
                return self.store.save_connection_check("mercado-livre", report)
            selected = categories[0]
            category_id = str(selected["external_category_id"])
            attributes = client.discover_attributes(category_id)
            report["api_reads"].append("GET /categories/{category_id}/attributes")
            self.store.upsert_category_mapping(
                "mercado-livre",
                "accessories/watches",
                external_category=category_id,
                status=CategoryMappingStatus.DISCOVERED,
            )
            self.store.save_category_details(
                "mercado-livre",
                "accessories/watches",
                {
                    **selected,
                    "required_attributes": [
                        item["id"]
                        for item in attributes
                        if item["classification"] == "REQUIRED"
                    ],
                },
            )
            self.store.save_attributes("mercado-livre", category_id, attributes)
            remote = {}
            for product in products:
                sku = str(product.get("sku") or product.get("id"))
                remote[sku] = client.lookup_listing(str(account["id"]), sku)
            report["api_reads"].append("GET /users/{user_id}/items/search?seller_sku")
            report.update(
                category_discovery="HEALTHY",
                attributes="HEALTHY",
                readback="HEALTHY",
                logistics="NOT_TESTED",
                external_category=selected,
                required_attributes=sum(
                    item["classification"] == "REQUIRED" for item in attributes
                ),
                remote_lookup=remote,
                readiness="CONNECTED_READ_ONLY",
            )
        except (MarketplaceApiError, KeyError) as exc:
            code = (
                exc.code
                if isinstance(exc, MarketplaceApiError)
                else "CREDENTIALS_INVALID"
            )
            report.update(auth="INVALID", readiness="NOT_READY")
            report["errors"].append(code)
        return self.store.save_connection_check("mercado-livre", report)

    def _inspect_tiktok(
        self, products: list[dict[str, Any]], base: dict[str, Any]
    ) -> dict[str, Any]:
        del products
        report = {**base, "channel": "tiktok-shop", "api_reads": [], "errors": []}
        try:
            client = self.tiktok_factory()
            account = client.account_identity()
            report["api_reads"].append("GET /authorization/202309/shops")
            if not account.get("shops"):
                raise MarketplaceApiError("NO_AUTHORIZED_SHOP")
            report.update(auth="HEALTHY", account="HEALTHY")
            categories = client.discover_categories("relógio")
            report["api_reads"].append("GET /product/202309/categories")
            eligible = [
                item
                for item in categories
                if item.get("is_leaf")
                and "AVAILABLE" in item.get("permission_statuses", [])
            ]
            if len(eligible) != 1:
                report.update(
                    category_discovery="REVIEW_REQUIRED",
                    attributes="NOT_TESTED",
                    readiness="CONNECTED_READ_ONLY",
                    category_candidates=eligible,
                )
                return self.store.save_connection_check("tiktok-shop", report)
            selected = eligible[0]
            rules = client.category_rules(str(selected["external_category_id"]))
            report["api_reads"].append(
                "GET /product/202309/categories/{category_id}/rules"
            )
            self.store.upsert_category_mapping(
                "tiktok-shop",
                "accessories/watches",
                external_category=str(selected["external_category_id"]),
                status=CategoryMappingStatus.DISCOVERED,
            )
            self.store.save_category_details(
                "tiktok-shop",
                "accessories/watches",
                {**selected, "required_attributes": [], "category_rules": rules},
            )
            report.update(
                category_discovery="HEALTHY",
                attributes="PARTIAL",
                logistics="NOT_TESTED",
                readback="NOT_TESTED",
                readiness="CONNECTED_READ_ONLY",
            )
        except (MarketplaceApiError, KeyError) as exc:
            code = (
                exc.code
                if isinstance(exc, MarketplaceApiError)
                else "CREDENTIALS_INVALID"
            )
            report.update(auth="INVALID", readiness="NOT_READY")
            report["errors"].append(code)
        return self.store.save_connection_check("tiktok-shop", report)

    def read_only_sync(self, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        product_by_sku = {
            str(item.get("sku") or item.get("id")): item for item in products
        }
        conflicts = []
        for check in self.store.connection_checks():
            channel = check["channel"]
            for sku, remote in check.get("remote_lookup", {}).items():
                local = self.store.listing(channel, sku)
                if remote.get("total", 0) > 1:
                    conflicts.append(
                        self.store.record_sync_conflict(
                            SyncConflict(
                                "DUPLICATE_REMOTE_LISTING",
                                sku,
                                channel,
                                str(local.get("id") if local else None),
                                str(remote.get("remote_ids", [])),
                                _now(),
                                "revisar duplicidade antes de publicar",
                            )
                        )
                    )
                if sku not in product_by_sku:
                    conflicts.append(
                        self.store.record_sync_conflict(
                            SyncConflict(
                                "REMOTE_PRODUCT_NOT_IN_MASTER",
                                sku,
                                channel,
                                "MISSING",
                                str(remote.get("remote_ids", [])),
                                _now(),
                                "reconciliar com Product Master",
                            )
                        )
                    )
        return conflicts

    def first_publish_plan(
        self, products: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        checks = self.store.connection_checks()
        ready_channels = [
            item
            for item in checks
            if item.get("readiness") == "READY_FOR_PUBLISH"
            and item.get("logistics") == "HEALTHY"
            and item.get("readback") == "HEALTHY"
        ]
        if not ready_channels:
            return None
        channel = ready_channels[0]["channel"]
        for product in products:
            sku = str(product.get("sku") or product.get("id"))
            if channel == "shopee" and sku == "black-gmt":
                continue
            listing = self.store.listing(channel, sku)
            fields = (
                product.get("fields") if isinstance(product.get("fields"), dict) else {}
            )
            price = product.get("price_brl") or product.get("sale_price")
            if price is None and isinstance(fields.get("sale_price"), dict):
                price = fields["sale_price"].get("value")
            availability = product.get("availability")
            if availability is None and isinstance(fields.get("availability"), dict):
                availability = fields["availability"].get("value")
            if listing and listing["state"] == "DRAFT" and price is not None:
                return self.store.save_publish_plan(
                    {
                        "sku": sku,
                        "product_name": product.get("name"),
                        "channel": channel,
                        "external_category": ready_channels[0].get("external_category"),
                        "required_attributes": ready_channels[0].get(
                            "required_attributes"
                        ),
                        "price": str(price),
                        "availability": availability,
                        "stock_strategy": product.get("inventory_mode"),
                        "shipping": product.get("shipping"),
                        "images": product.get("images") or product.get("image"),
                        "draft_id": listing["id"],
                        "approval_status": "PENDING",
                        "credential_status": ready_channels[0].get("credentials"),
                        "api_health": ready_channels[0].get("auth"),
                        "remaining_blockers": [
                            "PUBLISH_APPROVAL",
                            "BLOCKED_BY_GLOBAL_FLAG",
                        ],
                    }
                )
        return None
