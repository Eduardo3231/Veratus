from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlencode

import requests


class ConnectionStatus(StrEnum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    INVALID = "INVALID"
    NOT_TESTED = "NOT_TESTED"
    HEALTHY = "HEALTHY"
    EXPIRED = "EXPIRED"


class MarketplaceApiError(RuntimeError):
    def __init__(self, code: str, *, http_status: int | None = None):
        self.code = code
        self.http_status = http_status
        super().__init__(code)


@dataclass(frozen=True)
class CredentialRequirement:
    channel: str
    variable: str
    purpose: str
    required_for_read: bool
    obtain_at: str


CREDENTIAL_REQUIREMENTS = (
    CredentialRequirement(
        "mercado-livre",
        "MERCADO_LIVRE_CLIENT_ID",
        "OAuth application ID",
        False,
        "Mercado Livre Developers > aplicação",
    ),
    CredentialRequirement(
        "mercado-livre",
        "MERCADO_LIVRE_CLIENT_SECRET",
        "OAuth client secret/refresh",
        False,
        "Mercado Livre Developers > aplicação",
    ),
    CredentialRequirement(
        "mercado-livre",
        "MERCADO_LIVRE_ACCESS_TOKEN",
        "Bearer token para leituras da conta",
        True,
        "OAuth Authorization Code da aplicação Mercado Livre",
    ),
    CredentialRequirement(
        "mercado-livre",
        "MERCADO_LIVRE_REFRESH_TOKEN",
        "Renovar access token expirado",
        False,
        "Resposta OAuth do Mercado Livre",
    ),
    CredentialRequirement(
        "mercado-livre",
        "MERCADO_LIVRE_REDIRECT_URI",
        "Callback OAuth cadastrado",
        False,
        "Mesmo redirect URI da aplicação Mercado Livre",
    ),
    CredentialRequirement(
        "shopee",
        "SHOPEE_PARTNER_ID",
        "Identificador do parceiro Open Platform",
        True,
        "Shopee Open Platform Console",
    ),
    CredentialRequirement(
        "shopee",
        "SHOPEE_PARTNER_KEY",
        "Assinatura das requisições",
        True,
        "Shopee Open Platform Console",
    ),
    CredentialRequirement(
        "shopee",
        "SHOPEE_ACCESS_TOKEN",
        "Token da loja autorizada",
        True,
        "OAuth da Shopee Open Platform",
    ),
    CredentialRequirement(
        "shopee",
        "SHOPEE_REFRESH_TOKEN",
        "Renovar token da loja",
        False,
        "Resposta OAuth da Shopee Open Platform",
    ),
    CredentialRequirement(
        "shopee",
        "SHOPEE_SHOP_ID",
        "Escopo da loja",
        True,
        "Autorização/get shop info da Shopee",
    ),
    CredentialRequirement(
        "tiktok-shop",
        "TIKTOK_SHOP_APP_KEY",
        "Identificador da aplicação",
        True,
        "TikTok Shop Partner Center > App credentials",
    ),
    CredentialRequirement(
        "tiktok-shop",
        "TIKTOK_SHOP_APP_SECRET",
        "Assinatura HMAC das requisições",
        True,
        "TikTok Shop Partner Center > App credentials",
    ),
    CredentialRequirement(
        "tiktok-shop",
        "TIKTOK_SHOP_ACCESS_TOKEN",
        "Token seller para leituras",
        True,
        "TikTok Shop seller authorization",
    ),
    CredentialRequirement(
        "tiktok-shop",
        "TIKTOK_SHOP_REFRESH_TOKEN",
        "Renovar access token",
        False,
        "Resposta da autorização TikTok Shop",
    ),
    CredentialRequirement(
        "tiktok-shop",
        "TIKTOK_SHOP_SHOP_CIPHER",
        "Contexto da loja em chamadas de produto",
        False,
        "Get Authorized Shops",
    ),
    CredentialRequirement(
        "meta",
        "META_ACCESS_TOKEN",
        "Token de acesso Graph API",
        True,
        "Meta for Developers/Business Manager",
    ),
    CredentialRequirement(
        "meta",
        "META_BUSINESS_ID",
        "Identidade do negócio",
        True,
        "Meta Business Settings",
    ),
    CredentialRequirement(
        "meta",
        "META_CATALOG_ID",
        "Catálogo de produtos a consultar",
        True,
        "Commerce Manager",
    ),
)


def credential_matrix() -> list[dict[str, Any]]:
    return [
        {
            **asdict(item),
            "status": ConnectionStatus.PRESENT.value
            if os.getenv(item.variable, "").strip()
            else ConnectionStatus.MISSING.value,
        }
        for item in CREDENTIAL_REQUIREMENTS
    ]


def _safe_error(response: requests.Response) -> MarketplaceApiError:
    if response.status_code == 401:
        return MarketplaceApiError("AUTH_INVALID_OR_EXPIRED", http_status=401)
    if response.status_code == 403:
        return MarketplaceApiError("AUTH_FORBIDDEN", http_status=403)
    if response.status_code == 429:
        return MarketplaceApiError("RATE_LIMITED", http_status=429)
    return MarketplaceApiError("REMOTE_API_ERROR", http_status=response.status_code)


class MercadoLivreReadOnlyClient:
    BASE_URL = "https://api.mercadolibre.com"

    def __init__(
        self,
        access_token: str,
        *,
        session: requests.Session | None = None,
        timeout: int = 20,
    ):
        self._token = access_token
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = self.session.get(
            f"{self.BASE_URL}{path}",
            headers={"Authorization": f"Bearer {self._token}"},
            params=params,
            timeout=self.timeout,
        )
        if not response.ok:
            raise _safe_error(response)
        return response.json()

    def account_identity(self) -> dict[str, Any]:
        data = self._get("/users/me")
        return {
            "id": data.get("id"),
            "site_id": data.get("site_id"),
            "status": data.get("status", {}).get("site_status"),
        }

    def health_check(self) -> dict[str, Any]:
        account = self.account_identity()
        return {
            "status": "HEALTHY",
            "account_reachable": bool(account.get("id")),
            "account": account,
        }

    def discover_categories(
        self, title: str, *, site_id: str = "MLB"
    ) -> list[dict[str, Any]]:
        data = self._get(
            f"/sites/{site_id}/domain_discovery/search", params={"q": title, "limit": 3}
        )
        return [
            {
                "external_category_id": item.get("category_id"),
                "external_category_name": item.get("category_name"),
                "domain_id": item.get("domain_id"),
                "source": "Mercado Livre domain_discovery",
            }
            for item in data
        ]

    def discover_attributes(self, category_id: str) -> list[dict[str, Any]]:
        data = self._get(f"/categories/{category_id}/attributes")
        result = []
        for item in data:
            tags = item.get("tags") or {}
            classification = (
                "REQUIRED"
                if tags.get("required")
                else "CONDITIONAL"
                if tags.get("conditional_required")
                else "OPTIONAL"
            )
            result.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "classification": classification,
                    "value_type": item.get("value_type"),
                }
            )
        return result

    def lookup_listing(self, account_id: str, sku: str) -> dict[str, Any]:
        data = self._get(
            f"/users/{account_id}/items/search", params={"seller_sku": sku}
        )
        return {
            "sku": sku,
            "remote_ids": data.get("results", []),
            "total": data.get("paging", {}).get("total", 0),
        }

    @staticmethod
    def exchange_authorization_code(
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        code: str,
        session: requests.Session | None = None,
        timeout: int = 20,
    ) -> dict[str, Any]:
        response = (session or requests.Session()).post(
            f"{MercadoLivreReadOnlyClient.BASE_URL}/oauth/token",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=timeout,
        )
        if not response.ok:
            raise _safe_error(response)
        return response.json()

    @staticmethod
    def refresh_token(
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        session: requests.Session | None = None,
        timeout: int = 20,
    ) -> dict[str, Any]:
        response = (session or requests.Session()).post(
            f"{MercadoLivreReadOnlyClient.BASE_URL}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
            timeout=timeout,
        )
        if not response.ok:
            raise _safe_error(response)
        return response.json()


class MercadoLivreOAuthTokenManager:
    AUTH_URL = "https://auth.mercadolivre.com.br/authorization"

    def __init__(
        self,
        store: Any,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        session: requests.Session | None = None,
        timeout: int = 20,
    ) -> None:
        if not client_id or not client_secret or not redirect_uri:
            raise MarketplaceApiError("OAUTH_CONFIGURATION_INCOMPLETE")
        self.store = store
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.session = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def from_env(
        cls,
        store: Any,
        *,
        session: requests.Session | None = None,
        timeout: int = 20,
    ) -> MercadoLivreOAuthTokenManager:
        return cls(
            store,
            client_id=os.getenv("MERCADO_LIVRE_CLIENT_ID", "").strip(),
            client_secret=os.getenv("MERCADO_LIVRE_CLIENT_SECRET", "").strip(),
            redirect_uri=os.getenv("MERCADO_LIVRE_REDIRECT_URI", "").strip(),
            session=session,
            timeout=timeout,
        )

    def authorization_url(self, state: str) -> str:
        return f"{self.AUTH_URL}?{urlencode({'response_type': 'code', 'client_id': self.client_id, 'redirect_uri': self.redirect_uri, 'state': state})}"

    def exchange_code(self, code: str) -> Any:
        tokens = MercadoLivreReadOnlyClient.exchange_authorization_code(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=self.redirect_uri,
            code=code,
            session=self.session,
            timeout=self.timeout,
        )
        return self.store.save_tokens(tokens)

    def refresh(self) -> Any:
        current = self.store.load_tokens()
        if not current.refresh_token:
            raise MarketplaceApiError("REFRESH_TOKEN_MISSING")
        tokens = MercadoLivreReadOnlyClient.refresh_token(
            client_id=self.client_id,
            client_secret=self.client_secret,
            refresh_token=current.refresh_token,
            session=self.session,
            timeout=self.timeout,
        )
        return self.store.save_tokens(tokens)

    def access_token(self) -> str:
        from datetime import timedelta

        from .mercado_livre_oauth import _utcnow

        current = self.store.load_tokens()
        if current.expires_at and current.expires_at <= _utcnow() + timedelta(
            seconds=60
        ):
            current = self.refresh()
        return current.access_token

    def client(self) -> MercadoLivreOAuthReadOnlyClient:
        return MercadoLivreOAuthReadOnlyClient(self)


class MercadoLivreOAuthReadOnlyClient(MercadoLivreReadOnlyClient):
    """Read-only client with one controlled refresh-and-retry on HTTP 401."""

    def __init__(self, token_manager: MercadoLivreOAuthTokenManager) -> None:
        self.token_manager = token_manager
        super().__init__(
            token_manager.access_token(),
            session=token_manager.session,
            timeout=token_manager.timeout,
        )

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        self._token = self.token_manager.access_token()
        try:
            return super()._get(path, params=params)
        except MarketplaceApiError as exc:
            if exc.http_status != 401:
                raise
        refreshed = self.token_manager.refresh()
        self._token = refreshed.access_token
        return super()._get(path, params=params)


class TikTokShopReadOnlyClient:
    BASE_URL = "https://open-api.tiktokglobalshop.com"

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        access_token: str,
        *,
        shop_cipher: str | None = None,
        session: requests.Session | None = None,
        timeout: int = 20,
    ):
        self.app_key = app_key
        self._secret = app_secret
        self._token = access_token
        self.shop_cipher = shop_cipher
        self.session = session or requests.Session()
        self.timeout = timeout

    def _signature(self, path: str, params: dict[str, Any], body: str = "") -> str:
        filtered = {
            key: value
            for key, value in params.items()
            if key not in {"sign", "access_token"}
        }
        base = (
            self._secret
            + path
            + "".join(f"{key}{filtered[key]}" for key in sorted(filtered))
            + body
            + self._secret
        )
        return hmac.new(
            self._secret.encode(), base.encode(), hashlib.sha256
        ).hexdigest()

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        query = {
            "app_key": self.app_key,
            "timestamp": int(time.time()),
            **(params or {}),
        }
        query["sign"] = self._signature(path, query)
        response = self.session.get(
            f"{self.BASE_URL}{path}",
            headers={
                "Content-Type": "application/json",
                "x-tts-access-token": self._token,
            },
            params=query,
            timeout=self.timeout,
        )
        if not response.ok:
            raise _safe_error(response)
        data = response.json()
        if data.get("code") not in (None, 0):
            raise MarketplaceApiError(
                "REMOTE_API_ERROR", http_status=response.status_code
            )
        return data.get("data", data)

    def account_identity(self) -> dict[str, Any]:
        data = self._get("/authorization/202309/shops")
        shops = data.get("shops", [])
        return {
            "shops": [
                {
                    "id": item.get("id"),
                    "cipher": item.get("cipher"),
                    "region": item.get("region"),
                }
                for item in shops
            ]
        }

    def health_check(self) -> dict[str, Any]:
        account = self.account_identity()
        return {
            "status": "HEALTHY",
            "account_reachable": bool(account["shops"]),
            "account": account,
        }

    def discover_categories(self, keyword: str = "relógio") -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "locale": "pt-BR",
            "keyword": keyword,
            "category_version": "v1",
            "include_prohibited_categories": "true",
        }
        if self.shop_cipher:
            params["shop_cipher"] = self.shop_cipher
        data = self._get("/product/202309/categories", params=params)
        return [
            {
                "external_category_id": item.get("id"),
                "external_category_name": item.get("local_name"),
                "is_leaf": item.get("is_leaf"),
                "permission_statuses": item.get("permission_statuses", []),
                "source": "TikTok Shop Get Categories",
            }
            for item in data.get("categories", [])
        ]

    def category_rules(self, category_id: str) -> dict[str, Any]:
        if not self.shop_cipher:
            raise MarketplaceApiError("SHOP_CIPHER_REQUIRED")
        return self._get(
            f"/product/202309/categories/{category_id}/rules",
            params={
                "locale": "pt-BR",
                "category_version": "v1",
                "shop_cipher": self.shop_cipher,
            },
        )


CLIENT_CAPABILITIES = {
    "mercado-livre": {
        "authentication": "IMPLEMENTED",
        "token_refresh": "IMPLEMENTED",
        "account_identity": "IMPLEMENTED",
        "health_check": "IMPLEMENTED",
        "category_discovery": "IMPLEMENTED",
        "attribute_discovery": "IMPLEMENTED",
        "listing_lookup": "IMPLEMENTED",
        "listing_create": "MISSING",
        "listing_update": "MISSING",
        "listing_pause": "MISSING",
        "read_back": "PARTIAL",
        "error_normalization": "IMPLEMENTED",
        "rate_limits": "PARTIAL",
    },
    "shopee": {
        key: "API_CONTRACT_UNVERIFIED"
        for key in (
            "authentication",
            "token_refresh",
            "account_identity",
            "health_check",
            "category_discovery",
            "attribute_discovery",
            "listing_lookup",
            "listing_create",
            "listing_update",
            "listing_pause",
            "read_back",
            "error_normalization",
            "rate_limits",
        )
    },
    "tiktok-shop": {
        "authentication": "IMPLEMENTED",
        "token_refresh": "MISSING",
        "account_identity": "IMPLEMENTED",
        "health_check": "IMPLEMENTED",
        "category_discovery": "IMPLEMENTED",
        "attribute_discovery": "PARTIAL",
        "listing_lookup": "MISSING",
        "listing_create": "MISSING",
        "listing_update": "MISSING",
        "listing_pause": "MISSING",
        "read_back": "MISSING",
        "error_normalization": "IMPLEMENTED",
        "rate_limits": "PARTIAL",
    },
    "meta": {
        key: "API_CONTRACT_UNVERIFIED"
        for key in (
            "authentication",
            "token_refresh",
            "account_identity",
            "health_check",
            "category_discovery",
            "attribute_discovery",
            "listing_lookup",
            "listing_create",
            "listing_update",
            "listing_pause",
            "read_back",
            "error_normalization",
            "rate_limits",
        )
    },
}


def connection_status() -> dict[str, Any]:
    matrix = credential_matrix()
    result: dict[str, Any] = {}
    for channel in ("mercado-livre", "shopee", "tiktok-shop", "meta"):
        rows = [item for item in matrix if item["channel"] == channel]
        required = [item for item in rows if item["required_for_read"]]
        credentials = (
            "PRESENT"
            if required and all(item["status"] == "PRESENT" for item in required)
            else "MISSING"
        )
        if channel == "mercado-livre" and credentials == "MISSING":
            try:
                from .mercado_livre_oauth import persisted_credentials_available

                if persisted_credentials_available():
                    credentials = "PRESENT"
            except (ImportError, RuntimeError):
                pass
        contract = (
            "VERIFIED_READ_ONLY"
            if channel in {"mercado-livre", "tiktok-shop"}
            else "API_CONTRACT_UNVERIFIED"
        )
        result[channel] = {
            "credentials": credentials,
            "auth": "NOT_TESTED" if credentials == "PRESENT" else "MISSING",
            "account": "NOT_TESTED",
            "api_contract": contract,
            "category_discovery": "NOT_TESTED"
            if credentials == "PRESENT"
            else "CATEGORY_DISCOVERY_BLOCKED_BY_CREDENTIALS",
            "attributes": "NOT_TESTED",
            "logistics": "NOT_TESTED",
            "draft": "LOCAL_DRAFT",
            "readback": "NOT_TESTED",
            "publish": "BLOCKED_BY_GLOBAL_FLAG",
            "capabilities": CLIENT_CAPABILITIES[channel],
        }
    return result


def assess_origin(
    *, channel: str, origin_country: str, supported_countries: list[str] | None
) -> dict[str, str]:
    if supported_countries is None:
        return {
            "channel": channel,
            "status": "NOT_TESTED",
            "actual_requirement": "consultar regras logísticas da conta conectada",
            "reason": "nenhuma regra real foi lida",
            "what_must_change": "conectar em modo read-only e consultar a configuração logística",
        }
    if origin_country not in supported_countries:
        return {
            "channel": channel,
            "status": "ORIGIN_NOT_SUPPORTED",
            "actual_requirement": f"origem em: {', '.join(supported_countries)}",
            "reason": f"{origin_country} não consta nas origens aceitas pela conta",
            "what_must_change": "ajustar a operação logística no canal; não substituir o endereço automaticamente",
        }
    return {
        "channel": channel,
        "status": "SUPPORTED",
        "actual_requirement": f"origem em: {', '.join(supported_countries)}",
        "reason": "origem aceita pela regra consultada",
        "what_must_change": "nenhuma alteração",
    }
