"""Link de pagamento do Mercado Pago e confirmação só pelo provedor.

- ``create_payment_link``: preferência com ``external_reference`` = id do pedido.
  Sem ``PAYMENT_LINKS_ENABLED=true`` e ``MERCADO_PAGO_ACCESS_TOKEN``, nada é
  criado: devolve a prévia e o motivo.
- ``verify_signature``: valida o cabeçalho ``x-signature`` do webhook
  (HMAC-SHA256 de ``id:<data.id>;request-id:<x-request-id>;ts:<ts>;``).
- ``confirm_payment``: lê o pagamento de volta na API e só então marca o pedido
  ``PAID``: status ``approved``, mesmo pedido, mesmo valor, em reais. Print ou
  mensagem do cliente nunca confirmam pagamento.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import requests

from .meta_webhooks import env_flag

API_BASE = "https://api.mercadopago.com"
SIGNATURE_TOLERANCE_SECONDS = 600


class PaymentError(RuntimeError):
    def __init__(self, code: str, http_status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


class HttpSession(Protocol):
    def post(self, url: str, **kwargs: Any) -> Any: ...
    def get(self, url: str, **kwargs: Any) -> Any: ...


def links_enabled() -> bool:
    return env_flag("PAYMENT_LINKS_ENABLED")


def installments_interest() -> str:
    value = os.getenv("PAYMENT_INSTALLMENTS_INTEREST", "buyer").strip().lower()
    return "seller" if value == "seller" else "buyer"


@dataclass(frozen=True)
class PaymentLinkRequest:
    order_id: str
    title: str
    amount: Decimal
    sku: str
    notification_url: str | None = None


class MercadoPagoClient:
    def __init__(
        self,
        access_token: str,
        *,
        session: HttpSession | None = None,
        timeout: int = 20,
    ) -> None:
        self.access_token = access_token
        self.session = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def from_env(cls, session: HttpSession | None = None) -> MercadoPagoClient | None:
        token = os.getenv("MERCADO_PAGO_ACCESS_TOKEN", "").strip()
        return cls(token, session=session) if token else None

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _error(response: Any) -> PaymentError:
        # Só o status HTTP viaja; nunca o corpo nem o token.
        return PaymentError(
            "MERCADO_PAGO_ERROR", getattr(response, "status_code", None)
        )

    def create_preference(self, link: PaymentLinkRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "external_reference": link.order_id,
            "items": [
                {
                    "id": link.sku,
                    "title": link.title[:120],
                    "quantity": 1,
                    "currency_id": "BRL",
                    "unit_price": float(link.amount),
                }
            ],
            # Juros do parcelado: pagos pelo cliente (decisão do fundador).
            "payment_methods": {"installments": 12, "default_installments": 1},
            "statement_descriptor": "VERATUS",
            "binary_mode": True,
        }
        if link.notification_url:
            body["notification_url"] = link.notification_url
        response = self.session.post(
            f"{API_BASE}/checkout/preferences",
            headers=self._headers(f"pref-{link.order_id}"),
            json=body,
            timeout=self.timeout,
        )
        if not response.ok:
            raise self._error(response)
        data = response.json()
        return {"preference_id": str(data["id"]), "url": str(data["init_point"])}

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        response = self.session.get(
            f"{API_BASE}/v1/payments/{payment_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        if not response.ok:
            raise self._error(response)
        return response.json()


def create_payment_link(
    link: PaymentLinkRequest,
    *,
    client: MercadoPagoClient | None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    enabled = links_enabled() if enabled is None else enabled
    reasons = []
    if not enabled:
        reasons.append("PAYMENT_LINKS_DISABLED")
    if client is None:
        reasons.append("MERCADO_PAGO_CREDENTIALS_MISSING")
    if link.amount <= 0:
        reasons.append("INVALID_AMOUNT")
    if reasons:
        return {
            "status": "BLOCKED",
            "reasons": reasons,
            "preview": {
                "order_id": link.order_id,
                "amount": f"{link.amount:.2f}",
                "installments_interest": installments_interest(),
            },
            "external_write": False,
        }
    created = client.create_preference(link)
    return {"status": "CREATED", **created, "external_write": True}


def verify_signature(
    *,
    signature_header: str | None,
    request_id: str | None,
    data_id: str | None,
    secret: str,
    now: float | None = None,
) -> bool:
    """Valida ``x-signature: ts=<epoch>,v1=<hex>`` do Mercado Pago."""
    if not secret or not signature_header or not data_id:
        return False
    parts = dict(
        item.strip().split("=", 1)
        for item in signature_header.split(",")
        if "=" in item
    )
    ts, received = parts.get("ts"), parts.get("v1")
    if not ts or not received or not ts.isdigit():
        return False
    if now is not None:
        stamp = int(ts) / (1000 if len(ts) > 11 else 1)
        if abs(now - stamp) > SIGNATURE_TOLERANCE_SECONDS:
            return False
    manifest = f"id:{data_id.lower()};"
    if request_id:
        manifest += f"request-id:{request_id};"
    manifest += f"ts:{ts};"
    expected = hmac.new(
        secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, received)


def payment_matches_order(payment: dict[str, Any], order: dict[str, Any]) -> list[str]:
    """Motivos pelos quais o pagamento lido de volta não confirma o pedido."""
    reasons = []
    if payment.get("status") != "approved":
        reasons.append("PAYMENT_NOT_APPROVED")
    if str(payment.get("external_reference") or "") != order["order_id"]:
        reasons.append("ORDER_MISMATCH")
    if payment.get("currency_id") != "BRL":
        reasons.append("CURRENCY_MISMATCH")
    expected = Decimal(str(order["sale_price"])) - Decimal(
        str(order.get("discount") or "0")
    )
    try:
        paid = Decimal(str(payment.get("transaction_amount")))
    except (ArithmeticError, ValueError):
        paid = Decimal(-1)
    if paid < expected:
        reasons.append("AMOUNT_MISMATCH")
    return reasons


def confirm_payment(
    payment_id: str,
    *,
    client: MercadoPagoClient,
    ledger: Any,
) -> dict[str, Any]:
    """Lê o pagamento no provedor e marca ``PAID`` só se tudo bater."""
    from .orders import OrderConflictError, OrderStatus, StatusChange

    payment = client.get_payment(payment_id)
    order_id = str(payment.get("external_reference") or "")
    order = ledger.get(order_id) if order_id else None
    if order is None:
        return {"status": "IGNORED", "reasons": ["ORDER_NOT_FOUND"]}
    reasons = payment_matches_order(payment, order)
    if reasons:
        return {"status": "NOT_CONFIRMED", "order_id": order_id, "reasons": reasons}
    if order["status"] != OrderStatus.PENDING.value:
        return {"status": "ALREADY_PROCESSED", "order_id": order_id}
    try:
        outcome, _updated = ledger.change_status(
            order_id,
            StatusChange(
                event_id=f"mp-{payment_id}-paid",
                status=OrderStatus.PAID,
                recorded_by="mercado-pago-webhook",
                evidence_ref=f"mercado_pago:payment:{payment_id}",
            ),
        )
    except OrderConflictError:
        return {"status": "ALREADY_PROCESSED", "order_id": order_id}
    return {"status": "PAID", "order_id": order_id, "outcome": outcome}
