"""Economia por canal (Fase 1): motor, casos dourados, bordas de faixa e rotas."""

from __future__ import annotations

import json
import re
import subprocess
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from veratus_agents import channel_economics as engine
from veratus_agents.paid_media import calculate_economics

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "channel_economics_cases.json"
NO_INSTALLMENTS = engine.Assumptions(tiktok_free_shipping=True)
ORIGIN_OK = engine.Assumptions(watch_origin_confirmed=True)


def write_config(tmp_path: Path, status: str = "SOURCED", **changes) -> Path:
    """Configuração mínima, com custos fictícios e números redondos."""
    data = {
        "schema": "veratus.channel_fees/v1",
        "as_of": "2026-10",
        "defaults": {"target_margin": {"value": "0.20", "status": "CONFIRMED"}},
        "requirements": {"watch": {"domestic_origin_required_on": ["mercado_livre"]}},
        "common_rules": [
            {
                "id": "packaging",
                "kind": "packaging",
                "label": "Embalagem",
                "type": "fixed",
                "fixed": "2.00",
                "status": status,
                "source": "teste",
            }
        ],
        "channels": {
            "mercado_livre": {
                "kind": "marketplace",
                "rules": [
                    {
                        "id": "commission",
                        "kind": "channel_fee",
                        "label": "Comissão",
                        "type": "percent",
                        "rate": "0.10",
                        "status": status,
                        "source": "teste",
                    },
                    {
                        "id": "unit",
                        "kind": "channel_fee",
                        "label": "Custo por unidade",
                        "type": "band",
                        "bands": [{"min": "0", "max": "79.00", "fixed": "6.00"}],
                        "status": status,
                        "source": "teste",
                    },
                    {
                        "id": "shipping",
                        "kind": "shipping",
                        "label": "Frete",
                        "type": "fixed",
                        "min_price": "79.00",
                        "fixed": "20.00",
                        "status": status,
                        "source": "teste",
                    },
                ],
            },
            "direct_pix": {
                "kind": "direct",
                "rules": [
                    {
                        "id": "pix",
                        "kind": "payment_fee",
                        "label": "Pix",
                        "type": "percent",
                        "rate": "0.01",
                        "status": status,
                        "source": "teste",
                    }
                ],
            },
        },
    }
    data.update(changes)
    path = tmp_path / "fees.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def repo_config() -> engine.FeeConfig:
    return engine.load_fee_config(engine.DEFAULT_FEES_PATH)


def amounts(result: dict) -> dict[str, str]:
    return {item["id"]: item["amount"] for item in result["items"]}


# -- schema -----------------------------------------------------------------------


def test_repository_fee_file_loads_as_immutable_objects(repo_config):
    assert set(repo_config.channels) == {
        "mercado_livre",
        "shopee",
        "tiktok_shop",
        "amazon",
        "direct_pix",
        "direct_card",
    }
    with pytest.raises(TypeError):
        repo_config.channels["x"] = None  # type: ignore[index]
    rule = repo_config.channels["shopee"].rules[0]
    with pytest.raises(AttributeError):
        rule.status = "CONFIRMED"  # type: ignore[misc]


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "outro/v1"},
        {"channels": {}},
        {"channels": {"x": {"kind": "loja", "rules": []}}},
        {
            "channels": {
                "x": {
                    "kind": "direct",
                    "rules": [
                        {
                            "id": "a",
                            "kind": "channel_fee",
                            "label": "A",
                            "type": "percent",
                            "rate": "-0.1",
                            "status": "SOURCED",
                            "source": "s",
                        }
                    ],
                }
            }
        },
        {
            "channels": {
                "x": {
                    "kind": "direct",
                    "rules": [
                        {
                            "id": "a",
                            "kind": "channel_fee",
                            "label": "A",
                            "type": "band",
                            "bands": [
                                {"min": "0", "max": "50", "fixed": "1"},
                                {"min": "60", "fixed": "2"},
                            ],
                            "status": "SOURCED",
                            "source": "s",
                        }
                    ],
                }
            }
        },
    ],
)
def test_invalid_fee_files_are_rejected(tmp_path, change):
    path = write_config(tmp_path, **change)
    with pytest.raises(engine.FeeConfigError):
        engine.load_fee_config(path)


# -- status -----------------------------------------------------------------------


def test_sourced_rules_and_cost_make_a_complete_result(tmp_path):
    config = engine.load_fee_config(write_config(tmp_path))
    result = engine.evaluate(
        "direct_pix", "jewelry", "100.00", cost="30.00", config=config
    )

    assert result["status"] == "COMPLETE"
    assert amounts(result) == {"pix": "1.00", "packaging": "2.00"}
    assert result["fees"] == "1.00"
    assert result["other"] == "2.00"
    assert result["sobra"] == "97.00"
    assert result["lucro"] == "67.00"
    assert result["custo_maximo"] == "77.00"  # sobra − 20% do preço
    assert result["cpa_maximo"] == "47.00"  # lucro − 20% do preço


def test_assumption_estimates_and_unverified_or_missing_cost_is_incomplete(tmp_path):
    estimated = engine.load_fee_config(write_config(tmp_path, status="ASSUMPTION"))
    assert (
        engine.evaluate("direct_pix", "jewelry", 100, cost=30, config=estimated)[
            "status"
        ]
        == "ESTIMATED"
    )
    unverified = engine.load_fee_config(write_config(tmp_path, status="UNVERIFIED"))
    assert (
        engine.evaluate("direct_pix", "jewelry", 100, cost=30, config=unverified)[
            "status"
        ]
        == "INCOMPLETE"
    )
    sourced = engine.load_fee_config(write_config(tmp_path))
    no_cost = engine.evaluate("direct_pix", "jewelry", 100, config=sourced)
    assert no_cost["status"] == "INCOMPLETE"
    assert "COST_MISSING" in no_cost["status_reasons"]
    assert no_cost["lucro"] is None and no_cost["cpa_maximo"] is None


def test_watch_on_marketplace_waits_for_domestic_origin(tmp_path):
    config = engine.load_fee_config(write_config(tmp_path))
    blocked = engine.evaluate(
        "mercado_livre",
        "watch",
        "289.90",
        cost=50,
        config=config,
        assumptions=engine.Assumptions(),
    )
    direct = engine.evaluate(
        "direct_pix",
        "watch",
        "289.90",
        cost=50,
        config=config,
        assumptions=engine.Assumptions(),
    )
    confirmed = engine.evaluate(
        "mercado_livre",
        "watch",
        "289.90",
        cost=50,
        config=config,
        assumptions=ORIGIN_OK,
    )

    assert blocked["status"] == "BLOCKED_BY_ORIGIN"
    assert "ORIGIN_UNCONFIRMED" in blocked["status_reasons"]
    assert direct["status"] == "COMPLETE"
    assert confirmed["status"] == "COMPLETE"


def test_placeholder_file_never_reaches_complete(repo_config):
    for channel in repo_config.channels:
        for product_type in engine.PRODUCT_TYPES:
            result = engine.evaluate(
                channel,
                product_type,
                "289.90",
                cost="10.00",
                config=repo_config,
                assumptions=ORIGIN_OK,
            )
            assert result["status"] == "INCOMPLETE", (channel, product_type)


def test_watch_direct_shipping_is_the_founder_assumption(repo_config):
    result = engine.evaluate(
        "direct_pix", "watch", "289.90", cost="10.00", config=repo_config
    )
    shipping = next(item for item in result["items"] if item["kind"] == "shipping")

    assert shipping["amount"] == "25.00"
    assert shipping["status"] == "ASSUMPTION"


# -- bordas de faixa (arquivo do repositório) -------------------------------------


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (
            "49.99",
            {"tiktok_commission": "5.00", "tiktok_free_shipping_program": "3.00"},
        ),
        (
            "50.00",
            {"tiktok_commission": "7.00", "tiktok_free_shipping_program": "3.00"},
        ),
    ],
)
def test_tiktok_band_edge(repo_config, price, expected):
    result = engine.evaluate(
        "tiktok_shop", "jewelry", price, config=repo_config, assumptions=NO_INSTALLMENTS
    )
    assert {k: v for k, v in amounts(result).items() if k in expected} == expected


def test_tiktok_free_shipping_program_is_capped_and_optional(repo_config):
    expensive = engine.evaluate(
        "tiktok_shop",
        "jewelry",
        "2000.00",
        config=repo_config,
        assumptions=NO_INSTALLMENTS,
    )
    off = engine.evaluate(
        "tiktok_shop",
        "jewelry",
        "2000.00",
        config=repo_config,
        assumptions=engine.Assumptions(tiktok_free_shipping=False),
    )
    assert amounts(expensive)["tiktok_free_shipping_program"] == "50.00"
    assert "tiktok_free_shipping_program" not in amounts(off)


@pytest.mark.parametrize(
    ("price", "unit_cost", "shipping"),
    [("78.99", "6.75", None), ("79.00", None, "19.95")],
)
def test_mercado_livre_unit_cost_below_79_and_shipping_from_79(
    repo_config, price, unit_cost, shipping
):
    result = amounts(
        engine.evaluate("mercado_livre", "jewelry", price, config=repo_config)
    )
    assert result.get("ml_unit_cost") == unit_cost
    assert result.get("ml_shipping") == shipping


@pytest.mark.parametrize(
    ("price", "fee"), [("79.99", "20.00"), ("80.00", "27.20"), ("200.00", "54.00")]
)
def test_shopee_band_edge(repo_config, price, fee):
    result = amounts(engine.evaluate("shopee", "jewelry", price, config=repo_config))
    assert result["shopee_commission"] == fee


def test_card_installments_only_when_the_seller_pays(repo_config):
    buyer = engine.evaluate(
        "direct_card",
        "watch",
        "289.90",
        config=repo_config,
        assumptions=engine.Assumptions(installments=6, installments_interest="buyer"),
    )
    seller = engine.evaluate(
        "direct_card",
        "watch",
        "289.90",
        config=repo_config,
        assumptions=engine.Assumptions(installments=6, installments_interest="seller"),
    )
    assert "card_installments" not in amounts(buyer)
    assert amounts(seller)["card_installments"] == "28.87"


def test_each_item_is_rounded_to_cents(repo_config):
    result = engine.evaluate("amazon", "watch", "33.33", config=repo_config)
    for item in result["items"]:
        assert re.fullmatch(r"\d+\.\d{2}", item["amount"])
    assert amounts(result)["amazon_referral"] == "4.33"


# -- busca de preço ---------------------------------------------------------------


def test_price_for_target_margin_is_the_smallest_90_price(repo_config):
    for channel in ("shopee", "mercado_livre", "tiktok_shop"):
        price = engine.price_for_target_margin(
            channel,
            "jewelry",
            "20.00",
            target_margin="0.20",
            config=repo_config,
            assumptions=NO_INSTALLMENTS,
        )
        assert price is not None and str(price).endswith(".90")
        result = engine.evaluate(
            channel, "jewelry", price, cost="20.00", config=repo_config
        )
        assert Decimal(result["lucro"]) >= Decimal(price) * Decimal("0.20") - Decimal(
            "0.01"
        )
        for reais in range(int(price)):
            lower = Decimal(reais) + Decimal("0.90")
            check = engine.evaluate(
                channel, "jewelry", lower, cost="20.00", config=repo_config
            )
            assert Decimal(check["lucro"]) < (lower * Decimal("0.20")).quantize(
                Decimal("0.01")
            )


def test_price_search_above_250_uses_binary_search(repo_config):
    price = engine.price_for_target_margin(
        "shopee", "watch", "400.00", target_margin="0.20", config=repo_config
    )
    assert price is not None and price > 250
    below = price - 1
    assert Decimal(
        engine.evaluate("shopee", "watch", below, cost="400", config=repo_config)[
            "lucro"
        ]
    ) < (below * Decimal("0.20"))


def test_price_to_match_pix_leaves_the_same_sobra(repo_config):
    price = engine.price_to_match(
        "shopee", "watch", "289.90", config=repo_config, assumptions=ORIGIN_OK
    )
    pix = engine.evaluate("direct_pix", "watch", "289.90", config=repo_config)
    shopee = engine.evaluate("shopee", "watch", price, config=repo_config)
    previous = engine.evaluate("shopee", "watch", price - 1, config=repo_config)

    assert Decimal(shopee["sobra"]) >= Decimal(pix["sobra"])
    assert Decimal(previous["sobra"]) < Decimal(pix["sobra"])


def test_channel_price_plan_covers_every_channel(repo_config):
    plan = engine.channel_price_plan(
        {"category": "jewelry_accessories"},
        cost="20.00",
        price="129.90",
        config=repo_config,
    )
    assert plan["product_type"] == "jewelry"
    assert {item["channel"] for item in plan["channels"]} == set(repo_config.channels)
    assert plan["apply_requires"] == "PRICE_APPROVAL"


# -- paid_media -------------------------------------------------------------------


def test_paid_media_fields_come_from_the_channel_engine(tmp_path):
    config = engine.load_fee_config(write_config(tmp_path))
    product = {"sku": "teste", "price_brl": "100.00", "cost_brl": "30.00"}
    result = calculate_economics(product, channel="direct_pix", fee_config=config)

    assert result["status"] == "COMPLETE"
    assert result["inputs"]["payment_fee"] == "1.00"
    assert result["inputs"]["packaging_cost"] == "2.00"
    assert result["break_even_cpa"] == "67.00"
    assert result["break_even_cpa_final"] is True


def test_paid_media_keeps_autonomy_off_while_fees_are_estimated(tmp_path):
    config = engine.load_fee_config(write_config(tmp_path, status="ASSUMPTION"))
    product = {"sku": "teste", "price_brl": "100.00", "cost_brl": "30.00"}
    result = calculate_economics(product, channel="direct_pix", fee_config=config)

    assert result["status"] == "ESTIMATED"
    assert result["break_even_cpa"] is None
    assert result["break_even_cpa_final"] is False
    assert result["estimated_contribution_before_ads"] == "67.00"


# -- casos dourados do fundador ---------------------------------------------------


def _golden_cases() -> list[dict]:
    if not GOLDEN.exists():
        return []
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return data["cases"] if isinstance(data, dict) else data


@pytest.mark.skipif(
    not GOLDEN.exists(),
    reason="pacote do fundador pendente: tests/golden/channel_economics_cases.json",
)
@pytest.mark.parametrize("case", _golden_cases(), ids=lambda case: str(case.get("id")))
def test_golden_cases_match_the_founder_calculator(case):
    assumptions = engine.Assumptions(
        watch_origin_confirmed=True,
        **{
            key: (Decimal(str(value)) if key == "target_margin" else value)
            for key, value in (case.get("assumptions") or {}).items()
        },
    )
    result = engine.evaluate(
        case["channel"],
        case["product_type"],
        case["price"],
        cost=case.get("cost"),
        assumptions=assumptions,
        require_cost=False,
    )
    for name, expected in case["expected"].items():
        if expected is None or name not in result or name == "status":
            continue
        assert abs(Decimal(str(result[name])) - Decimal(str(expected))) <= Decimal(
            "0.01"
        ), name


# -- nada de custo real versionado ------------------------------------------------

_COST_KEYS = re.compile(
    r'"(?:cost|cost_brl|unit_cost|custo|custo_produto|supplier|fornecedor|margin_brl)"'
)


def _tracked(*patterns: str) -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "--", *patterns],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    return [ROOT / name for name in output]


def test_fee_and_knowledge_files_carry_no_product_cost_or_supplier():
    files = [engine.DEFAULT_FEES_PATH, *(_tracked("knowledge", "social/calendario-*"))]
    for path in files:
        if path.is_file() and path.suffix in {".json", ".yaml", ".yml", ".csv"}:
            assert not _COST_KEYS.search(path.read_text(encoding="utf-8")), path.name


def test_golden_costs_are_declared_fictitious():
    if not GOLDEN.exists():
        pytest.skip("pacote do fundador pendente")
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    declared = json.dumps(data if isinstance(data, dict) else data[:1]).casefold()
    assert "fict" in declared, "os custos dos casos dourados precisam ser fictícios"


# -- rotas administrativas --------------------------------------------------------


def test_economics_routes_for_a_test_sku(tmp_path):
    from integrations.webhook import app, rate_store

    rate_store.clear()
    with patch.dict(
        "os.environ",
        {
            "VERATUS_ADMIN_TOKEN": "economics-admin",
            "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
            "DATABASE_URL": "",
        },
        clear=False,
    ):
        client = app.test_client()
        headers = {"X-Veratus-Admin-Token": "economics-admin"}
        created = client.post(
            "/os/products",
            headers=headers,
            json={
                "actor": "teste",
                "event_id": "evt-economics-0001",
                "product": {
                    "name": "Colar de teste",
                    "category": "jewelry_accessories",
                    "subcategory": "necklace",
                    "category_code": "JOI",
                    "product_code": "TST",
                    "cost_brl": "20.00",
                    "price_brl": "129.90",
                },
            },
        )
        sku = created.json["product"]["sku"]
        shopee = client.get(f"/os/economics/{sku}?channel=shopee", headers=headers)
        every = client.get(f"/os/economics/{sku}", headers=headers)
        unknown = client.get(f"/os/economics/{sku}?channel=orkut", headers=headers)
        simulate = client.post(
            "/os/economics/simulate",
            headers=headers,
            json={
                "channel": "tiktok_shop",
                "product_type": "jewelry",
                "price": "49.90",
                "cost": "10.00",
                "assumptions": {"tiktok_free_shipping": False},
            },
        )
        plan = client.get(f"/os/pricing/channel-plan/{sku}", headers=headers)
        requested = client.post(
            f"/os/pricing/channel-plan/{sku}/request",
            headers=headers,
            json={"channel": "shopee", "price": "139.90", "reason": "teste"},
        )
        denied = client.get(f"/os/economics/{sku}")

    assert created.status_code == 201
    assert shopee.status_code == 200
    result = shopee.json["channels"]["shopee"]
    assert result["items"] and result["sobra"] and result["status"] == "INCOMPLETE"
    assert len(every.json["channels"]) == 6
    assert unknown.status_code == 400
    assert simulate.status_code == 200
    assert "min_price_for_target_margin" in simulate.json["simulation"]
    assert plan.status_code == 200 and len(plan.json["channels"]) == 6
    assert requested.status_code == 202
    assert requested.json["status"] == "PENDING_APPROVAL"
    assert requested.json["external_write"] is False
    assert denied.status_code == 401
