"""Pedidos de Shopee, TikTok Shop e Amazon no Orders Ledger, só pela leitura da API.

- Um pedido da plataforma = um pedido no ledger (``shopee-<order_sn>``,
  ``tiktok-shop-<id>``, ``amazon-<AmazonOrderId>``), criado PENDING e levado ao
  status lido na API por mudanças com ``event_id`` fixo: rodar de novo não
  duplica e nunca volta status.
- O pagamento vale só pela leitura do pedido na API da plataforma; push e
  webhook só entram na fila.
- Entra pedido pago (ou já enviado/entregue) de um SKU; a quantidade, quando
  passa de 1, fica no ``evidence_ref``. Vários SKUs, moeda que não é BRL ou
  valor ausente voltam ``BLOCKED`` para lançar à mão, como no Mercado Livre.
- Só ids, SKU, quantidade, valores e status: nada do comprador.
- Cada canal tem a sua chave (``*_SYNC_ENABLED``, false): desligada, o cliente
  nem é criado e nenhuma chamada sai. Sem cron: roda pela rota de admin.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from .marketplace_clients import MarketplaceApiError
from .meta_webhooks import env_flag
from .orders import (
    OrderChannel,
    OrderConflictError,
    OrderLedger,
    OrderRecord,
    OrderStatus,
    OrderTransitionError,
    PaymentMethod,
    StatusChange,
)

FLAGS = {
    "shopee": "SHOPEE_SYNC_ENABLED",
    "tiktok-shop": "TIKTOK_SHOP_SYNC_ENABLED",
    "amazon": "AMAZON_SYNC_ENABLED",
}
_LEDGER_CHANNEL = {
    "shopee": OrderChannel.SHOPEE,
    "tiktok-shop": OrderChannel.TIKTOK_SHOP,
    "amazon": OrderChannel.AMAZON,
}
MAX_HOURS = 360  # 15 dias: a janela máxima da Shopee
AMAZON_MAX_ITEM_READS = 30  # rajada do getOrderItems por rodada

_P, _S, _D = OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED
SHOPEE_STATUS = {
    "UNPAID": OrderStatus.PENDING,
    "READY_TO_SHIP": _P,
    "PROCESSED": _P,
    "RETRY_SHIP": _P,
    "INVOICE_PENDING": _P,
    "SHIPPED": _S,
    "TO_CONFIRM_RECEIVE": _S,
    "COMPLETED": _D,
    "CANCELLED": OrderStatus.CANCELED,
}
TIKTOK_SHOP_STATUS = {
    "UNPAID": OrderStatus.PENDING,
    "ON_HOLD": _P,
    "AWAITING_SHIPMENT": _P,
    "PARTIALLY_SHIPPING": _P,
    "AWAITING_COLLECTION": _P,
    "IN_TRANSIT": _S,
    "DELIVERED": _D,
    "COMPLETED": _D,
    "CANCELLED": OrderStatus.CANCELED,
}
AMAZON_STATUS = {
    "PendingAvailability": OrderStatus.PENDING,
    "Pending": OrderStatus.PENDING,
    "Unshipped": _P,
    "PartiallyShipped": _P,
    "Shipped": _S,
    "InvoiceUnconfirmed": _S,
    "Canceled": OrderStatus.CANCELED,
}
_FORWARD = (OrderStatus.PENDING, _P, _S, _D)
_RECORDABLE = frozenset({_P, _S, _D})


@dataclass(frozen=True)
class PlatformOrder:
    """Pedido lido na API, já sem nada do comprador."""

    channel: str
    order_id: str
    status: OrderStatus | None
    created_at: datetime | None = None
    currency: str | None = None
    lines: tuple[tuple[str, int], ...] = ()
    sale_price: Decimal = Decimal(0)
    discount: Decimal = Decimal(0)
    shipping_charged: Decimal = Decimal(0)

    @property
    def ledger_id(self) -> str:
        return f"{self.channel}-{self.order_id}"


def _money(value: Any) -> Decimal:
    """Valor da API (texto ou número); ausente, inválido ou negativo vira 0."""
    try:
        amount = Decimal(str(value)) if value not in (None, "") else Decimal(0)
    except InvalidOperation:
        return Decimal(0)
    if not amount.is_finite() or amount <= 0:
        return Decimal(0)
    return amount.quantize(Decimal("0.01"))


def _from_unix(value: Any) -> datetime | None:
    if isinstance(value, int) and value > 0:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return None


def _from_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _lines(pairs: Iterable[tuple[Any, Any]]) -> tuple[tuple[str, int], ...]:
    """(SKU, quantidade) somados por SKU."""
    totals: dict[str, int] = {}
    for sku, quantity in pairs:
        key = str(sku or "").strip()
        totals[key] = totals.get(key, 0) + (
            quantity if isinstance(quantity, int) and quantity > 0 else 0
        )
    return tuple(totals.items())


def shopee_order(detail: dict[str, Any]) -> PlatformOrder:
    items = detail.get("items") or []
    price = discount = Decimal(0)
    for item in items:
        quantity = item.get("quantity") if isinstance(item.get("quantity"), int) else 0
        original = _money(item.get("original_price"))
        paid = _money(item.get("discounted_price")) or original
        price += original * quantity
        discount += max(Decimal(0), original - paid) * quantity
    return PlatformOrder(
        "shopee",
        str(detail.get("order_sn") or ""),
        SHOPEE_STATUS.get(str(detail.get("order_status") or "")),
        _from_unix(detail.get("create_time")),
        detail.get("currency"),
        _lines((item.get("sku"), item.get("quantity")) for item in items),
        price,
        discount,
    )


def tiktok_shop_order(order: dict[str, Any]) -> PlatformOrder:
    lines = order.get("line_items") or []
    return PlatformOrder(
        "tiktok-shop",
        str(order.get("id") or ""),
        TIKTOK_SHOP_STATUS.get(str(order.get("status") or "")),
        _from_unix(order.get("create_time")),
        order.get("currency"),
        _lines((line.get("seller_sku"), 1) for line in lines),
        sum((_money(line.get("original_price")) for line in lines), Decimal(0)),
        sum((_money(line.get("seller_discount")) for line in lines), Decimal(0)),
        _money(order.get("shipping_fee")),
    )


def amazon_order(
    order: dict[str, Any], items: list[dict[str, Any]] | None = None
) -> PlatformOrder:
    items = items or []

    def total(field: str) -> Decimal:
        return sum(
            (_money((item.get(field) or {}).get("Amount")) for item in items),
            Decimal(0),
        )

    currencies = {
        (item.get("ItemPrice") or {}).get("CurrencyCode")
        for item in items
        if item.get("ItemPrice")
    }
    return PlatformOrder(
        "amazon",
        str(order.get("AmazonOrderId") or ""),
        AMAZON_STATUS.get(str(order.get("OrderStatus") or "")),
        _from_iso(order.get("PurchaseDate")),
        currencies.pop() if len(currencies) == 1 else None,
        _lines((item.get("SellerSKU"), item.get("QuantityOrdered")) for item in items),
        total("ItemPrice"),  # na Amazon, já é preço x quantidade
        total("PromotionDiscount"),
        total("ShippingPrice"),
    )


def block_reason(order: PlatformOrder) -> str | None:
    """Por que o pedido não entra sozinho no ledger (``None`` = entra)."""
    if not order.lines:
        return "NO_ITEMS"
    if len(order.lines) > 1:
        return "MULTI_SKU_ORDER"
    if not order.lines[0][0]:
        return "SKU_UNKNOWN"
    if order.lines[0][1] < 1:
        return "QUANTITY_UNKNOWN"
    if (order.currency or "").upper() != "BRL":
        return "CURRENCY_NOT_BRL"
    if order.sale_price <= 0:
        return "AMOUNT_MISSING"
    return None


def _steps(current: OrderStatus, target: OrderStatus | None) -> list[OrderStatus]:
    """Caminho para a frente até o status lido; nunca para trás."""
    if target == OrderStatus.CANCELED:
        return [target] if current in (OrderStatus.PENDING, _P) else []
    if target not in _FORWARD or current not in _FORWARD:
        return []
    return list(_FORWARD[_FORWARD.index(current) + 1 : _FORWARD.index(target) + 1])


def import_order(ledger: OrderLedger, order: PlatformOrder) -> dict[str, Any]:
    """Grava (uma vez) e leva o pedido ao status lido; repetir não muda nada."""
    ledger_id = order.ledger_id
    actor = f"{order.channel}-sync"
    evidence = f"{order.channel}:orders"
    result: dict[str, Any] = {"order_id": ledger_id, "status": "UNCHANGED"}
    current = ledger.get(ledger_id)
    if current is None:
        if order.status not in _RECORDABLE:
            # Pedido não pago (ou cancelado antes de pagar) não é venda.
            return {**result, "status": "SKIPPED", "reason": "NOT_PAID"}
        reason = block_reason(order)
        if reason:
            lines = [list(line) for line in order.lines]
            return {**result, "status": "BLOCKED", "reason": reason, "lines": lines}
        sku, quantity = order.lines[0]
        try:
            ledger.record(
                OrderRecord(
                    order_id=ledger_id,
                    created_at=order.created_at,
                    sku=sku,
                    channel=_LEDGER_CHANNEL[order.channel],
                    sale_price=order.sale_price,
                    discount=order.discount,
                    shipping_charged=order.shipping_charged,
                    payment_method=PaymentMethod.MARKETPLACE,
                    evidence_ref=evidence
                    + (f":qtd-{quantity}" if quantity > 1 else ""),
                    recorded_by=actor,
                )
            )
        except OrderConflictError:
            pass  # outra rodada gravou o mesmo pedido agora; segue pelo status
        except ValueError:
            return {**result, "status": "BLOCKED", "reason": "INVALID_ORDER"}
        current = ledger.get(ledger_id)
        result["status"] = "IMPORTED"
    for status in _steps(OrderStatus(current["status"]), order.status):
        try:
            change, current = ledger.change_status(
                ledger_id,
                StatusChange(
                    event_id=f"{ledger_id}:{status.value.lower()}",
                    status=status,
                    recorded_by=actor,
                    evidence_ref=evidence,
                ),
            )
        except (OrderConflictError, OrderTransitionError):
            return {**result, "status": "BLOCKED", "reason": "STATUS_CONFLICT"}
        if change == "changed" and result["status"] == "UNCHANGED":
            result["status"] = "SYNCED"
    return {**result, "order_status": current["status"]}


# -- leitura por canal ----------------------------------------------------------


def _read_shopee(
    ledger: OrderLedger, client: Any, start: int, end: int
) -> list[dict[str, Any]]:
    listed = client.get_order_list(start, end)
    # Detalhe (SKU e valores) só do pedido pago que ainda não está no ledger.
    new = [
        item["order_sn"]
        for item in listed
        if SHOPEE_STATUS.get(str(item.get("order_status"))) in _RECORDABLE
        and ledger.get(f"shopee-{item['order_sn']}") is None
    ]
    details = {item["order_sn"]: item for item in client.get_order_detail(new)}
    return [
        import_order(ledger, shopee_order(details.get(item["order_sn"]) or item))
        for item in listed
    ]


def _read_tiktok_shop(
    ledger: OrderLedger, client: Any, start: int, end: int
) -> list[dict[str, Any]]:
    return [
        import_order(ledger, tiktok_shop_order(order))
        for order in client.search_orders(start, end)
    ]


def _read_amazon(
    ledger: OrderLedger, client: Any, start: int, end: int
) -> list[dict[str, Any]]:
    del end
    entries: list[dict[str, Any]] = []
    reads = 0
    for raw in client.get_orders(datetime.fromtimestamp(start, tz=timezone.utc)):
        order = amazon_order(raw)
        if order.status in _RECORDABLE and ledger.get(order.ledger_id) is None:
            if reads >= AMAZON_MAX_ITEM_READS:
                entries.append(
                    {
                        "order_id": order.ledger_id,
                        "status": "DEFERRED",
                        "reason": "RATE",
                    }
                )
                continue
            reads += 1
            order = amazon_order(raw, client.get_order_items(order.order_id))
        entries.append(import_order(ledger, order))
    return entries


def _shopee_client() -> Any:
    from .shopee import shopee_read_client

    return shopee_read_client()


def _tiktok_shop_client() -> Any:
    from .tiktok_shop import tiktok_shop_client

    return tiktok_shop_client()


def _amazon_client() -> Any:
    from .amazon_sp import AmazonSellingPartnerClient

    return AmazonSellingPartnerClient.from_env()


_READERS = {
    "shopee": (_read_shopee, _shopee_client),
    "tiktok-shop": (_read_tiktok_shop, _tiktok_shop_client),
    "amazon": (_read_amazon, _amazon_client),
}


def sync_orders(
    channel: str,
    ledger: OrderLedger,
    *,
    client_factory: Callable[[], Any] | None = None,
    hours: int = 24,
    now: float | None = None,
) -> dict[str, Any]:
    """Lê os pedidos alterados nas últimas ``hours`` horas e grava no ledger."""
    if not env_flag(FLAGS[channel]):
        return {"status": "BLOCKED", "channel": channel, "reason": "SYNC_DISABLED"}
    reader, default_factory = _READERS[channel]
    end = int(time.time() if now is None else now)
    start = end - min(max(int(hours), 1), MAX_HOURS) * 3600
    try:
        entries = reader(ledger, (client_factory or default_factory)(), start, end)
    except MarketplaceApiError as exc:
        return {"status": "BLOCKED", "channel": channel, "reason": exc.code}
    except RuntimeError:  # loja não autorizada ou chave de cifra ausente
        return {"status": "BLOCKED", "channel": channel, "reason": "NOT_CONNECTED"}
    except requests.RequestException:
        return {"status": "BLOCKED", "channel": channel, "reason": "NETWORK_ERROR"}
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return {
        "status": "SYNCED",
        "channel": channel,
        "since": datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
        "orders_read": len(entries),
        "counts": counts,
        "orders": entries,
    }
