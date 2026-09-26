from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from uuid import uuid4

from .operations import ApprovalStatus, ApprovalType, CommandEngine, CommandType


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _env_decimal(name: str, default: str) -> Decimal:
    """Unset or invalid values use the default; an explicit zero is kept.

    Negative limits collapse to zero so a typo can only reduce spend.
    """
    value = _decimal(os.getenv(name))
    if value is None or not value.is_finite():
        return Decimal(default)
    return max(value, Decimal(0))


def _field(product: dict[str, Any], name: str) -> Any:
    if name in product:
        return product[name]
    fields = product.get("fields")
    if isinstance(fields, dict) and isinstance(fields.get(name), dict):
        return fields[name].get("value")
    return None


class AutonomyMode(StrEnum):
    SHADOW = "SHADOW"
    SUPERVISED = "SUPERVISED"
    BOUNDED_AUTONOMOUS = "BOUNDED_AUTONOMOUS"


class WorkerState(StrEnum):
    OFFLINE = "OFFLINE"
    READY = "READY"
    PLANNING = "PLANNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    MONITORING = "MONITORING"
    OPTIMIZING = "OPTIMIZING"
    BLOCKED = "BLOCKED"
    INCIDENT = "INCIDENT"
    PAUSED = "PAUSED"


class CreativeReviewStatus(StrEnum):
    READY_FOR_TEST = "READY_FOR_TEST"
    NEEDS_REVISION = "NEEDS_REVISION"
    REJECT = "REJECT"


class PaidMediaAdapter(Protocol):
    channel: str

    def health(self) -> dict[str, Any]: ...

    def get_tracking_status(self) -> dict[str, Any]: ...

    def get_insights(self, date_range: dict[str, str]) -> dict[str, Any]: ...

    def read_back(self, resource_id: str) -> dict[str, Any]: ...


class MetaAdsAdapter:
    """Meta capability adapter.

    This first version is deliberately read-only and does not guess Graph API
    endpoints. Remote writes remain delegated to an approved connector/client.
    """

    channel = "meta"

    def health(self) -> dict[str, Any]:
        checks = {
            "access_token": bool(os.getenv("META_ACCESS_TOKEN", "").strip()),
            "ad_account_id": bool(os.getenv("META_AD_ACCOUNT_ID", "").strip()),
            "business_id": bool(os.getenv("META_BUSINESS_ID", "").strip()),
        }
        return {
            "channel": self.channel,
            "status": "CONFIGURED_READ_ONLY" if all(checks.values()) else "BLOCKED",
            "checks": checks,
            "external_writes": False,
            "api_contract": "CONNECTOR_REQUIRED_FOR_REMOTE_READBACK",
        }

    def get_tracking_status(self) -> dict[str, Any]:
        dataset_present = bool(
            os.getenv("META_DATASET_ID", "").strip()
            or os.getenv("META_PIXEL_ID", "").strip()
        )
        event_verified = _env_bool("META_PURCHASE_EVENT_VERIFIED", False)
        return {
            "channel": self.channel,
            "dataset_present": dataset_present,
            "purchase_event_verified": event_verified,
            "status": "HEALTHY" if dataset_present and event_verified else "UNVERIFIED",
            "conversion_spend_allowed": dataset_present and event_verified,
        }

    def get_insights(self, date_range: dict[str, str]) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "status": "NOT_CONNECTED",
            "date_range": date_range,
            "metrics": {},
            "external_read_performed": False,
        }

    def read_back(self, resource_id: str) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "resource_id": resource_id,
            "status": "NOT_CONNECTED",
            "verified": False,
        }


@dataclass(frozen=True)
class PaidMediaConfig:
    enabled: bool = True
    autonomy_mode: AutonomyMode = AutonomyMode.SHADOW
    live_writes: bool = False
    daily_budget_brl: Decimal = Decimal("20.00")
    test_max_spend_brl: Decimal = Decimal("140.00")
    max_budget_delta_pct: Decimal = Decimal(0)
    reconciliation_required: bool = True
    circuit_breaker_enabled: bool = True
    safety_factor: Decimal = Decimal("0.70")

    @classmethod
    def from_env(cls) -> PaidMediaConfig:
        mode_raw = os.getenv("PAID_MEDIA_AUTONOMY_MODE", "SHADOW").upper()
        try:
            mode = AutonomyMode(mode_raw)
        except ValueError:
            mode = AutonomyMode.SHADOW
        return cls(
            enabled=_env_bool("PAID_MEDIA_WORKER_ENABLED", True),
            autonomy_mode=mode,
            live_writes=_env_bool("PAID_MEDIA_LIVE_WRITES", False),
            daily_budget_brl=_env_decimal("PAID_MEDIA_DEFAULT_DAILY_BUDGET_BRL", "20"),
            test_max_spend_brl=_env_decimal("PAID_MEDIA_TEST_MAX_SPEND_BRL", "140"),
            max_budget_delta_pct=_env_decimal("PAID_MEDIA_MAX_BUDGET_DELTA_PCT", "0"),
            reconciliation_required=_env_bool(
                "PAID_MEDIA_RECONCILIATION_REQUIRED", True
            ),
            circuit_breaker_enabled=_env_bool(
                "PAID_MEDIA_CIRCUIT_BREAKER_ENABLED", True
            ),
            safety_factor=_safety_factor(),
        )


def _safety_factor() -> Decimal:
    # Above 1 the target CPA would exceed break-even (e.g. "7" typed for "0.7");
    # zero makes the economics INVALID, which blocks the plan instead.
    value = _env_decimal("PAID_MEDIA_SAFETY_FACTOR", "0.70")
    return value if value <= 1 else Decimal(0)


@dataclass
class PaidMediaSnapshot:
    worker_state: str = WorkerState.OFFLINE.value
    autonomy_mode: str = AutonomyMode.SHADOW.value
    last_seen: str | None = None
    current_task: str | None = None
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    experiments: list[dict[str, Any]] = field(default_factory=list)
    creative_reviews: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    reconciliations: list[dict[str, Any]] = field(default_factory=list)
    incidents: list[dict[str, Any]] = field(default_factory=list)
    shift_reports: list[dict[str, Any]] = field(default_factory=list)
    idempotency: dict[str, str] = field(default_factory=dict)


class PaidMediaStore:
    def __init__(self, persistence_path: str):
        self.url = (
            persistence_path
            if persistence_path.startswith(("postgresql://", "postgres://"))
            else None
        )
        self.path = None if self.url else Path(persistence_path)
        self.lock = Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot = self._load() or PaidMediaSnapshot()

    def _load(self) -> PaidMediaSnapshot | None:
        if self.url:
            import psycopg

            with psycopg.connect(self.url) as db:
                db.execute(
                    """CREATE TABLE IF NOT EXISTS paid_media_runtime_snapshot (
                    id INTEGER PRIMARY KEY CHECK (id=1), payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
                )
                row = db.execute(
                    "SELECT payload FROM paid_media_runtime_snapshot WHERE id=1"
                ).fetchone()
            if not row:
                return None
            data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return PaidMediaSnapshot(**data)
        if not self.path:
            return None
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS paid_media_runtime_snapshot (id INTEGER PRIMARY KEY CHECK (id=1), payload TEXT NOT NULL)"
            )
            row = db.execute(
                "SELECT payload FROM paid_media_runtime_snapshot WHERE id=1"
            ).fetchone()
        return PaidMediaSnapshot(**json.loads(row[0])) if row else None

    def save(self) -> None:
        payload = asdict(self.snapshot)
        with self.lock:
            if self.url:
                import psycopg
                from psycopg.types.json import Jsonb

                with psycopg.connect(self.url) as db:
                    db.execute(
                        """CREATE TABLE IF NOT EXISTS paid_media_runtime_snapshot (
                        id INTEGER PRIMARY KEY CHECK (id=1), payload JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
                    )
                    db.execute(
                        """INSERT INTO paid_media_runtime_snapshot (id,payload)
                        VALUES (1,%s) ON CONFLICT (id) DO UPDATE SET
                        payload=EXCLUDED.payload, updated_at=NOW()""",
                        (Jsonb(payload),),
                    )
                return
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS paid_media_runtime_snapshot (id INTEGER PRIMARY KEY CHECK (id=1), payload TEXT NOT NULL)"
                )
                db.execute(
                    "INSERT OR REPLACE INTO paid_media_runtime_snapshot VALUES (1,?)",
                    (json.dumps(payload, ensure_ascii=False),),
                )
                db.commit()

    def checkpoint(self, stage: str, data: dict[str, Any]) -> None:
        self.snapshot.last_seen = _now()
        self.snapshot.checkpoints.append(
            {"stage": stage, "timestamp": self.snapshot.last_seen, "data": data}
        )
        self.snapshot.checkpoints = self.snapshot.checkpoints[-100:]
        self.save()


def calculate_economics(
    product: dict[str, Any], safety_factor: Decimal = Decimal("0.70")
) -> dict[str, Any]:
    inputs = {
        "sale_price": _decimal(
            _field(product, "sale_price") or product.get("price_brl")
        ),
        "product_cost": _decimal(_field(product, "cost")),
        "payment_fees": _decimal(_field(product, "payment_fees")),
        "channel_fees": _decimal(_field(product, "channel_fees")),
        "shipping_subsidy": _decimal(_field(product, "shipping_subsidy")),
        "taxes": _decimal(_field(product, "taxes")),
        "expected_returns_cost": _decimal(_field(product, "expected_returns_cost")),
    }
    missing = [name for name, value in inputs.items() if value is None]
    if missing:
        return {
            "status": "INCOMPLETE",
            "inputs": {k: str(v) if v is not None else None for k, v in inputs.items()},
            "missing_fields": missing,
            "contribution_before_ads": None,
            "break_even_cpa": None,
            "target_cpa": None,
            "break_even_roas": None,
            "target_roas": None,
        }
    contribution = inputs["sale_price"] - sum(
        value for name, value in inputs.items() if name != "sale_price"
    )
    if contribution <= 0 or safety_factor <= 0:
        return {
            "status": "INVALID",
            "missing_fields": [],
            "contribution_before_ads": str(contribution),
        }
    target_cpa = contribution * safety_factor
    return {
        "status": "COMPLETE",
        "inputs": {k: str(v) for k, v in inputs.items()},
        "missing_fields": [],
        "safety_factor": str(safety_factor),
        "contribution_before_ads": f"{contribution:.2f}",
        "break_even_cpa": f"{contribution:.2f}",
        "target_cpa": f"{target_cpa:.2f}",
        "break_even_roas": f"{inputs['sale_price'] / contribution:.4f}",
        "target_roas": f"{inputs['sale_price'] / target_cpa:.4f}",
    }


def review_creative(
    product: dict[str, Any], creative: dict[str, Any]
) -> dict[str, Any]:
    image = creative.get("image") or next(iter(product.get("images") or []), None)
    dimensions = {
        "hook": bool(creative.get("hook")),
        "product": bool(image),
        "offer": bool(creative.get("offer")),
        "branding": creative.get("brand") == "Veratus",
        "message": bool(creative.get("message")),
        "proof": bool(creative.get("proof")),
        "cta": bool(creative.get("cta")),
        "format": creative.get("format") in {"1:1", "4:5", "9:16"},
        "landing_match": bool(creative.get("landing_match")),
        "compliance": creative.get("compliance") is True,
    }
    score = sum(dimensions.values())
    if not dimensions["product"] or not dimensions["compliance"]:
        status = CreativeReviewStatus.REJECT
    elif score == len(dimensions):
        status = CreativeReviewStatus.READY_FOR_TEST
    else:
        status = CreativeReviewStatus.NEEDS_REVISION
    return {
        "review_id": f"creative_review_{uuid4().hex}",
        "sku": product.get("sku") or product.get("id"),
        "status": status.value,
        "score": score,
        "dimensions": dimensions,
        "recommendations": [name for name, ok in dimensions.items() if not ok],
        "creative_dna": {
            "hook_type": creative.get("hook_type", "UNSET"),
            "angle": creative.get("angle", "premium_product"),
            "format": creative.get("format", "UNSET"),
            "duration": creative.get("duration"),
            "product_focus": creative.get("product_focus", "single_sku"),
            "offer": creative.get("offer"),
            "cta": creative.get("cta"),
            "copy_style": creative.get("copy_style", "restrained"),
            "proof_type": creative.get("proof_type", "product_detail"),
            "visual_density": creative.get("visual_density", "low"),
            "landing_match": dimensions["landing_match"],
        },
        "created_at": _now(),
    }


class PaidAcquisitionWorker:
    agent_id = "paid-acquisition-worker"

    def __init__(
        self,
        store: PaidMediaStore,
        command_engine: CommandEngine,
        *,
        adapter: PaidMediaAdapter | None = None,
        config: PaidMediaConfig | None = None,
    ) -> None:
        self.store = store
        self.engine = command_engine
        self.adapter = adapter or MetaAdsAdapter()
        self.config = config or PaidMediaConfig.from_env()
        self.store.snapshot.autonomy_mode = self.config.autonomy_mode.value
        if self.store.snapshot.worker_state == WorkerState.OFFLINE.value:
            self.store.snapshot.worker_state = (
                WorkerState.READY.value
                if self.config.enabled
                else WorkerState.PAUSED.value
            )
        self.store.save()

    def status(self) -> dict[str, Any]:
        adapter_health = self.adapter.health()
        tracking = self.adapter.get_tracking_status()
        return {
            "agent_id": self.agent_id,
            "enabled": self.config.enabled,
            "state": self.store.snapshot.worker_state,
            "autonomy_mode": self.config.autonomy_mode.value,
            "live_writes": self.config.live_writes,
            "last_seen": self.store.snapshot.last_seen,
            "current_task": self.store.snapshot.current_task,
            "daily_cap_brl": f"{self.config.daily_budget_brl:.2f}",
            "test_max_spend_brl": f"{self.config.test_max_spend_brl:.2f}",
            "adapter": adapter_health,
            "tracking": tracking,
            "circuit_breaker_enabled": self.config.circuit_breaker_enabled,
            "external_writes": False,
        }

    def heartbeat(self, current_task: str | None = None) -> dict[str, Any]:
        self.store.snapshot.last_seen = _now()
        self.store.snapshot.current_task = current_task
        self.store.save()
        return {
            "last_seen": self.store.snapshot.last_seen,
            "current_task": current_task,
        }

    def creative_review(
        self, product: dict[str, Any], creative: dict[str, Any]
    ) -> dict[str, Any]:
        result = review_creative(product, creative)
        self.store.snapshot.creative_reviews.append(result)
        self.store.checkpoint(
            "CREATIVE_REVIEW",
            {"review_id": result["review_id"], "status": result["status"]},
        )
        return result

    @staticmethod
    def _candidate(products: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates = [
            product
            for product in products
            if product.get("active", True)
            and not product.get("missing_fields")
            and (product.get("images") or _field(product, "images"))
        ]
        return candidates[0] if candidates else None

    def _experiment(self, experiment_id: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self.store.snapshot.experiments
                if item["experiment_id"] == experiment_id
            ),
            None,
        )

    def _replay(self, key: str) -> dict[str, Any] | None:
        existing = self.store.snapshot.idempotency.get(key)
        return self._experiment(existing) if existing else None

    def plan(
        self,
        products: list[dict[str, Any]],
        creative: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if idempotency_key and (replayed := self._replay(idempotency_key)):
            return replayed
        if not self.config.enabled:
            raise ValueError("paid_media_worker_disabled")
        candidate = self._candidate(products)
        if candidate is None:
            self._incident(
                "PRODUCT_NOT_READY", "Nenhum SKU comercialmente completo e ativo"
            )
            raise ValueError("no_commercially_ready_product")
        creative_input = creative or {
            "image": next(iter(candidate.get("images") or []), None),
            "brand": "Veratus",
            "message": candidate.get("description"),
            "format": "4:5",
            "landing_match": True,
            "compliance": True,
            "angle": "premium_product",
            "product_focus": "single_sku",
        }
        review = review_creative(candidate, creative_input)
        economics = calculate_economics(candidate, self.config.safety_factor)
        tracking = self.adapter.get_tracking_status()
        meta_health = self.adapter.health()
        blockers = []
        if economics["status"] != "COMPLETE":
            blockers.append("ECONOMICS_INCOMPLETE")
        if tracking["status"] != "HEALTHY":
            blockers.append("PURCHASE_TRACKING_UNVERIFIED")
        if meta_health["status"] != "CONFIGURED_READ_ONLY":
            blockers.append("META_READBACK_NOT_CONNECTED")
        if review["status"] != CreativeReviewStatus.READY_FOR_TEST.value:
            blockers.append("CREATIVE_NEEDS_REVISION")
        sku = candidate.get("sku") or candidate.get("id")
        # Without an explicit key, identical inputs replay the stored plan and any
        # change in blockers, economics, creative or caps yields a fresh plan.
        fingerprint = {
            "sku": sku,
            "blockers": blockers,
            "economics": economics.get("inputs"),
            "creative": creative_input,
            "caps": [
                str(self.config.daily_budget_brl),
                str(self.config.test_max_spend_brl),
                str(self.config.safety_factor),
            ],
        }
        key = (
            idempotency_key
            or "paid-media-shadow:"
            + hashlib.sha256(
                json.dumps(fingerprint, sort_keys=True, default=str).encode()
            ).hexdigest()[:24]
        )
        if replayed := self._replay(key):
            return replayed
        self.store.snapshot.worker_state = WorkerState.PLANNING.value
        self.heartbeat("PLAN_META_PILOT")
        experiment_id = f"experiment_{uuid4().hex}"
        plan = {
            "experiment_id": experiment_id,
            "status": "SHADOW_PLAN",
            "channel": "meta",
            "sku": sku,
            "hypothesis": "Uma apresentação premium de um único relógio pode gerar intenção qualificada sem diluir o orçamento piloto.",
            "campaigns": 1,
            "ad_groups": 1,
            "creative_limit": 2,
            "recommended_creative_review_id": review["review_id"],
            "creative_status": review["status"],
            "economics": economics,
            "tracking": tracking,
            "adapter": meta_health,
            "daily_budget_brl": f"{self.config.daily_budget_brl:.2f}",
            "max_spend_brl": f"{self.config.test_max_spend_brl:.2f}",
            "objective": "PURCHASE"
            if tracking["status"] == "HEALTHY"
            else "BLOCKED_UNTIL_TRACKING",
            "blockers": blockers,
            "approval_required": True,
            "external_write": False,
            "created_at": _now(),
        }
        # Engine bookkeeping first: if it fails, no half-built plan is stored
        # under this idempotency key.
        command = self.engine.submit(
            "founder",
            self.agent_id,
            CommandType.REPORT,
            {"experiment_id": experiment_id, "mode": "SHADOW"},
            f"paid-media:{key}",
        )
        task = self.engine.delegate(
            command, self.agent_id, "PLAN_META_PILOT", plan["sku"]
        )
        self.engine.tasks.start(task.task_id)
        self.engine.tasks.complete(
            task.task_id,
            {
                "experiment_id": experiment_id,
                "blockers": blockers,
                "external_write": False,
            },
        )
        # A blocked plan cannot be approved, so it opens no approval request.
        plan["approval_request_id"] = None
        if not blockers:
            approval = self.engine.approvals.request(
                self.agent_id,
                ApprovalType.PUBLISH_APPROVAL,
                "Primeiro teste Meta exige aprovação específica do fundador",
                {
                    "experiment_id": experiment_id,
                    "daily_budget_brl": plan["daily_budget_brl"],
                    "max_spend_brl": plan["max_spend_brl"],
                },
            )
            plan["approval_request_id"] = approval.request_id
        self.engine._persist()
        self.store.snapshot.creative_reviews.append(review)
        self.store.snapshot.experiments.append(plan)
        self.store.snapshot.idempotency[key] = experiment_id
        self.store.snapshot.worker_state = (
            WorkerState.WAITING_APPROVAL.value
            if not blockers
            else WorkerState.BLOCKED.value
        )
        self.store.snapshot.current_task = None
        self.store.checkpoint(
            "PLAN_CREATED", {"experiment_id": experiment_id, "blockers": blockers}
        )
        self._shift_report(plan)
        return plan

    def _bound_approval(self, experiment_id: str, approval_request_id: str | None):
        approval = (
            self.engine.approvals.requests.get(approval_request_id)
            if approval_request_id
            else None
        )
        if (
            approval is None
            or approval.approval_type != ApprovalType.PUBLISH_APPROVAL
            or approval.payload.get("experiment_id") != experiment_id
        ):
            return None
        return approval

    def approve(
        self, experiment_id: str, approval_request_id: str, *, actor: str
    ) -> dict[str, Any]:
        plan = self._experiment(experiment_id)
        if plan is None:
            raise ValueError("experiment_not_found")
        if plan.get("blockers"):
            raise ValueError("plan_has_open_blockers")
        approval = self._bound_approval(experiment_id, approval_request_id)
        if approval is None:
            raise ValueError("approval_not_found")
        if approval.status is ApprovalStatus.PENDING:
            approval.payload.update({"resolved_by": actor, "resolved_at": _now()})
            approval = self.engine.approvals.resolve(
                approval_request_id, ApprovalStatus.APPROVED
            )
            self.engine._persist()
            self.store.checkpoint(
                "APPROVAL_RESOLVED",
                {
                    "experiment_id": experiment_id,
                    "approval_request_id": approval_request_id,
                    "actor": actor,
                },
            )
        return {
            "experiment_id": experiment_id,
            "approval": approval.status.value,
            "external_write": False,
        }

    def execute(
        self,
        experiment_id: str,
        approval_request_id: str | None,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        plan = self._experiment(experiment_id)
        if not plan:
            raise ValueError("experiment_not_found")
        approval = self._bound_approval(experiment_id, approval_request_id)
        reasons = []
        if self.config.autonomy_mode is AutonomyMode.SHADOW:
            reasons.append("SHADOW_MODE_NO_WRITE")
        if not self.config.live_writes:
            reasons.append("PAID_MEDIA_LIVE_WRITES_DISABLED")
        if approval is None or approval.status is not ApprovalStatus.APPROVED:
            reasons.append("APPROVAL_REQUIRED")
        reasons.extend(plan.get("blockers", []))
        if Decimal(plan["daily_budget_brl"]) > self.config.daily_budget_brl:
            reasons.append("DAILY_CAP_EXCEEDED")
        if Decimal(plan["max_spend_brl"]) > self.config.test_max_spend_brl:
            reasons.append("TEST_CAP_EXCEEDED")
        if reasons:
            self.store.snapshot.worker_state = WorkerState.BLOCKED.value
            self.store.checkpoint(
                "EXECUTION_BLOCKED",
                {"experiment_id": experiment_id, "reasons": sorted(set(reasons))},
            )
            return {
                "status": "BLOCKED",
                "experiment_id": experiment_id,
                "reasons": sorted(set(reasons)),
                "external_write": False,
            }
        return {
            "status": "BLOCKED",
            "experiment_id": experiment_id,
            "reasons": ["REMOTE_META_WRITE_ADAPTER_NOT_ENABLED"],
            "external_write": False,
            "idempotency_key": hashlib.sha256(idempotency_key.encode()).hexdigest()[
                :16
            ],
        }

    def sync(self) -> dict[str, Any]:
        self.store.snapshot.worker_state = WorkerState.MONITORING.value
        health = self.adapter.health()
        tracking = self.adapter.get_tracking_status()
        insights = self.adapter.get_insights({"preset": "today"})
        blockers = []
        if health["status"] != "CONFIGURED_READ_ONLY":
            blockers.append("META_READBACK_NOT_CONNECTED")
        if tracking["status"] != "HEALTHY":
            blockers.append("PURCHASE_TRACKING_UNVERIFIED")
        if blockers:
            self.store.snapshot.worker_state = WorkerState.BLOCKED.value
        else:
            self.store.snapshot.worker_state = WorkerState.READY.value
        result = {
            "health": health,
            "tracking": tracking,
            "insights": insights,
            "blockers": blockers,
            "external_write": False,
        }
        self.store.checkpoint("READ_ONLY_SYNC", result)
        return result

    def _incident(self, kind: str, detail: str) -> dict[str, Any]:
        item = {
            "incident_id": f"incident_{uuid4().hex}",
            "type": kind,
            "detail": detail,
            "status": "OPEN",
            "created_at": _now(),
        }
        self.store.snapshot.incidents.append(item)
        self.store.snapshot.worker_state = WorkerState.INCIDENT.value
        self.store.save()
        return item

    def _shift_report(self, plan: dict[str, Any]) -> dict[str, Any]:
        report = {
            "report_id": f"shift_{uuid4().hex}",
            "mode": self.config.autonomy_mode.value,
            "health": self.store.snapshot.worker_state,
            "current_experiment": plan["experiment_id"],
            "sku": plan["sku"],
            "channel": plan["channel"],
            "spend_today_brl": "0.00",
            "daily_cap_brl": plan["daily_budget_brl"],
            "purchases_platform": 0,
            "purchases_reconciled": 0,
            "real_revenue_brl": "0.00",
            "target_cpa": plan["economics"].get("target_cpa"),
            "current_cpa": None,
            "target_roas": plan["economics"].get("target_roas"),
            "current_roas": None,
            "creative_status": plan["creative_status"],
            "decision": "ESCALATE" if plan["blockers"] else "KEEP_MONITORING",
            "evidence": {"mode": "SHADOW", "external_readback": False},
            "action_executed": "SHADOW_PLAN_CREATED",
            "action_requiring_approval": "Remove blockers, approve exact Meta draft and R$20/day cap",
            "incidents": [
                item["incident_id"]
                for item in self.store.snapshot.incidents
                if item["status"] == "OPEN"
            ],
            "next_check": "after Meta readback, Purchase event validation and complete economics",
            "created_at": _now(),
        }
        self.store.snapshot.shift_reports.append(report)
        self.store.save()
        return report

    def latest_shift_report(self) -> dict[str, Any] | None:
        return (
            self.store.snapshot.shift_reports[-1]
            if self.store.snapshot.shift_reports
            else None
        )
