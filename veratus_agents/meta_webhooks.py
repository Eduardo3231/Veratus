"""Checks shared by every Meta webhook (WhatsApp Cloud API, Instagram).

Meta signs each POST with ``X-Hub-Signature-256: sha256=<HMAC-SHA256(body,
app_secret)>`` and verifies the endpoint with a GET carrying ``hub.mode``,
``hub.verify_token`` and ``hub.challenge``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import unicodedata

DEFAULT_GRAPH_API_VERSION = "v23.0"


def graph_api_version() -> str:
    return os.getenv("META_GRAPH_API_VERSION", "").strip() or DEFAULT_GRAPH_API_VERSION


def verify_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def verify_subscription(
    mode: str | None,
    token: str | None,
    challenge: str | None,
    expected_token: str,
) -> str | None:
    """Return the challenge to echo back, or None when the request is not ours."""
    if (
        mode == "subscribe"
        and expected_token
        and token
        and challenge
        and hmac.compare_digest(token, expected_token)
    ):
        return challenge
    return None


def normalize_text(text: str) -> str:
    """Lowercase without accents, so "Relógio" and "RELOGIO" match."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def env_flag(name: str) -> bool:
    """Send switches accept only the literal "true" (fail-closed)."""
    return os.getenv(name, "false").strip().lower() == "true"
