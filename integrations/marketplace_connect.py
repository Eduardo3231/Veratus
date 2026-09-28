"""Marketplace account connections beyond Mercado Livre (read-only).

Registered by integrations/webhook.py. Same contract as the Mercado Livre
routes: start, status and check need X-Veratus-Admin-Token; the callback checks
a single-use state; nothing here publishes or changes a listing.

Shopee does not echo an OAuth ``state`` parameter, so the state travels in the
callback path: /integrations/shopee/oauth/callback/<state>?code=...&shop_id=...
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from flask import Blueprint, jsonify, redirect, request

from veratus_agents.config import AgentSettings

_CODE = re.compile(r"[A-Za-z0-9._~-]{8,2048}")
_SHOP_ID = re.compile(r"\d{1,20}")
# Only these report fields leave the server; tokens and keys never do.
_REPORT_FIELDS = (
    "channel",
    "readiness",
    "auth",
    "account",
    "account_info",
    "category_discovery",
    "external_category",
    "category_candidates",
    "api_reads",
    "errors",
    "checked_at",
)


@lru_cache(maxsize=8)
def _store_cached(channel: str, database_url: str, runtime_dir: str, key: str):
    from veratus_agents.mercado_livre_oauth import MarketplaceTokenStore

    return MarketplaceTokenStore(
        encryption_key=key,
        database_url=database_url or None,
        sqlite_path=os.path.join(runtime_dir, "mercado-livre-oauth.sqlite3"),
        channel=channel,
    )


def token_store(channel: str):
    settings = AgentSettings.from_env()
    return _store_cached(
        channel,
        os.getenv("DATABASE_URL", "").strip(),
        str(settings.runtime_dir),
        os.getenv("VERATUS_TOKEN_ENCRYPTION_KEY", "").strip(),
    )


def _no_store(response, status: int = 200):
    response.headers["Cache-Control"] = "no-store"
    return response, status


def _blocked(message: str, status: int = 503):
    return jsonify({"status": "blocked", "message": message}), status


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report[key] for key in _REPORT_FIELDS if key in report}


def create_blueprint(
    admin_required: Callable[[], Any], marketplace_store: Callable[[], Any]
) -> Blueprint:
    blueprint = Blueprint("marketplace_connect", __name__)

    def inspect_shopee() -> dict[str, Any]:
        from veratus_agents.marketplace_service import MarketplaceConnectionService

        return MarketplaceConnectionService(marketplace_store()).inspect_shopee()

    @blueprint.get("/integrations/shopee/oauth/start")
    def shopee_oauth_start():
        denied = admin_required()
        if denied:
            return denied
        from veratus_agents.marketplace_clients import MarketplaceApiError
        from veratus_agents.mercado_livre_oauth import OAuthConfigurationError
        from veratus_agents.shopee import DEFAULT_REDIRECT, ShopeeClient

        try:
            store = token_store("shopee")
            client = ShopeeClient.from_env()
            state = store.create_state(
                int(os.getenv("SHOPEE_OAUTH_STATE_TTL_SECONDS", "600"))
            )
            base = os.getenv("SHOPEE_REDIRECT_URI", "").strip() or DEFAULT_REDIRECT
            url = client.authorization_url(f"{base.rstrip('/')}/{state}")
        except (OAuthConfigurationError, MarketplaceApiError, ValueError):
            return _blocked("oauth_configuration_incomplete")
        except Exception:  # noqa: BLE001 - public boundary sanitizes storage errors
            return _blocked("oauth_persistence_unavailable")
        if (
            request.args.get("format") == "json"
            or request.accept_mimetypes.best == "application/json"
        ):
            return _no_store(jsonify({"status": "ready", "authorization_url": url}))
        return _no_store(redirect(url, code=302), 302)

    @blueprint.get("/integrations/shopee/oauth/callback/<state>")
    def shopee_oauth_callback(state: str):
        from veratus_agents.marketplace_clients import MarketplaceApiError
        from veratus_agents.mercado_livre_oauth import (
            OAuthConfigurationError,
            OAuthStateError,
            TokenStorageError,
        )
        from veratus_agents.shopee import ShopeeClient, ShopeeTokenManager

        try:
            store = token_store("shopee")
            store.consume_state(state)
        except OAuthStateError:
            return jsonify({"status": "error", "message": "invalid_oauth_state"}), 400
        except OAuthConfigurationError:
            return _blocked("oauth_storage_not_configured")
        except Exception:  # noqa: BLE001 - public boundary sanitizes storage errors
            return _blocked("oauth_persistence_unavailable")
        code = request.args.get("code", "")
        shop_id = request.args.get("shop_id", "")
        if not shop_id and request.args.get("main_account_id"):
            # Main-account authorizations list many shops; the agent reads one.
            return jsonify(
                {"status": "error", "message": "authorize_a_single_shop"}
            ), 400
        if not _CODE.fullmatch(code) or not _SHOP_ID.fullmatch(shop_id):
            return jsonify(
                {"status": "error", "message": "invalid_authorization_response"}
            ), 400
        try:
            manager = ShopeeTokenManager(store, ShopeeClient.from_env())
            stored = manager.exchange(code, shop_id)
        except (MarketplaceApiError, OAuthConfigurationError, TokenStorageError):
            return jsonify(
                {"status": "error", "message": "oauth_token_exchange_failed"}
            ), 502
        try:
            report = inspect_shopee()
        except Exception:  # noqa: BLE001 - the token is saved; the check can be rerun
            report = {}
        connected = report.get("readiness") == "CONNECTED_READ_ONLY"
        return _no_store(
            jsonify(
                {
                    "status": "connected_read_only"
                    if connected
                    else "authorized_check_pending",
                    "token": stored.public_status(),
                    "connection_health": "HEALTHY" if connected else "NOT_READY",
                    "publish_enabled": False,
                    "external_writes": False,
                }
            )
        )

    @blueprint.get("/integrations/shopee/status")
    def shopee_status():
        denied = admin_required()
        if denied:
            return denied
        from veratus_agents.mercado_livre_oauth import (
            OAuthConfigurationError,
            TokenStorageError,
        )

        try:
            tokens = token_store("shopee").load_tokens(required=False)
        except (OAuthConfigurationError, TokenStorageError):
            return _blocked("oauth_storage_not_configured")
        except Exception:  # noqa: BLE001 - public boundary sanitizes storage errors
            return _blocked("oauth_persistence_unavailable")
        return _no_store(
            jsonify(
                {
                    "status": "authorized" if tokens else "not_authorized",
                    "token": tokens.public_status() if tokens else {"stored": False},
                    "publish_enabled": False,
                    "external_writes": False,
                }
            )
        )

    @blueprint.post("/integrations/shopee/check")
    def shopee_check():
        denied = admin_required()
        if denied:
            return denied
        return _no_store(
            jsonify({"status": "completed", "report": public_report(inspect_shopee())})
        )

    return blueprint
