"""Economia por canal: taxas, frete e sobra de cada venda, linha a linha.

O motor lê ``config/channel_fees_2026-10.json`` (schema ``veratus.channel_fees/v1``)
ou o arquivo em ``CHANNEL_FEES_PATH``. Cada regra traz fonte e status:

- ``SOURCED``/``CONFIRMED``: pode compor um resultado ``COMPLETE``;
- ``ASSUMPTION``: o resultado fica ``ESTIMATED``;
- ``UNVERIFIED``: o resultado fica ``INCOMPLETE``.

Custo de produto nunca está no arquivo: ele chega do Product Master (banco) ou da
chamada, e sem ele ``lucro`` e ``cpa_maximo`` ficam vazios. Valores em ``Decimal``,
arredondados em centavos só no fim de cada item.

Tipos de regra:

- ``percent``: ``preço × taxa`` (``rate`` ou ``rate_by_type``), com ``cap`` e
  ``minimum`` opcionais;
- ``fixed``: valor fixo (``fixed`` ou ``fixed_by_type``);
- ``band``: faixa de preço com ``rate`` e/ou ``fixed`` próprios (``min`` inclusivo,
  ``max`` exclusivo);
- ``installments``: juros do parcelado por número de parcelas.

``min_price``/``max_price`` limitam a faixa de preço em que a regra vale, e
``when`` liga a regra a uma premissa (``tiktok_free_shipping`` ou
``seller_pays_installments``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from types import MappingProxyType
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_FEES_PATH = BASE_DIR / "config" / "channel_fees_2026-10.json"
SCHEMA_ID = "veratus.channel_fees/v1"
PRODUCT_TYPES = ("jewelry", "watch", "smartphone")
STATUSES = ("SOURCED", "CONFIRMED", "ASSUMPTION", "UNVERIFIED")
KINDS = ("channel_fee", "payment_fee", "shipping", "packaging", "tax", "returns")
RULE_TYPES = ("percent", "fixed", "band", "installments")
CONDITIONS = ("tiktok_free_shipping", "seller_pays_installments")
CENT = Decimal("0.01")
# Abaixo de R$ 250 as faixas (R$ 50, 79, 80, 100 e 200) quebram a monotonicidade,
# então a busca de preço varre de real em real; acima, busca binária.
LINEAR_SEARCH_LIMIT = 250
SEARCH_CEILING = 100_000


class FeeConfigError(ValueError):
    """O arquivo de taxas não segue o schema veratus.channel_fees/v1."""


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _dec(value: Any, where: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise FeeConfigError(f"{where}: número ausente")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FeeConfigError(f"{where}: número inválido") from exc
    if not number.is_finite() or number < 0:
        raise FeeConfigError(f"{where}: número negativo ou inválido")
    return number


def _opt_dec(value: Any, where: str) -> Decimal | None:
    return None if value is None else _dec(value, where)


def _by_type(raw: Any, where: str) -> Mapping[str, Decimal]:
    if raw is None:
        return MappingProxyType({})
    if not isinstance(raw, dict) or set(raw) - set(PRODUCT_TYPES):
        raise FeeConfigError(f"{where}: tipos de produto válidos são {PRODUCT_TYPES}")
    return MappingProxyType({k: _dec(v, f"{where}.{k}") for k, v in raw.items()})


@dataclass(frozen=True)
class Band:
    min: Decimal
    max: Decimal | None
    rate: Decimal
    fixed: Decimal

    def contains(self, price: Decimal) -> bool:
        return price >= self.min and (self.max is None or price < self.max)


@dataclass(frozen=True)
class FeeRule:
    id: str
    kind: str
    label: str
    type: str
    status: str
    source: str
    rate: Decimal | None = None
    rate_by_type: Mapping[str, Decimal] = field(
        default_factory=lambda: MappingProxyType({})
    )
    fixed: Decimal | None = None
    fixed_by_type: Mapping[str, Decimal] = field(
        default_factory=lambda: MappingProxyType({})
    )
    status_by_type: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    bands: tuple[Band, ...] = ()
    rate_by_installments: Mapping[int, Decimal] = field(
        default_factory=lambda: MappingProxyType({})
    )
    cap: Decimal | None = None
    minimum: Decimal | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    when: str | None = None

    def status_for(self, product_type: str) -> str:
        return self.status_by_type.get(product_type, self.status)


@dataclass(frozen=True)
class Channel:
    id: str
    label: str
    kind: str
    rules: tuple[FeeRule, ...]


@dataclass(frozen=True)
class FeeConfig:
    as_of: str
    package_status: str
    channels: Mapping[str, Channel]
    common_rules: tuple[FeeRule, ...]
    domestic_origin_required: Mapping[str, frozenset[str]]
    target_margin: Decimal
    target_margin_status: str
    reference_channel: str
    path: str


@dataclass(frozen=True)
class Assumptions:
    """Premissas de uma simulação; nenhuma delas liga envio ou gasto."""

    target_margin: Decimal | None = None
    tiktok_free_shipping: bool = True
    installments: int = 1
    installments_interest: str = "buyer"  # "buyer" (cliente paga) ou "seller"
    watch_origin_confirmed: bool = False

    @classmethod
    def from_env(cls, **overrides: Any) -> Assumptions:
        from .commercial_config import origin_confirmed

        interest = os.getenv("PAYMENT_INSTALLMENTS_INTEREST", "buyer").strip().lower()
        base = cls(
            installments_interest="seller" if interest == "seller" else "buyer",
            watch_origin_confirmed=origin_confirmed(),
        )
        return replace(base, **overrides)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> Assumptions:
        base = cls.from_env()
        data = dict(data or {})
        unknown = set(data) - {
            "target_margin",
            "tiktok_free_shipping",
            "installments",
            "installments_interest",
        }
        if unknown:
            raise ValueError("assumption_desconhecida:" + ",".join(sorted(unknown)))
        changes: dict[str, Any] = {}
        if "target_margin" in data:
            margin = Decimal(str(data["target_margin"]))
            if not Decimal(0) <= margin < Decimal(1):
                raise ValueError("target_margin_invalida")
            changes["target_margin"] = margin
        if "tiktok_free_shipping" in data:
            if not isinstance(data["tiktok_free_shipping"], bool):
                raise ValueError("tiktok_free_shipping_invalido")
            changes["tiktok_free_shipping"] = data["tiktok_free_shipping"]
        if "installments" in data:
            installments = data["installments"]
            if not isinstance(installments, int) or not 1 <= installments <= 12:
                raise ValueError("installments_invalido")
            changes["installments"] = installments
        if "installments_interest" in data:
            if data["installments_interest"] not in ("buyer", "seller"):
                raise ValueError("installments_interest_invalido")
            changes["installments_interest"] = data["installments_interest"]
        return replace(base, **changes)


# -- carregamento e validação ---------------------------------------------------


def _parse_rule(raw: Any, where: str) -> FeeRule:
    if not isinstance(raw, dict):
        raise FeeConfigError(f"{where}: regra precisa ser objeto")
    for key in ("id", "kind", "label", "type", "status", "source"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise FeeConfigError(f"{where}: campo {key} ausente")
    where = f"{where}[{raw['id']}]"
    if raw["kind"] not in KINDS:
        raise FeeConfigError(f"{where}: kind deve ser um de {KINDS}")
    if raw["type"] not in RULE_TYPES:
        raise FeeConfigError(f"{where}: type deve ser um de {RULE_TYPES}")
    if raw["status"] not in STATUSES:
        raise FeeConfigError(f"{where}: status deve ser um de {STATUSES}")
    status_by_type = raw.get("status_by_type") or {}
    if not isinstance(status_by_type, dict) or any(
        k not in PRODUCT_TYPES or v not in STATUSES for k, v in status_by_type.items()
    ):
        raise FeeConfigError(f"{where}: status_by_type inválido")
    when = raw.get("when")
    if when is not None and when not in CONDITIONS:
        raise FeeConfigError(f"{where}: when deve ser um de {CONDITIONS}")
    bands: list[Band] = []
    for index, item in enumerate(raw.get("bands") or []):
        if not isinstance(item, dict):
            raise FeeConfigError(f"{where}.bands[{index}]: faixa inválida")
        bands.append(
            Band(
                min=_dec(item.get("min"), f"{where}.bands[{index}].min"),
                max=_opt_dec(item.get("max"), f"{where}.bands[{index}].max"),
                rate=_dec(item.get("rate", "0"), f"{where}.bands[{index}].rate"),
                fixed=_dec(item.get("fixed", "0"), f"{where}.bands[{index}].fixed"),
            )
        )
    for previous, current in pairwise(bands):
        if previous.max is None or previous.max != current.min:
            raise FeeConfigError(f"{where}: faixas precisam ser contíguas e ordenadas")
    installments_raw = raw.get("rate_by_installments") or {}
    if not isinstance(installments_raw, dict):
        raise FeeConfigError(f"{where}: rate_by_installments inválido")
    installments: dict[int, Decimal] = {}
    for key, value in installments_raw.items():
        if not str(key).isdigit() or not 2 <= int(key) <= 12:
            raise FeeConfigError(f"{where}: parcelas válidas são 2 a 12")
        installments[int(key)] = _dec(value, f"{where}.rate_by_installments.{key}")
    rule = FeeRule(
        id=raw["id"],
        kind=raw["kind"],
        label=raw["label"],
        type=raw["type"],
        status=raw["status"],
        source=raw["source"],
        rate=_opt_dec(raw.get("rate"), f"{where}.rate"),
        rate_by_type=_by_type(raw.get("rate_by_type"), f"{where}.rate_by_type"),
        fixed=_opt_dec(raw.get("fixed"), f"{where}.fixed"),
        fixed_by_type=_by_type(raw.get("fixed_by_type"), f"{where}.fixed_by_type"),
        status_by_type=MappingProxyType(dict(status_by_type)),
        bands=tuple(bands),
        rate_by_installments=MappingProxyType(installments),
        cap=_opt_dec(raw.get("cap"), f"{where}.cap"),
        minimum=_opt_dec(raw.get("minimum"), f"{where}.minimum"),
        min_price=_opt_dec(raw.get("min_price"), f"{where}.min_price"),
        max_price=_opt_dec(raw.get("max_price"), f"{where}.max_price"),
        when=when,
    )
    if rule.type == "percent" and rule.rate is None and not rule.rate_by_type:
        raise FeeConfigError(f"{where}: percent exige rate ou rate_by_type")
    if rule.type == "fixed" and rule.fixed is None and not rule.fixed_by_type:
        raise FeeConfigError(f"{where}: fixed exige fixed ou fixed_by_type")
    if rule.type == "band" and not rule.bands:
        raise FeeConfigError(f"{where}: band exige bands")
    if rule.type == "installments" and not rule.rate_by_installments:
        raise FeeConfigError(f"{where}: installments exige rate_by_installments")
    return rule


def load_fee_config(path: str | Path | None = None) -> FeeConfig:
    """Lê e valida o JSON de taxas; devolve objetos imutáveis."""
    path = Path(path or os.getenv("CHANNEL_FEES_PATH", "").strip() or DEFAULT_FEES_PATH)
    if not path.is_absolute():
        path = BASE_DIR / path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FeeConfigError(f"arquivo de taxas ilegível: {path.name}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_ID:
        raise FeeConfigError(f"schema deve ser {SCHEMA_ID}")
    raw_channels = data.get("channels")
    if not isinstance(raw_channels, dict) or not raw_channels:
        raise FeeConfigError("channels ausente")
    channels: dict[str, Channel] = {}
    for channel_id, raw in raw_channels.items():
        if not isinstance(raw, dict) or raw.get("kind") not in (
            "marketplace",
            "direct",
        ):
            raise FeeConfigError(f"{channel_id}: kind deve ser marketplace ou direct")
        rules = tuple(
            _parse_rule(item, f"channels.{channel_id}.rules")
            for item in raw.get("rules") or []
        )
        channels[channel_id] = Channel(
            channel_id, str(raw.get("label") or channel_id), raw["kind"], rules
        )
    common = tuple(
        _parse_rule(item, "common_rules") for item in data.get("common_rules") or []
    )
    requirements = data.get("requirements") or {}
    origin: dict[str, frozenset[str]] = {}
    for product_type, item in requirements.items():
        if product_type not in PRODUCT_TYPES or not isinstance(item, dict):
            raise FeeConfigError(f"requirements.{product_type}: inválido")
        listed = frozenset(item.get("domestic_origin_required_on") or ())
        if listed - set(channels):
            raise FeeConfigError(f"requirements.{product_type}: canal desconhecido")
        origin[product_type] = listed
    defaults = data.get("defaults") or {}
    margin = defaults.get("target_margin") or {"value": "0", "status": "ASSUMPTION"}
    reference = defaults.get("reference_channel") or "direct_pix"
    if reference not in channels:
        raise FeeConfigError("defaults.reference_channel: canal desconhecido")
    target_margin = _dec(margin.get("value"), "defaults.target_margin")
    if target_margin >= 1:
        raise FeeConfigError("defaults.target_margin precisa ser menor que 1")
    return FeeConfig(
        as_of=str(data.get("as_of") or ""),
        package_status=str(data.get("package_status") or "UNSPECIFIED"),
        channels=MappingProxyType(channels),
        common_rules=common,
        domestic_origin_required=MappingProxyType(origin),
        target_margin=target_margin,
        target_margin_status=str(margin.get("status") or "ASSUMPTION"),
        reference_channel=reference,
        path=path.name,
    )


@lru_cache(maxsize=4)
def _cached_config(path: str) -> FeeConfig:
    return load_fee_config(path)


def default_config() -> FeeConfig:
    return _cached_config(
        os.getenv("CHANNEL_FEES_PATH", "").strip() or str(DEFAULT_FEES_PATH)
    )


# -- cálculo --------------------------------------------------------------------


def product_type_of(product: Mapping[str, Any]) -> str:
    """jewelry, watch ou smartphone, a partir de categoria e coleção."""

    def value(name: str) -> str:
        raw = product.get(name)
        if isinstance(raw, dict):  # formato da descoberta: {"value": ...}
            raw = raw.get("value")
        if raw is None and isinstance(product.get("fields"), dict):
            nested = product["fields"].get(name)
            raw = nested.get("value") if isinstance(nested, dict) else nested
        return str(raw or "").casefold()

    text = " ".join(
        value(name)
        for name in ("product_type", "category", "subcategory", "collection")
    )
    if "smartphone" in text or "celular" in text or "iphone" in text:
        return "smartphone"
    if "jewel" in text or "feminine" in text or "joia" in text:
        return "jewelry"
    return "watch"


def _rule_amount(
    rule: FeeRule, product_type: str, price: Decimal, assumptions: Assumptions
) -> Decimal | None:
    """Valor do item, ou ``None`` quando a regra não vale para este preço."""
    if rule.min_price is not None and price < rule.min_price:
        return None
    if rule.max_price is not None and price >= rule.max_price:
        return None
    if rule.when == "tiktok_free_shipping" and not assumptions.tiktok_free_shipping:
        return None
    if rule.when == "seller_pays_installments" and (
        assumptions.installments_interest != "seller" or assumptions.installments <= 1
    ):
        return None
    if rule.type == "percent":
        rate = rule.rate_by_type.get(product_type, rule.rate)
        if rate is None:
            return None
        amount = price * rate
    elif rule.type == "fixed":
        amount = rule.fixed_by_type.get(product_type, rule.fixed)
        if amount is None:
            return None
    elif rule.type == "band":
        band = next((item for item in rule.bands if item.contains(price)), None)
        if band is None:
            return None
        amount = price * band.rate + band.fixed
    else:  # installments
        rate = rule.rate_by_installments.get(assumptions.installments)
        if rate is None:
            return None
        amount = price * rate
    if rule.cap is not None:
        amount = min(amount, rule.cap)
    if rule.minimum is not None:
        amount = max(amount, rule.minimum)
    return _money(amount)


def _status(statuses: list[str]) -> str:
    if "UNVERIFIED" in statuses:
        return "INCOMPLETE"
    if "ASSUMPTION" in statuses:
        return "ESTIMATED"
    return "COMPLETE"


def _blocked_by_origin(
    config: FeeConfig, channel: str, product_type: str, assumptions: Assumptions
) -> bool:
    required = config.domestic_origin_required.get(product_type, frozenset())
    return channel in required and not assumptions.watch_origin_confirmed


def _sobra(
    config: FeeConfig,
    channel: str,
    product_type: str,
    price: Decimal,
    assumptions: Assumptions,
) -> Decimal:
    rules = (*config.channels[channel].rules, *config.common_rules)
    total = Decimal(0)
    for rule in rules:
        amount = _rule_amount(rule, product_type, price, assumptions)
        total += amount or Decimal(0)
    return price - total


def evaluate(
    channel: str,
    product_type: str,
    price: Decimal | str | float,
    *,
    cost: Decimal | str | float | None = None,
    assumptions: Assumptions | None = None,
    config: FeeConfig | None = None,
    require_cost: bool = True,
) -> dict[str, Any]:
    """Uma venda de ``price`` em ``channel``: itens, sobra, lucro e status."""
    config = config or default_config()
    assumptions = assumptions or Assumptions.from_env()
    if channel not in config.channels:
        raise ValueError(f"canal_desconhecido:{channel}")
    if product_type not in PRODUCT_TYPES:
        raise ValueError(f"tipo_de_produto_desconhecido:{product_type}")
    price = _money(Decimal(str(price)))
    if price <= 0:
        raise ValueError("preco_invalido")
    cost_value = None if cost is None else _money(Decimal(str(cost)))
    margin = (
        assumptions.target_margin
        if assumptions.target_margin is not None
        else config.target_margin
    )
    items: list[dict[str, Any]] = []
    statuses: list[str] = []
    totals = dict.fromkeys(KINDS, Decimal(0))
    for rule in (*config.channels[channel].rules, *config.common_rules):
        amount = _rule_amount(rule, product_type, price, assumptions)
        if amount is None:
            continue
        status = rule.status_for(product_type)
        statuses.append(status)
        totals[rule.kind] += amount
        items.append(
            {
                "id": rule.id,
                "kind": rule.kind,
                "label": rule.label,
                "amount": f"{amount:.2f}",
                "status": status,
                "source": rule.source,
            }
        )
    fees = totals["channel_fee"] + totals["payment_fee"]
    shipping = totals["shipping"]
    other = totals["packaging"] + totals["tax"] + totals["returns"]
    sobra = price - fees - shipping - other
    target_amount = _money(price * margin)
    reasons = [
        f"{item['id']}:{item['status']}"
        for item in items
        if item["status"] in ("ASSUMPTION", "UNVERIFIED")
    ]
    status = _status(statuses)
    if cost_value is None and require_cost:
        status = "INCOMPLETE"
        reasons.append("COST_MISSING")
    lucro = None if cost_value is None else sobra - cost_value
    if _blocked_by_origin(config, channel, product_type, assumptions):
        status = "BLOCKED_BY_ORIGIN"
        reasons.append("ORIGIN_UNCONFIRMED")
    return {
        "channel": channel,
        "channel_label": config.channels[channel].label,
        "product_type": product_type,
        "price": f"{price:.2f}",
        "items": items,
        "fees": f"{fees:.2f}",
        "shipping": f"{shipping:.2f}",
        "other": f"{other:.2f}",
        "by_kind": {kind: f"{value:.2f}" for kind, value in totals.items()},
        "sobra": f"{sobra:.2f}",
        "lucro": None if lucro is None else f"{lucro:.2f}",
        "margem": None if lucro is None else f"{(lucro / price):.4f}",
        "target_margin": f"{margin:.4f}",
        "custo_maximo": f"{(sobra - target_amount):.2f}",
        "cpa_maximo": None if lucro is None else f"{(lucro - target_amount):.2f}",
        "status": status,
        "status_reasons": reasons,
        "fee_config": {"as_of": config.as_of, "package_status": config.package_status},
    }


def _search_price(predicate: Callable[[Decimal], bool]) -> Decimal | None:
    """Menor preço terminado em ,90 que satisfaz ``predicate``."""
    for reais in range(LINEAR_SEARCH_LIMIT):
        candidate = Decimal(reais) + Decimal("0.90")
        if predicate(candidate):
            return candidate
    low, high = LINEAR_SEARCH_LIMIT, SEARCH_CEILING
    if not predicate(Decimal(high) + Decimal("0.90")):
        return None
    while low < high:
        middle = (low + high) // 2
        if predicate(Decimal(middle) + Decimal("0.90")):
            high = middle
        else:
            low = middle + 1
    return Decimal(low) + Decimal("0.90")


def price_for_target_margin(
    channel: str,
    product_type: str,
    cost: Decimal | str | float,
    *,
    target_margin: Decimal | str | float | None = None,
    assumptions: Assumptions | None = None,
    config: FeeConfig | None = None,
) -> Decimal | None:
    """Menor preço ,90 em que ``(sobra − custo) / preço ≥ margem-alvo``."""
    config = config or default_config()
    assumptions = assumptions or Assumptions.from_env()
    margin = Decimal(
        str(
            target_margin
            if target_margin is not None
            else (
                assumptions.target_margin
                if assumptions.target_margin is not None
                else config.target_margin
            )
        )
    )
    cost_value = Decimal(str(cost))

    def ok(price: Decimal) -> bool:
        sobra = _sobra(config, channel, product_type, price, assumptions)
        return sobra - cost_value >= _money(price * margin)

    return _search_price(ok)


def price_to_match(
    channel: str,
    product_type: str,
    reference_price: Decimal | str | float,
    *,
    reference_channel: str | None = None,
    assumptions: Assumptions | None = None,
    config: FeeConfig | None = None,
) -> Decimal | None:
    """Menor preço ,90 em ``channel`` que deixa a mesma sobra do canal de referência.

    O custo do produto aparece dos dois lados e se cancela, então não é pedido.
    """
    config = config or default_config()
    assumptions = assumptions or Assumptions.from_env()
    reference_channel = reference_channel or config.reference_channel
    target = _sobra(
        config,
        reference_channel,
        product_type,
        _money(Decimal(str(reference_price))),
        assumptions,
    )
    return _search_price(
        lambda price: (
            _sobra(config, channel, product_type, price, assumptions) >= target
        )
    )


def channel_price_plan(
    product: Mapping[str, Any],
    *,
    cost: Decimal | str | float | None,
    price: Decimal | str | float | None,
    target_margin: Decimal | str | float | None = None,
    assumptions: Assumptions | None = None,
    config: FeeConfig | None = None,
) -> dict[str, Any]:
    """Para cada canal: preço mínimo da margem-alvo e preço que empata com o Pix."""
    config = config or default_config()
    assumptions = assumptions or Assumptions.from_env()
    product_type = product_type_of(product)
    plan = []
    for channel in config.channels:
        minimum = (
            None
            if cost is None
            else price_for_target_margin(
                channel,
                product_type,
                cost,
                target_margin=target_margin,
                assumptions=assumptions,
                config=config,
            )
        )
        match = (
            None
            if price is None
            else price_to_match(
                channel,
                product_type,
                price,
                assumptions=assumptions,
                config=config,
            )
        )
        current = (
            None
            if price is None
            else evaluate(
                channel,
                product_type,
                price,
                cost=cost,
                assumptions=assumptions,
                config=config,
            )
        )
        plan.append(
            {
                "channel": channel,
                "min_price_for_target_margin": None
                if minimum is None
                else str(minimum),
                "price_to_match_reference": None if match is None else str(match),
                "status": current["status"] if current else "INCOMPLETE",
                "status_reasons": current["status_reasons"] if current else [],
            }
        )
    return {
        "product_type": product_type,
        "reference_channel": config.reference_channel,
        "target_margin": str(
            target_margin if target_margin is not None else config.target_margin
        ),
        "channels": plan,
        "apply_requires": "PRICE_APPROVAL",
        "external_write": False,
    }


# Campos do EconomicsSnapshot (paid_media) que vêm do motor.
PAID_MEDIA_FIELDS = {
    "channel_fee": "channel_fee",
    "payment_fee": "payment_fee",
    "shipping": "shipping_cost_paid_by_veratus",
    "tax": "tax",
    "returns": "expected_returns_cost",
    "packaging": "packaging_cost",
}
