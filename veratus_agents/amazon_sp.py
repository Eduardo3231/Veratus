"""Amazon Selling Partner API: read-only connection for the Brazil store.

A private (self-authorized) app has no login page: in Seller Central the seller
authorizes the app and copies a refresh token. The service trades it at Login
with Amazon for an access token (1 h) and sends it in ``x-amz-access-token``.
Brazil is served by the North America endpoint; marketplace A2Q3Y263D00KWC.
Nothing here creates, changes or pauses a listing.

Environment (Render > Environment, never in the repository):
AMAZON_SP_LWA_CLIENT_ID, AMAZON_SP_LWA_CLIENT_SECRET, AMAZON_SP_REFRESH_TOKEN,
and optionally AMAZON_SP_MARKETPLACE_ID and AMAZON_SP_ENDPOINT.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import requests

from .marketplace_clients import MarketplaceApiError

CHANNEL = "amazon"
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
DEFAULT_ENDPOINT = "https://sellingpartnerapi-na.amazon.com"
BRAZIL_MARKETPLACE_ID = "A2Q3Y263D00KWC"
PARTICIPATIONS_PATH = "/sellers/v1/marketplaceParticipations"
PRODUCT_TYPES_PATH = "/definitions/2020-09-01/productTypes"
REQUIRED_ENV = (
    "AMAZON_SP_LWA_CLIENT_ID",
    "AMAZON_SP_LWA_CLIENT_SECRET",
    "AMAZON_SP_REFRESH_TOKEN",
)


def configuration() -> dict[str, bool]:
    """Which settings exist, never their values."""
    return {name: bool(os.getenv(name, "").strip()) for name in REQUIRED_ENV}


class AmazonSellingPartnerClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        marketplace_id: str = BRAZIL_MARKETPLACE_ID,
        session: requests.Session | None = None,
        timeout: int = 20,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not client_id or not client_secret or not refresh_token:
            raise MarketplaceApiError("AMAZON_CONFIGURATION_INCOMPLETE")
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self.endpoint = endpoint.rstrip("/")
        self.marketplace_id = marketplace_id
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock
        self._access_token: str | None = None
        self._expires_at = 0.0

    @classmethod
    def from_env(
        cls, *, session: requests.Session | None = None
    ) -> AmazonSellingPartnerClient:
        return cls(
            os.getenv("AMAZON_SP_LWA_CLIENT_ID", "").strip(),
            os.getenv("AMAZON_SP_LWA_CLIENT_SECRET", "").strip(),
            os.getenv("AMAZON_SP_REFRESH_TOKEN", "").strip(),
            endpoint=os.getenv("AMAZON_SP_ENDPOINT", "").strip() or DEFAULT_ENDPOINT,
            marketplace_id=os.getenv("AMAZON_SP_MARKETPLACE_ID", "").strip()
            or BRAZIL_MARKETPLACE_ID,
            session=session,
        )

    @staticmethod
    def _error(response: requests.Response) -> MarketplaceApiError:
        try:
            errors = response.json().get("errors") or []
            code = str(errors[0].get("code") or "") if errors else ""
        except (ValueError, AttributeError, IndexError):
            code = ""
        if response.status_code == 401:
            return MarketplaceApiError("AUTH_INVALID_OR_EXPIRED", http_status=401)
        if response.status_code == 429:
            return MarketplaceApiError("RATE_LIMITED", http_status=429)
        # Only Amazon's error code travels; never the message, token or body.
        return MarketplaceApiError(
            f"AMAZON_{(code or 'REMOTE_API_ERROR').upper()}"[:80],
            http_status=response.status_code,
        )

    def access_token(self, *, force: bool = False) -> str:
        if not force and self._access_token and self.clock() < self._expires_at - 60:
            return self._access_token
        response = self.session.post(
            LWA_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            timeout=self.timeout,
        )
        if not response.ok:
            # LWA answers 400 invalid_grant for a revoked or mistyped token.
            raise MarketplaceApiError("AMAZON_LWA_REFRESH_FAILED", http_status=401)
        data = response.json()
        self._access_token = str(data.get("access_token") or "")
        if not self._access_token:
            raise MarketplaceApiError("AMAZON_LWA_REFRESH_FAILED", http_status=401)
        self._expires_at = self.clock() + float(data.get("expires_in") or 3600)
        return self._access_token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in (1, 2):
            response = self.session.get(
                f"{self.endpoint}{path}",
                headers={
                    "x-amz-access-token": self.access_token(force=attempt == 2),
                    "user-agent": "Veratus/1.0 (Language=Python)",
                    "accept": "application/json",
                },
                params=params,
                timeout=self.timeout,
            )
            if response.status_code == 401 and attempt == 1:
                continue
            if not response.ok:
                raise self._error(response)
            return response.json()
        raise MarketplaceApiError("AUTH_INVALID_OR_EXPIRED", http_status=401)

    def account_identity(self) -> dict[str, Any]:
        payload = self._get(PARTICIPATIONS_PATH).get("payload") or []
        marketplaces = [
            {
                "id": (item.get("marketplace") or {}).get("id"),
                "country": (item.get("marketplace") or {}).get("countryCode"),
                "participating": (item.get("participation") or {}).get(
                    "isParticipating"
                ),
                "suspended_listings": (item.get("participation") or {}).get(
                    "hasSuspendedListings"
                ),
            }
            for item in payload
        ]
        target = next(
            (item for item in marketplaces if item["id"] == self.marketplace_id), None
        )
        return {
            "marketplace_id": self.marketplace_id,
            "participating": bool(target and target["participating"]),
            "suspended_listings": bool(target and target["suspended_listings"]),
            "marketplaces": marketplaces,
        }

    def discover_categories(self, keyword: str = "relógio") -> list[dict[str, Any]]:
        data = self._get(
            PRODUCT_TYPES_PATH,
            {
                "marketplaceIds": self.marketplace_id,
                "keywords": keyword,
                "locale": "pt_BR",
            },
        )
        return [
            {
                "external_category_id": item.get("name"),
                "external_category_name": item.get("displayName") or item.get("name"),
                "source": "Amazon Product Type Definitions",
            }
            for item in data.get("productTypes", [])
        ]
