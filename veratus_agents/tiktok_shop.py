"""TikTok Shop (API 202309): autorização da loja, renovação, pedidos e webhook.

- A loja autoriza o app pelo link do Partner Center (``service_id``); o callback
  troca o ``code`` em ``/api/v2/token/get`` e o token fica cifrado na
  ``MarketplaceTokenStore('tiktok-shop')``. Perto de vencer, ou num 401, ele é
  renovado uma vez em ``/api/v2/token/refresh``. Sem autorização guardada, vale
  o ``TIKTOK_SHOP_ACCESS_TOKEN`` do env (legado).
- Pedidos (``POST /order/202309/orders/search``: POST, mas só leitura, com os
  filtros no corpo) e webhook ficam atrás de ``TIKTOK_SHOP_SYNC_ENABLED``
  (false): desligada, nenhuma chamada sai. Do pedido só voltam ids, status, SKU
  e valores; endereço, e-mail e mensagem do comprador ficam fora.
- O webhook só entra na fila; o pagamento vale só pela leitura do pedido na API.

Nada aqui cria, muda ou pausa anúncio. Link de autorização, códigos de erro e
base da assinatura do webhook: a conferir na hora de ligar.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import requests

from .marketplace_clients import MarketplaceApiError, TikTokShopReadOnlyClient

CHANNEL = "tiktok-shop"
AUTH_HOST = "https://auth.tiktok-shops.com"
AUTHORIZE_URL = "https://services.tiktokshop.com/open/authorize"
TOKEN_PATH = "/api/v2/token/get"
REFRESH_PATH = "/api/v2/token/refresh"
ORDERS_SEARCH_PATH = "/order/202309/orders/search"
_HTTP_ERRORS = {403: "AUTH_FORBIDDEN", 429: "RATE_LIMITED"}
# Token inválido ou vencido no corpo de uma resposta 200.
AUTH_ERROR_CODES = frozenset({105001, 105002})
# Webhook que interessa ao ledger: mudança de status do pedido.
ORDER_STATUS_CHANGE = 1


def sync_enabled() -> bool:
    from .meta_webhooks import env_flag

    return env_flag("TIKTOK_SHOP_SYNC_ENABLED")


def _require_sync() -> None:
    if not sync_enabled():
        raise MarketplaceApiError("SYNC_DISABLED")


def authorization_url(service_id: str, state: str) -> str:
    """Link para a loja autorizar o app; o ``state`` volta no callback."""
    return f"{AUTHORIZE_URL}?{urlencode({'service_id': service_id, 'state': state})}"


def webhook_signature_valid(
    app_key: str, app_secret: str, body: bytes, header: str | None
) -> bool:
    """Authorization = HMAC-SHA256(app_secret, app_key + corpo), em hex."""
    if not app_key or not app_secret or not header:
        return False
    expected = hmac.new(
        app_secret.encode("utf-8"), app_key.encode("utf-8") + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(
        expected.encode("ascii"), header.strip().lower().encode("utf-8")
    )


def webhook_event(payload: Any, app_key: str) -> dict[str, Any] | None:
    """Evento da fila só com ids e status do pedido; ``None`` = ignorar.

    Endereço do comprador (type 3) e os demais tipos nem entram na fila.
    """
    if not isinstance(payload, dict):
        raise TypeError("INVALID_WEBHOOK_PAYLOAD")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    order_id = str(data.get("order_id") or "")
    if payload.get("type") != ORDER_STATUS_CHANGE or not (
        order_id.isdigit() and len(order_id) <= 32
    ):
        return None
    shop_id = str(payload.get("shop_id") or "")
    status = str(data.get("order_status") or "")[:40] or None
    update_time = data.get("update_time")
    basis = str(payload.get("tts_notification_id") or "") or json.dumps(
        [shop_id, order_id, status, update_time], default=str
    )
    return {
        "_id": f"tiktok-shop:{basis}",
        "topic": "order_status",
        "resource": f"/orders/{order_id}",
        "application_id": app_key,
        "user_id": shop_id,
        "status": status,
        "update_time": update_time if isinstance(update_time, int) else None,
    }


def _order_view(item: dict[str, Any]) -> dict[str, Any]:
    """Só o que o ledger usa; o resto da resposta (comprador incluso) fica fora."""
    payment = item.get("payment") or {}
    return {
        "id": str(item.get("id") or ""),
        "status": item.get("status"),
        "create_time": item.get("create_time"),
        "update_time": item.get("update_time"),
        "paid_time": item.get("paid_time"),
        "currency": payment.get("currency"),
        "shipping_fee": payment.get("shipping_fee"),
        # Uma linha por unidade vendida.
        "line_items": [
            {
                "seller_sku": line.get("seller_sku"),
                "sku_id": line.get("sku_id"),
                "original_price": line.get("original_price"),
                "seller_discount": line.get("seller_discount"),
                "is_gift": bool(line.get("is_gift")),
            }
            for line in item.get("line_items") or []
        ],
    }


class TikTokShopClient(TikTokShopReadOnlyClient):
    """Leitura da loja com o token da autorização (renovado) e pedidos."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        access_token: str = "",
        *,
        shop_cipher: str | None = None,
        session: requests.Session | None = None,
        timeout: int = 20,
        token_store: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not app_key or not app_secret:
            raise MarketplaceApiError("TIKTOK_SHOP_CONFIGURATION_INCOMPLETE")
        super().__init__(
            app_key,
            app_secret,
            access_token,
            shop_cipher=shop_cipher,
            session=session,
            timeout=timeout,
        )
        self.token_store = token_store
        self.clock = clock

    # -- autorização ------------------------------------------------------------

    def _token_call(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        _require_sync()
        response = self.session.get(
            f"{AUTH_HOST}{path}",
            params={"app_key": self.app_key, "app_secret": self._secret, **params},
            timeout=self.timeout,
        )
        try:
            data = response.json()
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        body = data.get("data") if isinstance(data.get("data"), dict) else {}
        if not response.ok or data.get("code") != 0 or not body.get("access_token"):
            # Só o código volta; nunca o corpo, a chave ou o token.
            raise MarketplaceApiError("TIKTOK_SHOP_TOKEN_FAILED", http_status=401)
        expires_at = int(body.get("access_token_expire_in") or 0)  # horário Unix
        return {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token"),
            "expires_in": max(0, expires_at - int(self.clock()))
            if expires_at
            else None,
            "user_id": body.get("open_id"),
            "scope": ",".join(map(str, body.get("granted_scopes") or [])) or None,
            "region": body.get("seller_base_region"),
        }

    def exchange_code(self, code: str) -> dict[str, Any]:
        return self._token_call(
            TOKEN_PATH, {"auth_code": code, "grant_type": "authorized_code"}
        )

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._token_call(
            REFRESH_PATH,
            {"refresh_token": refresh_token, "grant_type": "refresh_token"},
        )

    def _access_token(self, *, force_refresh: bool = False) -> str:
        if self.token_store is None:
            return self._token
        from .mercado_livre_oauth import _utcnow

        tokens = self.token_store.load_tokens()
        due = tokens.expires_at and tokens.expires_at <= _utcnow() + timedelta(
            seconds=60
        )
        if force_refresh or due:
            if not tokens.refresh_token:
                raise MarketplaceApiError("REFRESH_TOKEN_MISSING", http_status=401)
            tokens = self.token_store.save_tokens(self.refresh(tokens.refresh_token))
        return tokens.access_token

    # -- leitura ----------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        for attempt in (1, 2):
            query = {
                "app_key": self.app_key,
                "timestamp": int(self.clock()),
                **(params or {}),
            }
            query["sign"] = self._signature(path, query, payload)
            headers = {
                "Content-Type": "application/json",
                "x-tts-access-token": self._access_token(force_refresh=attempt == 2),
            }
            if method == "POST":
                response = self.session.post(
                    f"{self.BASE_URL}{path}",
                    headers=headers,
                    params=query,
                    data=payload,
                    timeout=self.timeout,
                )
            else:
                response = self.session.get(
                    f"{self.BASE_URL}{path}",
                    headers=headers,
                    params=query,
                    timeout=self.timeout,
                )
            data = response.json() if response.ok else {}
            expired = (
                response.status_code == 401 or data.get("code") in AUTH_ERROR_CODES
            )
            if expired and attempt == 1 and self.token_store is not None:
                continue
            if expired:
                raise MarketplaceApiError("AUTH_INVALID_OR_EXPIRED", http_status=401)
            if not response.ok:
                raise MarketplaceApiError(
                    _HTTP_ERRORS.get(response.status_code, "REMOTE_API_ERROR"),
                    http_status=response.status_code,
                )
            if data.get("code") not in (None, 0):
                raise MarketplaceApiError(
                    "REMOTE_API_ERROR", http_status=response.status_code
                )
            return data.get("data", data)
        raise MarketplaceApiError("AUTH_INVALID_OR_EXPIRED", http_status=401)

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def _single_shop_cipher(self) -> str:
        shops = self.account_identity()["shops"]
        if len(shops) != 1 or not shops[0].get("cipher"):
            raise MarketplaceApiError("SHOP_CIPHER_REQUIRED")
        self.shop_cipher = str(shops[0]["cipher"])
        return self.shop_cipher

    def search_orders(
        self, update_time_ge: int, update_time_lt: int, *, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        """Pedidos alterados na janela, do mais antigo para o mais novo."""
        _require_sync()
        cipher = self.shop_cipher or self._single_shop_cipher()
        orders: list[dict[str, Any]] = []
        page_token = ""
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "shop_cipher": cipher,
                "page_size": 50,
                "sort_field": "update_time",
                "sort_order": "ASC",
            }
            if page_token:
                params["page_token"] = page_token
            data = self._request(
                "POST",
                ORDERS_SEARCH_PATH,
                params=params,
                body={
                    "update_time_ge": update_time_ge,
                    "update_time_lt": update_time_lt,
                },
            )
            orders += [_order_view(item) for item in data.get("orders") or []]
            page_token = str(data.get("next_page_token") or "")
            if not page_token:
                break
        return orders


def tiktok_shop_client(*, session: requests.Session | None = None) -> TikTokShopClient:
    """Cliente com o token da autorização guardada; sem ela, o do env (legado)."""
    from .mercado_livre_oauth import (
        MarketplaceTokenStore,
        OAuthConfigurationError,
        TokenStorageError,
    )

    try:
        store = MarketplaceTokenStore.from_env(CHANNEL)
        if not store.has_tokens():
            store = None
    except (OAuthConfigurationError, TokenStorageError):
        store = None
    token = "" if store else os.getenv("TIKTOK_SHOP_ACCESS_TOKEN", "").strip()
    if store is None and not token:
        raise MarketplaceApiError("SHOP_NOT_AUTHORIZED")
    return TikTokShopClient(
        os.getenv("TIKTOK_SHOP_APP_KEY", "").strip(),
        os.getenv("TIKTOK_SHOP_APP_SECRET", "").strip(),
        token,
        shop_cipher=os.getenv("TIKTOK_SHOP_SHOP_CIPHER", "").strip() or None,
        session=session,
        token_store=store,
    )
