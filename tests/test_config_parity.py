"""Deployment flags stay fail-closed and documented in both config files."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from veratus_agents.marketplace_ops import ExternalWriteGuard, MarketplaceStore
from veratus_agents.paid_media import AutonomyMode, PaidMediaConfig

ROOT = Path(__file__).resolve().parents[1]
CHANNEL_FLAGS = [
    f"{channel}_{suffix}"
    for channel in ("MERCADO_LIVRE", "SHOPEE", "TIKTOK_SHOP", "META")
    for suffix in ("ENABLED", "PUBLISH_ENABLED")
]
MUST_BE_FALSE = {
    "PUBLISH_ENABLED",
    "PAID_MEDIA_LIVE_WRITES",
    "META_PURCHASE_EVENT_VERIFIED",
    "WHATSAPP_SEND_ENABLED",
    "INSTAGRAM_DM_ENABLED",
    *CHANNEL_FLAGS,
}
# Platform/container settings live only in the Blueprint; the MailerLite key is
# used by the manual, consent-checked import script and never by the web service.
RENDER_ONLY = {"PORT", "PYTHONUNBUFFERED"}
LOCAL_ONLY = {"MAILERLITE_API_KEY", "MAILERLITE_GROUP_ID"}


def _render_env() -> dict[str, str | None]:
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    entries = re.findall(r"- key: (\w+)\n\s+(value|sync|fromDatabase):\s*(.*)", text)
    return {
        key: (value.strip().strip('"') if kind == "value" else None)
        for key, kind, value in entries
    }


def _example_env() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if re.match(r"^[A-Z0-9_]+=", line):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def test_render_and_env_example_declare_the_same_names() -> None:
    render, example = set(_render_env()), set(_example_env())

    assert render - example == RENDER_ONLY
    assert example - render == LOCAL_ONLY


def test_external_write_flags_are_explicitly_false_everywhere() -> None:
    render, example = _render_env(), _example_env()

    for name in MUST_BE_FALSE:
        assert render[name] == "false", name
        assert example[name] == "false", name
    assert render["MARKETPLACE_MODE"] == example["MARKETPLACE_MODE"] == "LOCAL"
    assert render["PAID_MEDIA_AUTONOMY_MODE"] == "SHADOW"
    assert example["PAID_MEDIA_AUTONOMY_MODE"] == "SHADOW"


def test_absent_flags_resolve_to_blocked(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (*MUST_BE_FALSE, "MARKETPLACE_MODE", "PAID_MEDIA_AUTONOMY_MODE"):
        monkeypatch.delenv(name, raising=False)
    store = MarketplaceStore(tmp_path / "marketplace.sqlite3")
    product = {"sku": "ocean-blue", "status": "APPROVED"}

    for channel in ("mercado-livre", "shopee", "tiktok-shop", "meta"):
        allowed, reasons = ExternalWriteGuard(store).check(
            channel, product, publish_enabled=True
        )
        assert allowed is False
        assert {
            "MARKETPLACE_MODE_LOCAL",
            "GLOBAL_PUBLISH_DISABLED",
            "CHANNEL_DISABLED",
            "CHANNEL_PUBLISH_DISABLED",
        } <= set(reasons)
    config = PaidMediaConfig.from_env()
    assert config.live_writes is False
    assert config.autonomy_mode is AutonomyMode.SHADOW


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0", Decimal(0)), ("-5", Decimal(0)), ("abc", Decimal(20)), ("NaN", Decimal(20))],
)
def test_paid_media_caps_never_expand_on_bad_input(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: Decimal
) -> None:
    monkeypatch.setenv("PAID_MEDIA_DEFAULT_DAILY_BUDGET_BRL", raw)
    monkeypatch.setenv("PAID_MEDIA_TEST_MAX_SPEND_BRL", raw)

    config = PaidMediaConfig.from_env()

    assert config.daily_budget_brl == expected
    assert config.test_max_spend_brl == (Decimal(140) if expected else expected)


@pytest.mark.parametrize(("raw", "expected"), [("0.7", "0.7"), ("7", "0"), ("-1", "0")])
def test_safety_factor_above_one_blocks_instead_of_raising_cpa(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    monkeypatch.setenv("PAID_MEDIA_SAFETY_FACTOR", raw)

    assert PaidMediaConfig.from_env().safety_factor == Decimal(expected)
