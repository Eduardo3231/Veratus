"""Marketplace account connections beyond Mercado Livre: Shopee, TikTok Shop,
Amazon and the TikTok content account.

Registered by integrations/webhook.py. Same contract as the Mercado Livre
routes: start, status, check and orders sync need X-Veratus-Admin-Token; the
callback checks a single-use state; nothing here publishes or changes a listing.

Shopee does not echo an OAuth ``state`` parameter, so the state travels in the
callback path: /integrations/shopee/oauth/callback/<state>?code=...&shop_id=...

Push da Shopee e webhook da TikTok Shop: assinatura conferida antes de tudo (sem
o segredo, 503 para a plataforma tentar de novo; nunca aceita sem assinatura),
depois só entram na fila, sem processar na requisição. Pedidos entram no ledger
só pela leitura da API (``POST /integrations/<canal>/orders/sync``, sem cron).
Cada parte nova fica atrás da sua chave (``*_SYNC_ENABLED``,
``TIKTOK_CONTENT_ENABLED``), todas false.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import requests
from flask import Blueprint, jsonify, redirect, request

from veratus_agents.config import AgentSettings

_CODE = re.compile(r"[A-Za-z0-9._~-]{8,2048}")
# O código do TikTok traz "*" e "!" (a conferir na hora de ligar).
_TIKTOK_CODE = re.compile(r"[A-Za-z0-9._~*!-]{8,2048}")
_SHOP_ID = re.compile(r"\d{1,20}")
_STATE_TTL_SECONDS = 600
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


@lru_cache(maxsize=4)
def _ledger_cached(operations_db: str, database_url: str, key: str):
    from veratus_agents.orders import make_order_ledger

    return make_order_ledger(operations_db, database_url or None, key or None)


def order_ledger():
    """O mesmo Orders Ledger do webhook (banco de operações)."""
    settings = AgentSettings.from_env()
    return _ledger_cached(
        str(settings.operations_db),
        settings.database_url or "",
        os.getenv("VERATUS_TOKEN_ENCRYPTION_KEY", "").strip(),
    )


def _flag(name: str) -> bool:
    from veratus_agents.meta_webhooks import env_flag

    return env_flag(name)


def _no_store(response, status: int = 200):
    response.headers["Cache-Control"] = "no-store"
    return response, status


def _blocked(message: str, status: int = 503):
    return jsonify({"status": "blocked", "message": message}), status


def _error(message: str, status: int):
    return jsonify({"status": "error", "message": message}), status


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report[key] for key in _REPORT_FIELDS if key in report}


def _authorization_response(url: str):
    if (
        request.args.get("format") == "json"
        or request.accept_mimetypes.best == "application/json"
    ):
        return _no_store(jsonify({"status": "ready", "authorization_url": url}))
    return _no_store(redirect(url, code=302), 302)


def _consume_state(channel: str, state: str):
    """(store, None) com o state válido, de uso único; (None, resposta) se não."""
    from veratus_agents.mercado_livre_oauth import (
        OAuthConfigurationError,
        OAuthStateError,
    )

    try:
        store = token_store(channel)
        store.consume_state(state)
    except OAuthStateError:
        return None, _error("invalid_oauth_state", 400)
    except OAuthConfigurationError:
        return None, _blocked("oauth_storage_not_configured")
    except Exception:  # noqa: BLE001 - public boundary sanitizes storage errors
        return None, _blocked("oauth_persistence_unavailable")
    return store, None


def _token_status(channel: str):
    from veratus_agents.mercado_livre_oauth import (
        OAuthConfigurationError,
        TokenStorageError,
    )

    try:
        tokens = token_store(channel).load_tokens(required=False)
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


def _signed_body(configured: bool, verify: Callable[[bytes], bool]):
    """(corpo, None) com assinatura válida; (None, resposta) se não.

    Sem o segredo, 503: a plataforma tenta de novo. O limite de 8 KB do app vale.
    """
    if not configured:
        return None, _blocked("push_secret_not_configured")
    body = request.get_data(cache=True)
    if not verify(body):
        return None, _error("invalid_signature", 401)
    return body, None


def _json_object(body: bytes):
    try:
        payload = json.loads(body)
    except ValueError:
        return None, _error("invalid_json", 400)
    if not isinstance(payload, dict):
        return None, _error("json_object_required", 400)
    return payload, None


def _enqueue(channel: str, event: dict[str, Any] | None):
    """Só entra na fila (deduplicada pelo id); o pedido é lido depois, pela API."""
    if event is None:
        return jsonify({"status": "ignored"}), 200
    try:
        event_id, inserted = token_store(channel).enqueue_notification(event)
    except Exception:  # noqa: BLE001 - 503 faz a plataforma reenviar
        return _blocked("push_storage_unavailable")
    return jsonify(
        {
            "status": "accepted" if inserted else "duplicate",
            "event_id": event_id,
            "processing": "PENDING_ORDERS_SYNC" if inserted else "ALREADY_QUEUED",
        }
    ), 200


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
        return _authorization_response(url)

    @blueprint.get("/integrations/shopee/oauth/callback/<state>")
    def shopee_oauth_callback(state: str):
        from veratus_agents.marketplace_clients import MarketplaceApiError
        from veratus_agents.mercado_livre_oauth import (
            OAuthConfigurationError,
            TokenStorageError,
        )
        from veratus_agents.shopee import ShopeeClient, ShopeeTokenManager

        store, failure = _consume_state("shopee", state)
        if failure:
            return failure
        code = request.args.get("code", "")
        shop_id = request.args.get("shop_id", "")
        if not shop_id and request.args.get("main_account_id"):
            # Main-account authorizations list many shops; the agent reads one.
            return _error("authorize_a_single_shop", 400)
        if not _CODE.fullmatch(code) or not _SHOP_ID.fullmatch(shop_id):
            return _error("invalid_authorization_response", 400)
        try:
            manager = ShopeeTokenManager(store, ShopeeClient.from_env())
            stored = manager.exchange(code, shop_id)
        except (MarketplaceApiError, OAuthConfigurationError, TokenStorageError):
            return _error("oauth_token_exchange_failed", 502)
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
        return _token_status("shopee")

    @blueprint.post("/integrations/shopee/check")
    def shopee_check():
        denied = admin_required()
        if denied:
            return denied
        return _no_store(
            jsonify({"status": "completed", "report": public_report(inspect_shopee())})
        )

    @blueprint.post("/integrations/shopee/push")
    def shopee_push():
        from veratus_agents import shopee

        key = os.getenv("SHOPEE_PARTNER_KEY", "").strip()
        body, failure = _signed_body(
            bool(key),
            lambda raw: shopee.push_signature_valid(
                key, shopee.push_url(), raw, request.headers.get("Authorization")
            ),
        )
        if failure:
            return failure
        if not _flag("SHOPEE_SYNC_ENABLED"):
            return _blocked("shopee_sync_disabled")
        payload, failure = _json_object(body)
        if failure:
            return failure
        return _enqueue(shopee.CHANNEL, shopee.push_event(payload))

    def inspect_amazon() -> dict[str, Any]:
        from veratus_agents.marketplace_service import MarketplaceConnectionService

        return MarketplaceConnectionService(marketplace_store()).inspect_amazon()

    @blueprint.get("/integrations/amazon/status")
    def amazon_status():
        denied = admin_required()
        if denied:
            return denied
        from veratus_agents.amazon_sp import configuration

        configured = configuration()
        last = next(
            (
                item
                for item in marketplace_store().connection_checks()
                if item.get("channel") == "amazon"
            ),
            None,
        )
        return _no_store(
            jsonify(
                {
                    "status": "configured"
                    if all(configured.values())
                    else "not_configured",
                    "configuration": configured,
                    "last_check": public_report(last) if last else None,
                    "publish_enabled": False,
                    "external_writes": False,
                }
            )
        )

    @blueprint.post("/integrations/amazon/check")
    def amazon_check():
        denied = admin_required()
        if denied:
            return denied
        return _no_store(
            jsonify({"status": "completed", "report": public_report(inspect_amazon())})
        )

    # -- TikTok Shop --------------------------------------------------------------

    def inspect_tiktok_shop() -> dict[str, Any]:
        from veratus_agents.marketplace_clients import connection_status
        from veratus_agents.marketplace_service import MarketplaceConnectionService
        from veratus_agents.tiktok_shop import tiktok_shop_client

        service = MarketplaceConnectionService(
            marketplace_store(), tiktok_factory=tiktok_shop_client
        )
        # A checagem do serviço, com o token da autorização guardada.
        return service._inspect_tiktok([], connection_status()["tiktok-shop"])

    @blueprint.get("/integrations/tiktok-shop/oauth/start")
    def tiktok_shop_oauth_start():
        denied = admin_required()
        if denied:
            return denied
        from veratus_agents import tiktok_shop
        from veratus_agents.mercado_livre_oauth import OAuthConfigurationError

        if not tiktok_shop.sync_enabled():
            return _blocked("tiktok_shop_sync_disabled")
        service_id = os.getenv("TIKTOK_SHOP_SERVICE_ID", "").strip()
        keys = ("TIKTOK_SHOP_APP_KEY", "TIKTOK_SHOP_APP_SECRET")
        if not service_id or not all(os.getenv(name, "").strip() for name in keys):
            return _blocked("oauth_configuration_incomplete")
        try:
            state = token_store(tiktok_shop.CHANNEL).create_state(_STATE_TTL_SECONDS)
        except OAuthConfigurationError:
            return _blocked("oauth_configuration_incomplete")
        except Exception:  # noqa: BLE001 - public boundary sanitizes storage errors
            return _blocked("oauth_persistence_unavailable")
        return _authorization_response(tiktok_shop.authorization_url(service_id, state))

    @blueprint.get("/integrations/tiktok-shop/oauth/callback")
    def tiktok_shop_oauth_callback():
        from veratus_agents import tiktok_shop
        from veratus_agents.marketplace_clients import MarketplaceApiError
        from veratus_agents.mercado_livre_oauth import (
            OAuthConfigurationError,
            TokenStorageError,
        )

        if not tiktok_shop.sync_enabled():
            return _blocked("tiktok_shop_sync_disabled")
        store, failure = _consume_state(
            tiktok_shop.CHANNEL, request.args.get("state", "")
        )
        if failure:
            return failure
        code = request.args.get("code", "")
        if not _CODE.fullmatch(code):
            return _error("invalid_authorization_response", 400)
        try:
            client = tiktok_shop.TikTokShopClient(
                os.getenv("TIKTOK_SHOP_APP_KEY", "").strip(),
                os.getenv("TIKTOK_SHOP_APP_SECRET", "").strip(),
            )
            granted = client.exchange_code(code)
            stored = store.save_tokens(granted)
        except (
            MarketplaceApiError,
            OAuthConfigurationError,
            TokenStorageError,
            requests.RequestException,
        ):
            return _error("oauth_token_exchange_failed", 502)
        try:
            report = inspect_tiktok_shop()
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
                    # Confirma se a loja autorizada é do Brasil (BR).
                    "seller_region": granted.get("region"),
                    "connection_health": "HEALTHY" if connected else "NOT_READY",
                    "publish_enabled": False,
                    "external_writes": False,
                }
            )
        )

    @blueprint.get("/integrations/tiktok-shop/status")
    def tiktok_shop_status():
        denied = admin_required()
        if denied:
            return denied
        return _token_status("tiktok-shop")

    @blueprint.post("/integrations/tiktok-shop/check")
    def tiktok_shop_check():
        denied = admin_required()
        if denied:
            return denied
        from veratus_agents.tiktok_shop import sync_enabled

        if not sync_enabled():
            return _blocked("tiktok_shop_sync_disabled")
        try:
            report = inspect_tiktok_shop()
        except Exception:  # noqa: BLE001 - token ilegível, banco ou rede fora
            return _blocked("check_unavailable")
        return _no_store(
            jsonify({"status": "completed", "report": public_report(report)})
        )

    @blueprint.post("/integrations/tiktok-shop/webhook")
    def tiktok_shop_webhook():
        from veratus_agents import tiktok_shop

        app_key = os.getenv("TIKTOK_SHOP_APP_KEY", "").strip()
        secret = os.getenv("TIKTOK_SHOP_APP_SECRET", "").strip()
        body, failure = _signed_body(
            bool(app_key and secret),
            lambda raw: tiktok_shop.webhook_signature_valid(
                app_key, secret, raw, request.headers.get("Authorization")
            ),
        )
        if failure:
            return failure
        if not tiktok_shop.sync_enabled():
            return _blocked("tiktok_shop_sync_disabled")
        payload, failure = _json_object(body)
        if failure:
            return failure
        return _enqueue(
            tiktok_shop.CHANNEL, tiktok_shop.webhook_event(payload, app_key)
        )

    # -- pedidos (Shopee, TikTok Shop, Amazon) -----------------------------------

    @blueprint.post(
        "/integrations/<any(shopee, amazon, 'tiktok-shop'):channel>/orders/sync"
    )
    def orders_sync(channel: str):
        denied = admin_required()
        if denied:
            return denied
        from veratus_agents.marketplace_orders import sync_orders

        try:
            hours = int(request.args.get("hours", "24"))
        except ValueError:
            return _error("invalid_hours", 400)
        try:
            result = sync_orders(channel, order_ledger(), hours=hours)
        except Exception:  # noqa: BLE001 - public boundary sanitizes storage errors
            return _blocked("orders_sync_unavailable")
        return _no_store(jsonify(result))

    # -- TikTok (conteúdo, caixa de entrada do criador) ---------------------------

    def inspect_tiktok_content(client: Any = None) -> dict[str, Any]:
        from veratus_agents import tiktok_content
        from veratus_agents.marketplace_clients import MarketplaceApiError

        report: dict[str, Any] = {"channel": "tiktok", "api_reads": [], "errors": []}
        try:
            client = client or tiktok_content.TikTokContentClient.from_env(
                store=token_store(tiktok_content.CHANNEL)
            )
            creator = client.creator_info()
            report["api_reads"].append("POST /v2/post/publish/creator_info/query/")
            report.update(
                auth="HEALTHY",
                account="HEALTHY",
                account_info=creator,
                readiness="CONNECTED_READ_ONLY",
            )
        except MarketplaceApiError as exc:
            report.update(auth="INVALID", readiness="NOT_READY")
            report["errors"].append(exc.code)
        except (RuntimeError, requests.RequestException):
            report.update(auth="MISSING", readiness="NOT_READY")
            report["errors"].append("NOT_CONNECTED")
        return report

    @blueprint.get("/integrations/tiktok/oauth/start")
    def tiktok_oauth_start():
        denied = admin_required()
        if denied:
            return denied
        from veratus_agents import tiktok_content
        from veratus_agents.marketplace_clients import MarketplaceApiError
        from veratus_agents.mercado_livre_oauth import OAuthConfigurationError

        if not tiktok_content.content_enabled():
            return _blocked("tiktok_content_disabled")
        try:
            store = token_store(tiktok_content.CHANNEL)
            client = tiktok_content.TikTokContentClient.from_env(store=store)
            url = client.authorization_url(store.create_state(_STATE_TTL_SECONDS))
        except (MarketplaceApiError, OAuthConfigurationError):
            return _blocked("oauth_configuration_incomplete")
        except Exception:  # noqa: BLE001 - public boundary sanitizes storage errors
            return _blocked("oauth_persistence_unavailable")
        return _authorization_response(url)

    @blueprint.get("/integrations/tiktok/oauth/callback")
    def tiktok_oauth_callback():
        from veratus_agents import tiktok_content
        from veratus_agents.marketplace_clients import MarketplaceApiError
        from veratus_agents.mercado_livre_oauth import (
            OAuthConfigurationError,
            TokenStorageError,
        )

        if not tiktok_content.content_enabled():
            return _blocked("tiktok_content_disabled")
        store, failure = _consume_state(
            tiktok_content.CHANNEL, request.args.get("state", "")
        )
        if failure:
            return failure
        code = request.args.get("code", "")
        if request.args.get("error") or not _TIKTOK_CODE.fullmatch(code):
            return _error("invalid_authorization_response", 400)
        try:
            client = tiktok_content.TikTokContentClient.from_env(store=store)
            stored = client.exchange_code(code)
        except (
            MarketplaceApiError,
            OAuthConfigurationError,
            TokenStorageError,
            requests.RequestException,
        ):
            return _error("oauth_token_exchange_failed", 502)
        connected = (
            inspect_tiktok_content(client).get("readiness") == "CONNECTED_READ_ONLY"
        )
        return _no_store(
            jsonify(
                {
                    "status": "connected_inbox_only"
                    if connected
                    else "authorized_check_pending",
                    "token": stored.public_status(),
                    "direct_post": False,
                    "external_writes": False,
                }
            )
        )

    @blueprint.get("/integrations/tiktok/status")
    def tiktok_status():
        denied = admin_required()
        if denied:
            return denied
        return _token_status("tiktok-content")

    @blueprint.post("/integrations/tiktok/check")
    def tiktok_check():
        denied = admin_required()
        if denied:
            return denied
        from veratus_agents.tiktok_content import content_enabled

        if not content_enabled():
            return _blocked("tiktok_content_disabled")
        report = public_report(inspect_tiktok_content())
        return _no_store(jsonify({"status": "completed", "report": report}))

    return blueprint
