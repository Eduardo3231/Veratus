"""Shopee Open Platform v2: shop authorization and read-only account calls.

Flow: /integrations/shopee/oauth/start signs a link to
``/api/v2/shop/auth_partner``; Shopee sends ``code`` and ``shop_id`` back to
the callback, which trades them at ``/api/v2/auth/token/get`` for an
access_token (4 h) and a refresh_token (30 days). Tokens stay encrypted in the
shared MarketplaceTokenStore under the ``shopee`` channel. Nothing here creates,
changes or pauses a listing.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import unicodedata
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import requests

from .marketplace_clients import MarketplaceApiError

CHANNEL = "shopee"
DEFAULT_HOST = "https://partner.shopeemobile.com"
DEFAULT_REDIRECT = "https://veratus.onrender.com/integrations/shopee/oauth/callback"
AUTH_PATH = "/api/v2/shop/auth_partner"
TOKEN_PATH = "/api/v2/auth/token/get"
REFRESH_PATH = "/api/v2/auth/access_token/get"
SHOP_INFO_PATH = "/api/v2/shop/get_shop_info"
CATEGORY_PATH = "/api/v2/product/get_category"
_AUTH_ERRORS = {"error_auth", "invalid_access_token", "invalid_acceess_token"}


def sign(
    partner_key: str,
    partner_id: int,
    path: str,
    timestamp: int,
    access_token: str = "",
    shop_id: str = "",
) -> str:
    """HMAC-SHA256 of partner_id + path + timestamp (+ access_token + shop_id)."""
    base = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    return hmac.new(
        partner_key.encode("utf-8"), base.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


class ShopeeClient:
    def __init__(
        self,
        partner_id: int,
        partner_key: str,
        *,
        host: str = DEFAULT_HOST,
        session: requests.Session | None = None,
        timeout: int = 20,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not partner_id or not partner_key:
            raise MarketplaceApiError("SHOPEE_CONFIGURATION_INCOMPLETE")
        self.partner_id = int(partner_id)
        self._key = partner_key
        self.host = host.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock

    @classmethod
    def from_env(cls, *, session: requests.Session | None = None) -> ShopeeClient:
        raw_id = os.getenv("SHOPEE_PARTNER_ID", "").strip()
        if not raw_id.isdigit():
            raise MarketplaceApiError("SHOPEE_CONFIGURATION_INCOMPLETE")
        return cls(
            int(raw_id),
            os.getenv("SHOPEE_PARTNER_KEY", "").strip(),
            host=os.getenv("SHOPEE_API_HOST", "").strip() or DEFAULT_HOST,
            session=session,
        )

    def _query(
        self, path: str, access_token: str = "", shop_id: str = ""
    ) -> dict[str, Any]:
        timestamp = int(self.clock())
        query: dict[str, Any] = {
            "partner_id": self.partner_id,
            "timestamp": timestamp,
            "sign": sign(
                self._key, self.partner_id, path, timestamp, access_token, shop_id
            ),
        }
        if access_token:
            query.update(access_token=access_token, shop_id=int(shop_id))
        return query

    @staticmethod
    def _checked(response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            data = {}
        error = str(data.get("error") or "") if isinstance(data, dict) else ""
        # Only Shopee's error code travels; never the body, key or token.
        if error in _AUTH_ERRORS or response.status_code in (401, 403):
            raise MarketplaceApiError("AUTH_INVALID_OR_EXPIRED", http_status=401)
        if response.status_code == 429:
            raise MarketplaceApiError("RATE_LIMITED", http_status=429)
        if not response.ok or error:
            raise MarketplaceApiError(
                f"SHOPEE_{(error or 'remote_api_error').upper()}"[:80],
                http_status=response.status_code,
            )
        return data

    def authorization_url(self, redirect_url: str) -> str:
        query = {**self._query(AUTH_PATH), "redirect": redirect_url}
        return f"{self.host}{AUTH_PATH}?{urlencode(query)}"

    def _token_request(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            f"{self.host}{path}",
            params=self._query(path),
            json={**body, "partner_id": self.partner_id},
            timeout=self.timeout,
        )
        data = self._checked(response)
        if not data.get("access_token"):
            raise MarketplaceApiError("SHOPEE_TOKEN_MISSING")
        return data

    @staticmethod
    def _for_store(data: dict[str, Any], shop_id: str) -> dict[str, Any]:
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_in": data.get("expire_in"),
            "user_id": str(shop_id),
        }

    def exchange_code(self, code: str, shop_id: str) -> dict[str, Any]:
        data = self._token_request(TOKEN_PATH, {"code": code, "shop_id": int(shop_id)})
        return self._for_store(data, shop_id)

    def refresh(self, refresh_token: str, shop_id: str) -> dict[str, Any]:
        data = self._token_request(
            REFRESH_PATH, {"refresh_token": refresh_token, "shop_id": int(shop_id)}
        )
        return self._for_store(data, shop_id)

    def shop_get(
        self,
        path: str,
        access_token: str,
        shop_id: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.session.get(
            f"{self.host}{path}",
            params={**self._query(path, access_token, shop_id), **(params or {})},
            timeout=self.timeout,
        )
        return self._checked(response)


class ShopeeTokenManager:
    """Keeps the shop token fresh; one refresh when it is about to expire."""

    def __init__(self, store: Any, client: ShopeeClient) -> None:
        self.store = store
        self.client = client

    def exchange(self, code: str, shop_id: str) -> Any:
        return self.store.save_tokens(self.client.exchange_code(code, shop_id))

    def refresh(self) -> Any:
        current = self.store.load_tokens()
        if not current.refresh_token or not current.user_id:
            raise MarketplaceApiError("REFRESH_TOKEN_MISSING")
        return self.store.save_tokens(
            self.client.refresh(current.refresh_token, current.user_id)
        )

    def current(self) -> Any:
        from .mercado_livre_oauth import _utcnow

        tokens = self.store.load_tokens()
        if tokens.expires_at and tokens.expires_at <= _utcnow() + timedelta(seconds=60):
            tokens = self.refresh()
        return tokens


class ShopeeReadOnlyClient:
    """Account reads for the Shopee agent, with one refresh-and-retry on 401."""

    def __init__(self, manager: ShopeeTokenManager) -> None:
        self.manager = manager

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        tokens = self.manager.current()
        try:
            return self.manager.client.shop_get(
                path, tokens.access_token, tokens.user_id, params
            )
        except MarketplaceApiError as exc:
            if exc.http_status != 401:
                raise
        tokens = self.manager.refresh()
        return self.manager.client.shop_get(
            path, tokens.access_token, tokens.user_id, params
        )

    def account_identity(self) -> dict[str, Any]:
        data = self._get(SHOP_INFO_PATH)
        info = data.get("response") or data
        return {
            "id": self.manager.store.load_tokens().user_id,
            "shop_name": info.get("shop_name"),
            "region": info.get("region"),
            "status": info.get("status"),
        }

    def discover_categories(self, keyword: str = "relógio") -> list[dict[str, Any]]:
        data = self._get(CATEGORY_PATH, {"language": "pt-br"})
        wanted = _normalize(keyword)
        return [
            {
                "external_category_id": item.get("category_id"),
                "external_category_name": item.get("display_category_name")
                or item.get("original_category_name"),
                "has_children": item.get("has_children"),
                "source": "Shopee get_category",
            }
            for item in (data.get("response") or {}).get("category_list", [])
            if wanted
            in _normalize(
                item.get("display_category_name")
                or item.get("original_category_name")
                or ""
            )
        ]


def shopee_read_client() -> ShopeeReadOnlyClient:
    from .mercado_livre_oauth import MarketplaceTokenStore

    store = MarketplaceTokenStore.from_env(CHANNEL)
    return ShopeeReadOnlyClient(ShopeeTokenManager(store, ShopeeClient.from_env()))
