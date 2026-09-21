from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CommercialClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_type: Literal[
        "price",
        "availability",
        "shipping",
        "delivery",
        "warranty",
        "authenticity",
        "payment",
        "specification",
        "scarcity",
    ]
    value: str = Field(min_length=1, max_length=300)
    evidence_ref: str | None = Field(default=None, max_length=200)


class SalesDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    customer_reply: str = Field(min_length=1, max_length=1800)
    intent: Literal[
        "descoberta",
        "produto",
        "preco",
        "disponibilidade",
        "compra",
        "suporte",
        "outro",
    ]
    product_name: str | None = Field(default=None, max_length=120)
    lead_temperature: Literal["frio", "morno", "quente"] = "morno"
    missing_information: list[str] = Field(default_factory=list, max_length=12)
    next_action: Literal["revisao_humana", "confirmar_dados", "responder_duvida"] = (
        "revisao_humana"
    )
    internal_notes: list[str] = Field(default_factory=list, max_length=12)
    commercial_claims: list[CommercialClaim] = Field(
        default_factory=list, max_length=12
    )
    requires_human_review: Literal[True] = True


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    approved_for_human_review: bool
    status: Literal["approved_for_human_review", "needs_revision", "blocked"]
    issues: list[str] = Field(default_factory=list, max_length=20)
    corrected_reply: str | None = Field(default=None, max_length=1800)
    rationale: str = Field(default="", max_length=1200)

    @model_validator(mode="after")
    def decision_must_be_consistent(self) -> ReviewDecision:
        approved = self.status == "approved_for_human_review"
        if self.approved_for_human_review != approved:
            raise ValueError("status e approved_for_human_review são contraditórios")
        if self.status in {"needs_revision", "blocked"} and not self.issues:
            raise ValueError(
                "decisões não aprovadas precisam explicar ao menos um problema"
            )
        return self


class GateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    issues: list[str] = Field(default_factory=list, max_length=30)
    policy_version: Literal["commercial-v1"] = "commercial-v1"


class PerformanceRecord(BaseModel):
    """Observação imutável e documentada de desempenho comercial."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    occurred_on: date
    channel: str = Field(min_length=1, max_length=60)
    campaign: str = Field(default="sem_campanha", min_length=1, max_length=140)
    creative: str = Field(default="sem_criativo", min_length=1, max_length=140)
    impressions: int = Field(default=0, ge=0, le=2_000_000_000)
    clicks: int = Field(default=0, ge=0, le=2_000_000_000)
    leads: int = Field(default=0, ge=0, le=2_000_000_000)
    conversations: int = Field(default=0, ge=0, le=2_000_000_000)
    sales: int = Field(default=0, ge=0, le=2_000_000_000)
    spend_brl: Decimal = Field(default=Decimal(0), ge=0, decimal_places=2)
    revenue_brl: Decimal = Field(default=Decimal(0), ge=0, decimal_places=2)
    product_cost_brl: Decimal = Field(default=Decimal(0), ge=0, decimal_places=2)
    shipping_cost_brl: Decimal = Field(default=Decimal(0), ge=0, decimal_places=2)
    fees_brl: Decimal = Field(default=Decimal(0), ge=0, decimal_places=2)
    taxes_brl: Decimal = Field(default=Decimal(0), ge=0, decimal_places=2)
    refunds_brl: Decimal = Field(default=Decimal(0), ge=0, decimal_places=2)
    evidence: str = Field(min_length=1, max_length=500)
