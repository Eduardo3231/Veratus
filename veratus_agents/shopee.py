"""Shopee Open Platform v2: shop authorization and read-only account calls.

Flow: /integrations/shopee/oauth/start signs a link to
``/api/v2/shop/auth_partner``; Shopee sends ``code`` and ``shop_id`` back to
the callback, which trades them at ``/api/v2/auth/token/get`` for an
access_token (4 h) and a refresh_token (30 days). Tokens stay encrypted in the
shared MarketplaceTokenStore under the ``shopee`` channel. Nothing here creates,
changes or pauses a listing.

Pedidos e anúncios (get_order_list, get_order_detail, get_item_list,
get_item_base_info) e a push ficam atrás de ``SHOPEE_SYNC_ENABLED`` (false):
desligada, nenhuma chamada sai. Do pedido só voltam ids, SKU, quantidade,
valores e status; nada do comprador é pedido nem guardado. Formatos da API e da
push: a conferir na hora de ligar.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
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
ORDER_LIST_PATH = "/api/v2/order/get_order_list"
ORDER_DETAIL_PATH = "/api/v2/order/get_order_detail"
ITEM_LIST_PATH = "/api/v2/product/get_item_list"
ITEM_BASE_INFO_PATH = "/api/v2/product/get_item_base_info"
PUSH_PATH = "/integrations/shopee/push"
# Campos opcionais do detalhe: nada de buyer_*, recipient_address, CPF ou nota.
ORDER_DETAIL_FIELDS = "item_list,pay_time,total_amount"
MAX_WINDOW_SECONDS = 15 * 24 * 3600  # janela máxima do get_order_list
_ORDER_SN = re.compile(r"[A-Za-z0-9]{6,40}")
# Push que interessa ao ledger: status (3) e rastreio (4) do pedido.
_PUSH_TOPICS = {3: "order_status", 4: "order_tracking"}
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


def _require_sync() -> None:
    from .meta_webhooks import env_flag

    if not env_flag("SHOPEE_SYNC_ENABLED"):
        raise MarketplaceApiError("SYNC_DISABLED")


def push_url() -> str:
    """URL cadastrada no Shopee Open Platform; ela entra na assinatura da push."""
    base = os.getenv("PUBLIC_BASE_URL", "").strip() or "https://veratus.onrender.com"
    return base.rstrip("/") + PUSH_PATH


def push_signature_valid(
    partner_key: str, url: str, body: bytes, header: str | None
) -> bool:
    """Authorization = HMAC-SHA256(partner_key, url + "|" + corpo), em hex.

    Comparação em tempo constante. Base da assinatura: a conferir na hora de ligar.
    """
    if not partner_key or not header:
        return False
    expected = hmac.new(
        partner_key.encode("utf-8"), url.encode("utf-8") + b"|" + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(
        expected.encode("ascii"), header.strip().lower().encode("utf-8")
    )


def push_event(payload: Any) -> dict[str, Any] | None:
    """Evento da fila só com código, loja, pedido e status; ``None`` = ignorar.

    O pagamento nunca sai daqui: o status vale só depois da leitura do pedido na
    API. Reenvio da mesma push tem o mesmo id, e a fila não duplica.
    """
    if not isinstance(payload, dict):
        raise TypeError("INVALID_PUSH_PAYLOAD")
    code = payload.get("code")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    order_sn = str(data.get("ordersn") or "")
    topic = _PUSH_TOPICS.get(code) if isinstance(code, int) else None
    if topic is None or not _ORDER_SN.fullmatch(order_sn):
        return None
    shop_id = str(payload.get("shop_id") or "")
    basis = json.dumps(
        {"code": code, "shop_id": shop_id, "data": data}, sort_keys=True, default=str
    )
    update_time = data.get("update_time")
    return {
        "_id": "shopee:" + hashlib.sha256(basis.encode("utf-8")).hexdigest(),
        "topic": topic,
        "resource": f"/orders/{order_sn}",
        "application_id": os.getenv("SHOPEE_PARTNER_ID", "").strip(),
        "user_id": shop_id,
        "status": str(data.get("status") or "")[:40] or None,
        "update_time": update_time if isinstance(update_time, int) else None,
    }


def _order_view(item: dict[str, Any]) -> dict[str, Any]:
    """Só o que o ledger usa; o resto da resposta (comprador incluso) fica fora."""
    return {
        "order_sn": str(item.get("order_sn") or ""),
        "order_status": item.get("order_status"),
        "currency": item.get("currency"),
        "create_time": item.get("create_time"),
        "update_time": item.get("update_time"),
        "pay_time": item.get("pay_time"),
        "total_amount": item.get("total_amount"),
        "items": [
            {
                "item_id": line.get("item_id"),
                "model_id": line.get("model_id"),
                "sku": line.get("model_sku") or line.get("item_sku"),
                "quantity": line.get("model_quantity_purchased"),
                "original_price": line.get("model_original_price"),
                "discounted_price": line.get("model_discounted_price"),
            }
            for line in item.get("item_list") or []
        ],
    }


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

    def get_order_list(
        self, time_from: int, time_to: int, *, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        """Pedidos alterados na janela (update_time): só order_sn e status."""
        _require_sync()
        if not 0 < time_to - time_from <= MAX_WINDOW_SECONDS:
            raise MarketplaceApiError("SHOPEE_ORDER_WINDOW_INVALID")
        orders: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(max_pages):
            body = (
                self._get(
                    ORDER_LIST_PATH,
                    {
                        "time_range_field": "update_time",
                        "time_from": time_from,
                        "time_to": time_to,
                        "page_size": 100,
                        "cursor": cursor,
                        "response_optional_fields": "order_status",
                    },
                ).get("response")
                or {}
            )
            orders += [
                {
                    "order_sn": str(item["order_sn"]),
                    "order_status": item.get("order_status"),
                }
                for item in body.get("order_list") or []
                if _ORDER_SN.fullmatch(str(item.get("order_sn") or ""))
            ]
            cursor = str(body.get("next_cursor") or "")
            if not body.get("more") or not cursor:
                break
        return orders

    def get_order_detail(self, order_sns: list[str]) -> list[dict[str, Any]]:
        """Detalhe sem dado do comprador: ids, SKU, quantidade, valores e status."""
        _require_sync()
        wanted = [sn for sn in order_sns if _ORDER_SN.fullmatch(sn)]
        details: list[dict[str, Any]] = []
        for start in range(0, len(wanted), 50):
            data = self._get(
                ORDER_DETAIL_PATH,
                {
                    "order_sn_list": ",".join(wanted[start : start + 50]),
                    "response_optional_fields": ORDER_DETAIL_FIELDS,
                },
            )
            details += [
                _order_view(item)
                for item in (data.get("response") or {}).get("order_list") or []
            ]
        return details

    def get_item_list(
        self, *, item_status: str = "NORMAL", max_pages: int = 5
    ) -> list[dict[str, Any]]:
        """Anúncios da loja (id, status e atualização), só leitura."""
        _require_sync()
        items: list[dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            body = (
                self._get(
                    ITEM_LIST_PATH,
                    {"offset": offset, "page_size": 100, "item_status": [item_status]},
                ).get("response")
                or {}
            )
            items += [
                {
                    "item_id": item.get("item_id"),
                    "item_status": item.get("item_status"),
                    "update_time": item.get("update_time"),
                }
                for item in body.get("item") or []
            ]
            if not body.get("has_next_page"):
                break
            offset = int(body.get("next_offset") or 0)
        return items

    def get_item_base_info(self, item_ids: list[int]) -> list[dict[str, Any]]:
        """SKU e status de cada anúncio (leitura de volta), 50 por chamada."""
        _require_sync()
        ids = [str(item_id) for item_id in item_ids if isinstance(item_id, int)]
        found: list[dict[str, Any]] = []
        for start in range(0, len(ids), 50):
            data = self._get(
                ITEM_BASE_INFO_PATH, {"item_id_list": ",".join(ids[start : start + 50])}
            )
            found += [
                {
                    "item_id": item.get("item_id"),
                    "item_sku": item.get("item_sku"),
                    "item_status": item.get("item_status"),
                    "category_id": item.get("category_id"),
                }
                for item in (data.get("response") or {}).get("item_list") or []
            ]
        return found


def shopee_read_client() -> ShopeeReadOnlyClient:
    from .mercado_livre_oauth import MarketplaceTokenStore

    store = MarketplaceTokenStore.from_env(CHANNEL)
    return ShopeeReadOnlyClient(ShopeeTokenManager(store, ShopeeClient.from_env()))
