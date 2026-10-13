"""Pricing Agent: preço por canal a partir do motor de economia.

Só recomenda. Aplicar um preço continua sendo um ``ApprovalRequest`` do tipo
``PRICE_APPROVAL``; nada é escrito em canal externo.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from . import channel_economics as engine
from .operations import ApprovalType, CommandEngine, request_approval

AGENT_ID = "pricing-agent"
ProductLookup = Callable[[str], dict[str, Any] | None]


def _value(product: dict[str, Any], *names: str) -> Decimal | None:
    for name in names:
        raw = product.get(name)
        if isinstance(raw, dict):
            raw = raw.get("value")
        if raw is None and isinstance(product.get("fields"), dict):
            nested = product["fields"].get(name)
            raw = nested.get("value") if isinstance(nested, dict) else nested
        if raw in (None, ""):
            continue
        try:
            return Decimal(str(raw))
        except InvalidOperation:
            continue
    return None


def product_price(product: dict[str, Any]) -> Decimal | None:
    return _value(product, "price_brl", "sale_price", "price")


def product_cost(product: dict[str, Any]) -> Decimal | None:
    return _value(product, "cost_brl", "cost", "unit_cost")


class PricingAgent:
    def __init__(
        self,
        lookup: ProductLookup,
        *,
        config: engine.FeeConfig | None = None,
        assumptions: engine.Assumptions | None = None,
    ) -> None:
        self.lookup = lookup
        self.config = config
        self.assumptions = assumptions

    def _product(self, sku: str) -> dict[str, Any]:
        product = self.lookup(sku)
        if product is None:
            raise KeyError(sku)
        return product

    def channel_price_plan(
        self, sku: str, *, target_margin: Decimal | None = None
    ) -> dict[str, Any]:
        product = self._product(sku)
        plan = engine.channel_price_plan(
            product,
            cost=product_cost(product),
            price=product_price(product),
            target_margin=target_margin,
            assumptions=self.assumptions,
            config=self.config,
        )
        return {"sku": sku, "agent": AGENT_ID, **plan}

    def request_price_change(
        self,
        command_engine: CommandEngine,
        sku: str,
        channel: str,
        price: Decimal,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Abre o pedido de aprovação; o preço só muda depois do fundador."""
        product = self._product(sku)
        config = self.config or engine.default_config()
        if channel not in config.channels:
            raise ValueError("canal_desconhecido")
        result = engine.evaluate(
            channel,
            engine.product_type_of(product),
            price,
            cost=product_cost(product),
            assumptions=self.assumptions,
            config=config,
        )
        approval = request_approval(
            command_engine,
            AGENT_ID,
            ApprovalType.PRICE_APPROVAL,
            reason,
            {
                "sku": sku,
                "channel": channel,
                "price": result["price"],
                "economics_status": result["status"],
                "status_reasons": result["status_reasons"],
                "external_write": False,
            },
        )
        return {
            "status": "PENDING_APPROVAL",
            "approval_id": approval.request_id,
            "external_write": False,
        }
