from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from threading import Lock
from typing import Any, ClassVar, Protocol
from uuid import uuid4


class Priority(StrEnum):
    ROUTINE = "ROUTINE"
    HIGH = "HIGH"
    CEO_OVERRIDE = "CEO_OVERRIDE"


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    BLOCKED = "BLOCKED"
    ESCALATED = "ESCALATED"


class AgentStatus(StrEnum):
    OFFLINE = "OFFLINE"
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class ApprovalType(StrEnum):
    PRODUCT_APPROVAL = "PRODUCT_APPROVAL"
    PRICE_APPROVAL = "PRICE_APPROVAL"
    PUBLISH_APPROVAL = "PUBLISH_APPROVAL"
    DESTRUCTIVE_ACTION = "DESTRUCTIVE_ACTION"
    CREDENTIAL_CHANGE = "CREDENTIAL_CHANGE"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class CommandType(StrEnum):
    PREPARE_DISTRIBUTION = "PREPARE_DISTRIBUTION"
    CREATE_DRAFT = "CREATE_DRAFT"
    RUN_QA = "RUN_QA"
    REPORT = "REPORT"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    CEO_OVERRIDE = "CEO_OVERRIDE"


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    name: str
    level: int
    team: str
    supervisor: str | None
    permissions: frozenset[str]
    external_write: bool = False
    mission: str = ""
    capabilities: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    read_scope: tuple[str, ...] = ()
    write_scope: tuple[str, ...] = ()
    escalation_rules: tuple[str, ...] = ()
    status: AgentStatus = AgentStatus.IDLE


@dataclass
class AuditEvent:
    event_id: str
    timestamp: str
    actor: str
    action: str
    target: str
    status: str
    command_id: str | None = None
    task_id: str | None = None
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    task_id: str
    command_id: str
    agent: str
    action: str
    target: str
    priority: Priority
    status: TaskStatus = TaskStatus.QUEUED
    parent_task_id: str | None = None
    depends_on: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    child_tasks: list[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 2
    timeout_seconds: int = 300
    started_at: str | None = None
    next_retry: str | None = None
    last_error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class Command:
    command_id: str
    issuer: str
    target: str
    command_type: CommandType
    payload: dict[str, Any]
    priority: Priority
    status: TaskStatus = TaskStatus.QUEUED


@dataclass
class AgentRuntime:
    agent_id: str
    status: AgentStatus = AgentStatus.IDLE
    current_task: str | None = None
    last_task: str | None = None
    started_at: str | None = None
    last_activity: str | None = None
    error: str | None = None


@dataclass
class ApprovalRequest:
    request_id: str
    requested_by: str
    approval_type: ApprovalType
    reason: str
    payload: dict[str, Any]
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class DependencyError(ValueError):
    pass


class ConcurrencyError(ValueError):
    pass


class ApprovalEngine:
    def __init__(self) -> None:
        self.requests: dict[str, ApprovalRequest] = {}

    def request(
        self,
        requested_by: str,
        approval_type: ApprovalType,
        reason: str,
        payload: dict[str, Any],
    ) -> ApprovalRequest:
        item = ApprovalRequest(
            f"approval_{uuid4().hex}", requested_by, approval_type, reason, payload
        )
        self.requests[item.request_id] = item
        return item

    def resolve(self, request_id: str, status: ApprovalStatus) -> ApprovalRequest:
        item = self.requests[request_id]
        if item.status is not ApprovalStatus.PENDING:
            raise ValueError("approval já resolvida")
        item.status = status
        return item


AGENT_REGISTRY: dict[str, AgentDefinition] = {
    "general-manager": AgentDefinition(
        "general-manager",
        "General Manager",
        1,
        "management",
        None,
        frozenset({"DELEGATE", "REPORT", "PAUSE", "RESUME"}),
    ),
    "product-supervisor": AgentDefinition(
        "product-supervisor",
        "Product Supervisor",
        2,
        "product",
        "general-manager",
        frozenset({"DELEGATE", "REPORT", "RUN_QA"}),
    ),
    "distribution-supervisor": AgentDefinition(
        "distribution-supervisor",
        "Distribution Supervisor",
        2,
        "distribution",
        "general-manager",
        frozenset({"DELEGATE", "REPORT", "CREATE_DRAFT"}),
    ),
    "product-intake": AgentDefinition(
        "product-intake",
        "Product Intake",
        3,
        "product",
        "product-supervisor",
        frozenset({"READ_PRODUCT", "WRITE_PRODUCT"}),
    ),
    "copy-agent": AgentDefinition(
        "copy-agent",
        "Copy Agent",
        3,
        "product",
        "product-supervisor",
        frozenset({"READ_PRODUCT", "WRITE_COPY"}),
    ),
    "creative-agent": AgentDefinition(
        "creative-agent",
        "Creative Agent",
        3,
        "product",
        "product-supervisor",
        frozenset({"READ_PRODUCT", "WRITE_CREATIVE"}),
    ),
    "pricing-agent": AgentDefinition(
        "pricing-agent",
        "Pricing Agent",
        3,
        "product",
        "product-supervisor",
        frozenset({"READ_PRODUCT", "READ_COST", "WRITE_PRICE_RECOMMENDATION"}),
    ),
    "quality-agent": AgentDefinition(
        "quality-agent",
        "Quality Agent",
        3,
        "product",
        "product-supervisor",
        frozenset({"READ_PRODUCT", "RUN_QA"}),
    ),
    "mercado-livre-agent": AgentDefinition(
        "mercado-livre-agent",
        "Mercado Livre Agent",
        3,
        "distribution",
        "distribution-supervisor",
        frozenset({"READ_APPROVED_PRODUCT", "CREATE_DRAFT"}),
    ),
    "shopee-agent": AgentDefinition(
        "shopee-agent",
        "Shopee Agent",
        3,
        "distribution",
        "distribution-supervisor",
        frozenset({"READ_APPROVED_PRODUCT", "CREATE_DRAFT"}),
    ),
    "tiktok-shop-agent": AgentDefinition(
        "tiktok-shop-agent",
        "TikTok Shop Agent",
        3,
        "distribution",
        "distribution-supervisor",
        frozenset({"READ_APPROVED_PRODUCT", "CREATE_DRAFT"}),
    ),
    "meta-agent": AgentDefinition(
        "meta-agent",
        "Meta Agent",
        3,
        "distribution",
        "distribution-supervisor",
        frozenset({"READ_APPROVED_PRODUCT", "CREATE_DRAFT"}),
    ),
    "sync-monitor-agent": AgentDefinition(
        "sync-monitor-agent",
        "Sync Monitor Agent",
        3,
        "distribution",
        "distribution-supervisor",
        frozenset({"READ_APPROVED_PRODUCT", "REPORT"}),
    ),
}

_TEAM_CONTRACTS = {
    "general-manager": (
        "planejar, delegar, monitorar e consolidar",
        ("plan", "delegate", "report"),
        ("task-engine", "audit-log"),
        ("product-master", "task-context"),
        ("tasks", "external-action"),
    ),
    "product-supervisor": (
        "coordenar intake, copy, criativo, preço e QA",
        ("assign", "retry", "escalate"),
        ("task-engine", "audit-log"),
        ("product-master",),
        ("missing-input", "timeout"),
    ),
    "distribution-supervisor": (
        "coordenar drafts e sync",
        ("assign", "retry", "escalate"),
        ("task-engine", "audit-log"),
        ("approved-products",),
        ("external-action", "timeout"),
    ),
}
for _agent_id, _definition in list(AGENT_REGISTRY.items()):
    _mission, _capabilities, _tools, _read, _escalations = _TEAM_CONTRACTS.get(
        _agent_id,
        (
            f"executar responsabilidades de {_definition.name}",
            ("execute",),
            ("task-engine", "audit-log"),
            ("assigned-context",),
            ("missing-input", "timeout", "failure"),
        ),
    )
    AGENT_REGISTRY[_agent_id] = replace(
        _definition,
        mission=_mission,
        capabilities=_capabilities,
        tools=_tools,
        read_scope=_read,
        write_scope=tuple(
            _definition.permissions
            & {
                "WRITE_PRODUCT",
                "WRITE_COPY",
                "WRITE_CREATIVE",
                "WRITE_PRICE_RECOMMENDATION",
                "CREATE_DRAFT",
            }
        ),
        escalation_rules=_escalations,
    )


class PermissionError(ValueError):
    pass


class TaskEngine:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.audit: list[AuditEvent] = []
        self.runtimes = {
            agent_id: AgentRuntime(agent_id) for agent_id in AGENT_REGISTRY
        }
        self.resource_locks: dict[str, Lock] = {}
        self.persist_callback = None

    def create(
        self,
        command: Command,
        agent: str,
        action: str,
        target: str,
        *,
        parent_task_id: str | None = None,
        depends_on: list[str] | None = None,
        timeout_seconds: int = 300,
    ) -> Task:
        task_id = f"task_{uuid4().hex}"
        dependencies = depends_on or []
        blocked = [
            item
            for item in dependencies
            if item not in self.tasks
            or self.tasks[item].status is not TaskStatus.COMPLETED
        ]
        task = Task(
            task_id,
            command.command_id,
            agent,
            action,
            target,
            command.priority,
            TaskStatus.BLOCKED if blocked else TaskStatus.ASSIGNED,
            parent_task_id=parent_task_id,
            depends_on=dependencies,
            blocked_by=blocked,
            timeout_seconds=timeout_seconds,
        )
        self.tasks[task_id] = task
        if parent_task_id and parent_task_id in self.tasks:
            self.tasks[parent_task_id].child_tasks.append(task_id)
        self.record(
            command.issuer,
            "TASK_CREATED",
            target,
            "BLOCKED" if blocked else "SUCCESS",
            command.command_id,
            task_id,
            after={"depends_on": dependencies},
        )
        if not blocked:
            self.record(
                agent, "TASK_ASSIGNED", target, "SUCCESS", command.command_id, task_id
            )
        self.persist_callback and self.persist_callback()
        return task

    def start(self, task_id: str) -> Task:
        task = self.tasks[task_id]
        if task.blocked_by and any(
            self.tasks[item].status is not TaskStatus.COMPLETED
            for item in task.blocked_by
        ):
            raise DependencyError("dependências ainda não concluídas")
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc).isoformat()
        runtime = self.runtimes[task.agent]
        (
            runtime.status,
            runtime.current_task,
            runtime.started_at,
            runtime.last_activity,
        ) = AgentStatus.RUNNING, task_id, task.started_at, task.started_at
        self.record(
            task.agent, "TASK_STARTED", task.target, "SUCCESS", task.command_id, task_id
        )
        self.persist_callback and self.persist_callback()
        return task

    def record(
        self,
        actor: str,
        action: str,
        target: str,
        status: str,
        command_id: str | None = None,
        task_id: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        self.audit.append(
            AuditEvent(
                f"evt_{uuid4().hex}",
                datetime.now(timezone.utc).isoformat(),
                actor,
                action,
                target,
                status,
                command_id,
                task_id,
                before or {},
                after or {},
            )
        )

    def complete(self, task_id: str, result: dict[str, Any]) -> Task:
        task = self.tasks[task_id]
        task.status, task.result = TaskStatus.COMPLETED, result
        runtime = self.runtimes[task.agent]
        (
            runtime.status,
            runtime.last_task,
            runtime.current_task,
            runtime.last_activity,
        ) = AgentStatus.IDLE, task_id, None, datetime.now(timezone.utc).isoformat()
        self.record(
            task.agent,
            "TASK_COMPLETED",
            task.target,
            "SUCCESS",
            task.command_id,
            task.task_id,
            after=result,
        )
        self.persist_callback and self.persist_callback()
        return task

    def fail(self, task_id: str, error: str) -> Task:
        task = self.tasks[task_id]
        task.last_error = error
        task.errors.append(error)
        if task.retry_count < task.max_retries:
            task.retry_count += 1
            task.status = TaskStatus.QUEUED
            task.next_retry = (
                datetime.now(timezone.utc) + timedelta(seconds=2**task.retry_count)
            ).isoformat()
        else:
            task.status = TaskStatus.ESCALATED
        runtime = self.runtimes[task.agent]
        runtime.status, runtime.error, runtime.current_task = (
            AgentStatus.ERROR,
            error,
            None,
        )
        self.record(
            task.agent,
            "TASK_FAILED",
            task.target,
            task.status.value,
            task.command_id,
            task.task_id,
            after={"retry_count": task.retry_count},
        )
        self.persist_callback and self.persist_callback()
        return task

    def timeout(self, task_id: str) -> Task:
        self.record(
            self.tasks[task_id].agent,
            "TASK_TIMEOUT",
            self.tasks[task_id].target,
            "FAILED",
            self.tasks[task_id].command_id,
            task_id,
        )
        return self.fail(task_id, "TASK_TIMEOUT")

    def status(self) -> list[dict[str, Any]]:
        return [asdict(runtime) for runtime in self.runtimes.values()]


class CommandEngine:
    def __init__(
        self, task_engine: TaskEngine | None = None, persistence_path: str | None = None
    ) -> None:
        self.tasks = task_engine or TaskEngine()
        self.commands: dict[str, Command] = {}
        self.idempotency: dict[str, str] = {}
        self.approvals = ApprovalEngine()
        self.overrides: set[str] = set()
        self.persistence_url = (
            persistence_path
            if persistence_path
            and persistence_path.startswith(("postgresql://", "postgres://"))
            else None
        )
        self.persistence_path = None if self.persistence_url else persistence_path
        self.tasks.persist_callback = self._persist
        self._load()

    def _write_snapshot(self, payload: str) -> None:
        if self.persistence_url:
            import psycopg
            from psycopg.types.json import Jsonb

            with psycopg.connect(self.persistence_url) as db:
                db.execute(
                    """CREATE TABLE IF NOT EXISTS operational_runtime_snapshot (
                    id INTEGER PRIMARY KEY CHECK (id=1), payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
                )
                db.execute(
                    """INSERT INTO operational_runtime_snapshot (id, payload)
                    VALUES (1, %s) ON CONFLICT (id) DO UPDATE SET
                    payload=EXCLUDED.payload, updated_at=NOW()""",
                    (Jsonb(json.loads(payload)),),
                )
            return
        if self.persistence_path:
            with sqlite3.connect(self.persistence_path) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS runtime_snapshot (id INTEGER PRIMARY KEY CHECK (id=1), payload TEXT NOT NULL)"
                )
                db.execute(
                    "INSERT OR REPLACE INTO runtime_snapshot VALUES (1, ?)",
                    (payload,),
                )
                db.commit()

    def _read_snapshot(self) -> dict[str, Any] | None:
        if self.persistence_url:
            import psycopg

            with psycopg.connect(self.persistence_url) as db:
                db.execute(
                    """CREATE TABLE IF NOT EXISTS operational_runtime_snapshot (
                    id INTEGER PRIMARY KEY CHECK (id=1), payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
                )
                row = db.execute(
                    "SELECT payload FROM operational_runtime_snapshot WHERE id=1"
                ).fetchone()
            if not row:
                return None
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        if not self.persistence_path:
            return None
        with sqlite3.connect(self.persistence_path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runtime_snapshot (id INTEGER PRIMARY KEY CHECK (id=1), payload TEXT NOT NULL)"
            )
            row = db.execute(
                "SELECT payload FROM runtime_snapshot WHERE id=1"
            ).fetchone()
        return json.loads(row[0]) if row else None

    def _persist(self) -> None:
        if not self.persistence_path and not self.persistence_url:
            return
        payload = json.dumps(
            self.snapshot(),
            default=lambda value: value.value if isinstance(value, StrEnum) else value,
        )
        self._write_snapshot(payload)

    def _load(self) -> None:
        if not self.persistence_path and not self.persistence_url:
            return
        raw = self._read_snapshot()
        if raw is None:
            return
        for item in raw.get("commands", []):
            command = Command(
                item["command_id"],
                item["issuer"],
                item["target"],
                CommandType(item["command_type"]),
                item["payload"],
                Priority(item["priority"]),
                TaskStatus(item["status"]),
            )
            self.commands[command.command_id] = command
        for item in raw.get("tasks", []):
            task = Task(
                item["task_id"],
                item["command_id"],
                item["agent"],
                item["action"],
                item["target"],
                Priority(item["priority"]),
                TaskStatus(item["status"]),
                item.get("parent_task_id"),
                item.get("depends_on", []),
                item.get("blocked_by", []),
                item.get("child_tasks", []),
                item.get("retry_count", 0),
                item.get("max_retries", 2),
                item.get("timeout_seconds", 300),
                item.get("started_at"),
                item.get("next_retry"),
                item.get("last_error"),
                item.get("result", {}),
                item.get("errors", []),
            )
            self.tasks.tasks[task.task_id] = task
        self.tasks.audit = [AuditEvent(**item) for item in raw.get("audit", [])]
        self.idempotency = raw.get("idempotency", {})
        self.overrides = set(raw.get("overrides", []))
        for item in raw.get("approvals", []):
            approval = ApprovalRequest(
                item["request_id"],
                item["requested_by"],
                ApprovalType(item["approval_type"]),
                item["reason"],
                item["payload"],
                ApprovalStatus(item["status"]),
                item.get("created_at", datetime.now(timezone.utc).isoformat()),
            )
            self.approvals.requests[approval.request_id] = approval
        for agent_id, runtime in raw.get("runtimes", {}).items():
            if agent_id in self.tasks.runtimes:
                self.tasks.runtimes[agent_id] = AgentRuntime(**runtime)

    def submit(
        self,
        issuer: str,
        target: str,
        command_type: CommandType,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> Command:
        if idempotency_key in self.idempotency:
            return self.commands[self.idempotency[idempotency_key]]
        priority = (
            Priority.CEO_OVERRIDE if issuer in {"founder", "ceo"} else Priority.ROUTINE
        )
        command = Command(
            f"cmd_{uuid4().hex}", issuer, target, command_type, payload, priority
        )
        self.commands[command.command_id] = command
        self.idempotency[idempotency_key] = command.command_id
        self.tasks.record(
            issuer,
            "COMMAND_CREATED",
            target,
            "SUCCESS",
            command.command_id,
            after={"priority": priority.value},
        )
        if command_type is CommandType.CEO_OVERRIDE:
            self.overrides.add(str(payload.get("sku", payload.get("target", ""))))
            self.tasks.record(
                issuer, "CEO_OVERRIDE", target, "SUCCESS", command.command_id
            )
        self._persist()
        return command

    def authorize(self, agent: str, permission: str) -> None:
        definition = AGENT_REGISTRY.get(agent)
        if definition is None or permission not in definition.permissions:
            raise PermissionError(f"{agent} não possui {permission}")

    def delegate(
        self, command: Command, agent: str, action: str, target: str, **kwargs: Any
    ) -> Task:
        if command.priority is Priority.CEO_OVERRIDE and command.issuer in {
            "founder",
            "ceo",
        }:
            self.tasks.record(
                command.issuer, "CEO_OVERRIDE", target, "SUCCESS", command.command_id
            )
        return self.tasks.create(command, agent, action, target, **kwargs)

    def parse(self, message: str) -> Command:
        normalized = message.lower().strip()
        target = (
            "general-manager" if "gerente" in normalized else "distribution-supervisor"
        )
        command_type = (
            CommandType.PREPARE_DISTRIBUTION
            if "prepare" in normalized or "distribui" in normalized
            else CommandType.REPORT
        )
        return self.submit(
            "founder",
            target,
            command_type,
            {"message": message},
            f"natural:{normalized}",
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "commands": [asdict(item) for item in self.commands.values()],
            "tasks": [asdict(item) for item in self.tasks.tasks.values()],
            "approvals": [asdict(item) for item in self.approvals.requests.values()],
            "audit": [asdict(item) for item in self.tasks.audit],
            "runtimes": {
                key: asdict(value) for key, value in self.tasks.runtimes.items()
            },
            "idempotency": self.idempotency,
            "overrides": list(self.overrides),
        }


def simulate_prepare_distribution(
    product_count: int,
    channels: tuple[str, ...] = ("mercado-livre", "shopee", "tiktok-shop", "meta"),
    blocked_sku: str | None = None,
) -> dict[str, Any]:
    engine = CommandEngine()
    command = engine.submit(
        "founder",
        "general-manager",
        CommandType.PREPARE_DISTRIBUTION,
        {"product_count": product_count},
        "simulate-prepare-distribution-v1",
    )
    if blocked_sku:
        engine.submit(
            "founder",
            "shopee-agent",
            CommandType.CEO_OVERRIDE,
            {"sku": blocked_sku, "action": "PUBLISH"},
            f"override:{blocked_sku}",
        )
    manager = engine.delegate(command, "general-manager", "PLAN", "approved-products")
    engine.tasks.start(manager.task_id)
    engine.tasks.complete(
        manager.task_id,
        {"delegated_to": ["product-supervisor", "distribution-supervisor"]},
    )
    product = engine.delegate(
        command,
        "product-supervisor",
        "COORDINATE_PRODUCT_QA",
        "approved-products",
        depends_on=[manager.task_id],
    )
    engine.tasks.start(product.task_id)
    engine.tasks.complete(product.task_id, {"status": "APPROVED"})
    distribution = engine.delegate(
        command,
        "distribution-supervisor",
        "DISTRIBUTE_DRAFTS",
        "approved-products",
        depends_on=[product.task_id],
    )
    engine.tasks.start(distribution.task_id)
    engine.tasks.complete(
        distribution.task_id, {"channels": list(channels), "external_write": False}
    )
    drafts = []
    for channel in channels:
        agent = (
            channel + "-agent" if channel != "mercado-livre" else "mercado-livre-agent"
        )
        task = engine.delegate(
            command,
            agent,
            "CREATE_DRAFT",
            f"approved-products:{channel}",
            depends_on=[distribution.task_id],
        )
        engine.tasks.start(task.task_id)
        if blocked_sku and channel == "shopee":
            engine.tasks.fail(task.task_id, f"CEO_OVERRIDE:{blocked_sku}")
            task.status = TaskStatus.BLOCKED
            continue
        engine.tasks.complete(
            task.task_id, {"mode": "DRAFT", "channel": channel, "external_write": False}
        )
        drafts.append(channel)
    sync = engine.delegate(
        command,
        "sync-monitor-agent",
        "SYNC_CHECK",
        "distribution",
        depends_on=[
            task.task_id
            for task in engine.tasks.tasks.values()
            if task.action == "CREATE_DRAFT" and task.status is TaskStatus.COMPLETED
        ],
    )
    engine.tasks.start(sync.task_id)
    engine.tasks.complete(sync.task_id, {"conflicts": []})
    command.status = TaskStatus.COMPLETED
    return {
        "command_id": command.command_id,
        "status": "COMPLETED",
        "tasks": [task.__dict__ for task in engine.tasks.tasks.values()],
        "drafts": drafts,
        "audit": [event.__dict__ for event in engine.tasks.audit],
    }


class ProductSource(Protocol):
    def list_products(
        self, *, status: Any = None, limit: int = 100
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class ParsedCommand:
    handler: str
    channels: tuple[str, ...] = ()
    collection: str | None = None


class OperationalRuntime:
    """Deterministic 13-agent runtime.

    The runtime deliberately stops at persisted local drafts. An external API write is
    never performed here; publishing belongs behind the approval and write guard.
    """

    CHANNEL_AGENTS: ClassVar[dict[str, str]] = {
        "mercado-livre": "mercado-livre-agent",
        "shopee": "shopee-agent",
        "tiktok-shop": "tiktok-shop-agent",
        "meta": "meta-agent",
    }

    def __init__(
        self,
        persistence_path: str,
        *,
        product_source: ProductSource
        | Callable[[], list[dict[str, Any]]]
        | None = None,
        marketplace_store: Any | None = None,
        connection_service: Any | None = None,
        external_writes_enabled: bool = False,
    ) -> None:
        if external_writes_enabled:
            raise ValueError("external writes must remain behind ExternalWriteGuard")
        self.engine = CommandEngine(persistence_path=persistence_path)
        self.product_source = product_source
        self.marketplace_store = marketplace_store
        self.connection_service = connection_service
        self.external_writes_enabled = False

    @staticmethod
    def parse_command(message: str) -> ParsedCommand:
        normalized = " ".join(message.casefold().strip().split())
        if not normalized:
            raise ValueError("command is required")
        if (
            any(
                token in normalized
                for token in ("analise", "analisar", "diagnóstico", "diagnostico")
            )
            and "estado atual" in normalized
            and "produto" in normalized
            and "marketplace" in normalized
        ):
            return ParsedCommand(
                "operational_audit", tuple(OperationalRuntime.CHANNEL_AGENTS)
            )
        if "cadastre" in normalized and "feminin" in normalized:
            return ParsedCommand("intake_feminine", collection="feminine")
        if (
            "prepare" in normalized
            and "feminin" in normalized
            and any(token in normalized for token in ("marketplace", "mercado livre"))
        ):
            return ParsedCommand(
                "prepare_feminine",
                tuple(OperationalRuntime.CHANNEL_AGENTS),
                "feminine",
            )
        if "prepare" in normalized and any(
            token in normalized
            for token in (
                "marketplace",
                "mercado livre",
                "produto",
                "relógio",
                "relogio",
            )
        ):
            channels = tuple(
                channel
                for token, channel in (
                    ("mercado livre", "mercado-livre"),
                    ("shopee", "shopee"),
                    ("tiktok", "tiktok-shop"),
                    ("meta", "meta"),
                )
                if token in normalized
            )
            return ParsedCommand(
                "prepare", channels or tuple(OperationalRuntime.CHANNEL_AGENTS)
            )
        if "sincronize" in normalized or "sincronizar" in normalized:
            return ParsedCommand("sync")
        if (
            "mercado livre" in normalized
            and any(
                token in normalized
                for token in ("valide", "validar", "conexão", "conexao")
            )
            and "prontid" in normalized
        ):
            return ParsedCommand("refresh_mercado_livre", ("mercado-livre",))
        if "marketplaces conectados" in normalized and "prontid" in normalized:
            return ParsedCommand("refresh_connections")
        if "produtos incompletos" in normalized:
            return ParsedCommand("products_incomplete")
        if "produtos prontos" in normalized:
            return ParsedCommand("products_ready")
        if "canais conectados" in normalized:
            return ParsedCommand("channels")
        if "aprova" in normalized and "pendente" in normalized:
            return ParsedCommand("approvals")
        if "erros" in normalized:
            return ParsedCommand("errors")
        for token, channel in (
            ("mercado livre", "mercado-livre"),
            ("shopee", "shopee"),
            ("tiktok", "tiktok-shop"),
            ("meta", "meta"),
        ):
            if "pend" in normalized and token in normalized:
                return ParsedCommand("channel_pending", (channel,))
        raise ValueError("unsupported command")

    def execute(
        self,
        message: str,
        *,
        issuer: str = "founder",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        parsed = self.parse_command(message)
        key = idempotency_key or f"runtime:{' '.join(message.casefold().split())}"
        kind = (
            CommandType.PREPARE_DISTRIBUTION
            if parsed.handler in {"prepare", "prepare_feminine"}
            else CommandType.REPORT
        )
        command = self.engine.submit(
            issuer,
            "general-manager",
            kind,
            {
                "message": message,
                "handler": parsed.handler,
                "channels": list(parsed.channels),
            },
            key,
        )
        prior = [
            task
            for task in self.engine.tasks.tasks.values()
            if task.command_id == command.command_id
        ]
        if prior and command.status in {
            TaskStatus.COMPLETED,
            TaskStatus.BLOCKED,
            TaskStatus.ESCALATED,
        }:
            return self._report(command)
        try:
            if parsed.handler == "prepare":
                self._execute_prepare(command, parsed.channels)
            elif parsed.handler == "intake_feminine":
                self._execute_feminine_intake(command)
            elif parsed.handler == "prepare_feminine":
                self._execute_feminine_distribution(command, parsed.channels)
            else:
                self._execute_report(command, parsed)
            command.status = TaskStatus.COMPLETED
        except Exception as exc:
            command.status = TaskStatus.ESCALATED
            self.engine.tasks.record(
                "general-manager",
                "COMMAND_ESCALATED",
                command.target,
                "ESCALATED",
                command.command_id,
                after={"error": type(exc).__name__},
            )
            self.engine._persist()
            raise
        self.engine._persist()
        return self._report(command)

    def _products(self) -> list[dict[str, Any]]:
        if self.product_source is None:
            from .catalog import load_catalog

            products = load_catalog()
        elif callable(self.product_source) and not hasattr(
            self.product_source, "list_products"
        ):
            products = self.product_source()
        else:
            products = self.product_source.list_products(limit=500)  # type: ignore[union-attr]
        return [
            item
            for item in products
            if str(item.get("status", "ACTIVE")).upper()
            not in {"PAUSED", "OUT_OF_STOCK"}
        ]

    def _run_task(
        self,
        command: Command,
        agent: str,
        action: str,
        target: str,
        result: dict[str, Any],
        **kwargs: Any,
    ) -> Task:
        task = self.engine.delegate(command, agent, action, target, **kwargs)
        self.engine.tasks.start(task.task_id)
        self.engine.tasks.complete(task.task_id, result)
        return task

    def _product_readiness(self, product: dict[str, Any]) -> tuple[bool, list[str]]:
        required = {
            "identifier": product.get("sku") or product.get("id"),
            "name": self._field_value(product, "name"),
            "category": self._field_value(product, "category"),
            "sale_price": self._field_value(product, "sale_price")
            or self._field_value(product, "price_brl"),
            "cost": self._field_value(product, "cost")
            or self._field_value(product, "cost_brl"),
        }
        images = self._field_value(product, "images")
        if not images:
            image = self._field_value(product, "image")
            images = [image] if image else []
        required["images"] = images
        missing = [name for name, value in required.items() if value in (None, "", [])]
        status = str(product.get("status", "ACTIVE")).upper()
        if status not in {"ACTIVE", "APPROVED", "PUBLISHED"}:
            missing.append("approved_status")
        return not missing, missing

    def _readiness_inventory(
        self, products: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        eligible: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for product in products:
            ready, missing = self._product_readiness(product)
            identifier = str(
                product.get("sku") or product.get("id") or product.get("name")
            )
            if ready:
                eligible.append(product)
            else:
                blocked.append({"product": identifier, "missing_fields": missing})
        return eligible, blocked

    def _execute_prepare(self, command: Command, channels: tuple[str, ...]) -> None:
        candidates = [
            item
            for item in self._products()
            if str(item.get("status", "ACTIVE")).upper()
            in {"ACTIVE", "APPROVED", "PUBLISHED"}
        ]
        products, readiness_blocked = self._readiness_inventory(candidates)
        identifiers = [
            str(item.get("sku") or item.get("id") or item.get("name"))
            for item in products
        ]
        manager = self._run_task(
            command,
            "general-manager",
            "CREATE_EXECUTION_PLAN",
            "active-watches",
            {
                "products": identifiers,
                "channels": list(channels),
                "external_writes": "BLOCKED",
            },
        )
        product_supervisor = self._run_task(
            command,
            "product-supervisor",
            "COORDINATE_PRODUCT_TEAM",
            "active-watches",
            {
                "delegated_agents": [
                    "product-intake",
                    "copy-agent",
                    "creative-agent",
                    "pricing-agent",
                    "quality-agent",
                ]
            },
            parent_task_id=manager.task_id,
            depends_on=[manager.task_id],
        )
        intake = self._run_task(
            command,
            "product-intake",
            "VALIDATE_PRODUCT_INPUT",
            "active-watches",
            {"processed": len(products), "source": "PRODUCT_MASTER"},
            parent_task_id=product_supervisor.task_id,
            depends_on=[product_supervisor.task_id],
        )
        copy = self._run_task(
            command,
            "copy-agent",
            "BUILD_CHANNEL_COPY",
            "active-watches",
            {
                "processed": len(products),
                "channels": list(channels),
                "unsupported_claims": 0,
            },
            parent_task_id=product_supervisor.task_id,
            depends_on=[intake.task_id],
        )
        creative = self._run_task(
            command,
            "creative-agent",
            "VALIDATE_CREATIVE_ASSETS",
            "active-watches",
            {
                "processed": len(products),
                "assets_verified": sum(
                    bool(item.get("images") or item.get("image")) for item in products
                ),
            },
            parent_task_id=product_supervisor.task_id,
            depends_on=[intake.task_id],
        )
        pricing = self._run_task(
            command,
            "pricing-agent",
            "CALCULATE_CHANNEL_PRICING",
            "active-watches",
            {
                "processed": len(products),
                "products": [
                    {
                        "product": str(item.get("sku") or item.get("id")),
                        "sale_price_brl": self._field_value(item, "sale_price")
                        or self._field_value(item, "price_brl"),
                        "cost_brl": self._field_value(item, "cost")
                        or self._field_value(item, "cost_brl"),
                    }
                    for item in products
                ],
                "blocked": readiness_blocked,
                "fees": "ESTIMATED",
            },
            parent_task_id=product_supervisor.task_id,
            depends_on=[intake.task_id],
        )
        quality = self._run_task(
            command,
            "quality-agent",
            "RUN_PRODUCT_AND_CHANNEL_QA",
            "active-watches",
            {
                "processed": len(products),
                "eligible": identifiers,
                "blocked": readiness_blocked,
                "state": "PRODUCT_READY" if identifiers else "NO_ELIGIBLE_PRODUCTS",
            },
            parent_task_id=product_supervisor.task_id,
            depends_on=[copy.task_id, creative.task_id, pricing.task_id],
        )
        distribution = self._run_task(
            command,
            "distribution-supervisor",
            "COORDINATE_CHANNEL_DRAFTS",
            "approved-products",
            {"channels": list(channels), "independent_failures": True},
            parent_task_id=manager.task_id,
            depends_on=[quality.task_id],
        )
        channel_tasks: list[Task] = []
        for channel in channels:
            if channel not in self.CHANNEL_AGENTS:
                raise ValueError(f"unsupported channel: {channel}")
            drafts = []
            blocked = []
            for product, product_id in zip(products, identifiers, strict=True):
                if channel == "shopee" and "black-gmt" in product_id.casefold():
                    blocked.append({"product": product_id, "reason": "BLOCKED_BY_CEO"})
                    if self.marketplace_store is not None:
                        from .marketplace_ops import ListingState

                        self.marketplace_store.override(
                            product_id,
                            channel,
                            "PUBLISH",
                            "DO_NOT_PUBLISH: CEO override",
                        )
                        self.marketplace_store.upsert_listing(
                            product_id,
                            channel,
                            ListingState.BLOCKED,
                            payload=self._draft_payload(product, product_id, channel),
                            error="BLOCKED_BY_CEO",
                        )
                else:
                    draft = {"product": product_id, "state": "DRAFT"}
                    if self.marketplace_store is not None:
                        from .marketplace_adapters import Marketplace
                        from .marketplace_ops import prepare_listing

                        listing = prepare_listing(
                            self.marketplace_store,
                            self._draft_product(product, product_id),
                            Marketplace(channel),
                            self._draft_payload(product, product_id, channel),
                        )
                        draft["listing_id"] = listing["id"]
                    drafts.append(draft)
            channel_tasks.append(
                self._run_task(
                    command,
                    self.CHANNEL_AGENTS[channel],
                    "PREPARE_CANONICAL_LISTING",
                    channel,
                    {
                        "channel": channel,
                        "drafts": drafts,
                        "blocked": blocked,
                        "external_write": "BLOCKED_BY_GLOBAL_FLAG",
                    },
                    parent_task_id=distribution.task_id,
                    depends_on=[distribution.task_id],
                )
            )
        sync = self._run_task(
            command,
            "sync-monitor-agent",
            "SYNC_DRAFTS",
            "all-channels",
            {
                "checked_channels": list(channels),
                "conflicts": [],
                "external_write": False,
            },
            parent_task_id=distribution.task_id,
            depends_on=[item.task_id for item in channel_tasks],
        )
        self._run_task(
            command,
            "general-manager",
            "CONSOLIDATE_MANAGER_REPORT",
            "execution-plan",
            {
                "product_supervisor": product_supervisor.task_id,
                "distribution_supervisor": distribution.task_id,
                "sync": sync.task_id,
                "status": "COMPLETED",
                "eligible_products": identifiers,
                "blocked_products": readiness_blocked,
            },
            parent_task_id=manager.task_id,
            depends_on=[sync.task_id],
        )

    @staticmethod
    def _field_value(product: dict[str, Any], name: str, default: Any = None) -> Any:
        if name in product:
            return product[name]
        fields = product.get("fields")
        if isinstance(fields, dict) and isinstance(fields.get(name), dict):
            return fields[name].get("value", default)
        return default

    def _draft_product(self, product: dict[str, Any], sku: str) -> dict[str, Any]:
        return {
            "sku": sku,
            "name": self._field_value(product, "name", sku),
            "category": self._field_value(product, "category", "accessories"),
            "subcategory": self._field_value(product, "subcategory"),
            "status": "APPROVED",
        }

    def _draft_payload(
        self, product: dict[str, Any], sku: str, channel: str
    ) -> dict[str, Any]:
        images = self._field_value(product, "images")
        if not images:
            image = self._field_value(product, "image")
            images = [image] if image else []
        price = self._field_value(product, "price_brl")
        if price is None:
            price = self._field_value(product, "sale_price")
        return {
            "sku": sku,
            "channel": channel,
            "title": self._field_value(product, "name", sku),
            "description": self._field_value(product, "description", ""),
            "price_brl": price,
            "inventory_mode": "ON_DEMAND",
            "channel_stock_cap": 20,
            "images": images,
            "category": self._field_value(product, "category", "accessories"),
            "category_mapping_status": "CATEGORY_DISCOVERY_BLOCKED_BY_CREDENTIALS",
            "client_status": "API_CONTRACT_UNVERIFIED",
            "external_write": False,
        }

    def _feminine_products(self) -> list[dict[str, Any]]:
        return [
            item
            for item in self._products()
            if self._field_value(item, "collection") == "feminine"
        ]

    def _execute_feminine_intake(self, command: Command) -> None:
        products = self._feminine_products()
        identifiers = [str(item.get("sku") or item.get("id")) for item in products]
        manager = self._run_task(
            command,
            "general-manager",
            "CREATE_FEMININE_IMPORT_PLAN",
            "feminine",
            {"products": identifiers, "external_writes": "BLOCKED"},
        )
        supervisor = self._run_task(
            command,
            "product-supervisor",
            "COORDINATE_FEMININE_PRODUCT_TEAM",
            "feminine",
            {
                "delegated_agents": [
                    "product-intake",
                    "copy-agent",
                    "creative-agent",
                    "pricing-agent",
                    "quality-agent",
                ]
            },
            parent_task_id=manager.task_id,
            depends_on=[manager.task_id],
        )
        intake = self._run_task(
            command,
            "product-intake",
            "PRODUCT_IDENTITY_REVIEW",
            "feminine",
            {
                "assets_found": 11,
                "unique_products": len(products),
                "alternate_images_not_imported": 2,
                "products": identifiers,
            },
            parent_task_id=supervisor.task_id,
            depends_on=[supervisor.task_id],
        )
        copy = self._run_task(
            command,
            "copy-agent",
            "BUILD_EVIDENCE_SAFE_COPY",
            "feminine",
            {"processed": len(products), "unsupported_claims": 0},
            parent_task_id=supervisor.task_id,
            depends_on=[intake.task_id],
        )
        creative = self._run_task(
            command,
            "creative-agent",
            "VALIDATE_FEMININE_ASSETS",
            "feminine",
            {
                "processed": len(products),
                "assets_verified": sum(bool(item.get("images")) for item in products),
                "duplicates_excluded": 2,
            },
            parent_task_id=supervisor.task_id,
            depends_on=[intake.task_id],
        )
        pricing = self._run_task(
            command,
            "pricing-agent",
            "RECOMMEND_PRICE_PENDING_COST",
            "feminine",
            {
                "processed": len(products),
                "state": "PRICE_RECOMMENDATION_PENDING_COST",
                "needs_pricing": identifiers,
            },
            parent_task_id=supervisor.task_id,
            depends_on=[intake.task_id],
        )
        quality = self._run_task(
            command,
            "quality-agent",
            "RUN_FEMININE_PRODUCT_QA",
            "feminine",
            {
                "processed": len(products),
                "approved": [],
                "needs_information": identifiers,
                "false_material_claims": 0,
                "false_gemstone_claims": 0,
            },
            parent_task_id=supervisor.task_id,
            depends_on=[copy.task_id, creative.task_id, pricing.task_id],
        )
        self._run_task(
            command,
            "general-manager",
            "CONSOLIDATE_FEMININE_IMPORT_REPORT",
            "feminine",
            {
                "products_created": len(products),
                "approved": 0,
                "needs_pricing": len(products),
                "needs_information": len(products),
                "external_writes": False,
            },
            parent_task_id=manager.task_id,
            depends_on=[quality.task_id],
        )

    def _execute_feminine_distribution(
        self, command: Command, channels: tuple[str, ...]
    ) -> None:
        products = self._feminine_products()
        approved = [
            item
            for item in products
            if str(item.get("status", "")).upper() == "APPROVED"
        ]
        blocked = [
            {
                "product": str(item.get("sku") or item.get("id")),
                "reason": "BLOCKED_NEEDS_PRICING",
            }
            for item in products
            if item not in approved
        ]
        manager = self._run_task(
            command,
            "general-manager",
            "PLAN_FEMININE_DISTRIBUTION",
            "feminine",
            {
                "approved": len(approved),
                "blocked_needs_pricing": len(blocked),
                "external_writes": "BLOCKED",
            },
        )
        distribution = self._run_task(
            command,
            "distribution-supervisor",
            "FILTER_APPROVED_FEMININE_PRODUCTS",
            "feminine",
            {"eligible": len(approved), "blocked": blocked},
            parent_task_id=manager.task_id,
            depends_on=[manager.task_id],
        )
        channel_tasks = []
        for channel in channels:
            channel_tasks.append(
                self._run_task(
                    command,
                    self.CHANNEL_AGENTS[channel],
                    "PREPARE_FEMININE_DRAFTS",
                    channel,
                    {
                        "drafts": [],
                        "blocked": blocked,
                        "category_discovery": [
                            "necklace",
                            "bracelet",
                            "anklet",
                        ],
                        "external_write": "BLOCKED_BY_GLOBAL_FLAG",
                    },
                    parent_task_id=distribution.task_id,
                    depends_on=[distribution.task_id],
                )
            )
        sync = self._run_task(
            command,
            "sync-monitor-agent",
            "SYNC_FEMININE_DRAFT_STATUS",
            "feminine",
            {"drafts": 0, "blocked": len(blocked), "external_write": False},
            parent_task_id=distribution.task_id,
            depends_on=[task.task_id for task in channel_tasks],
        )
        self._run_task(
            command,
            "general-manager",
            "CONSOLIDATE_FEMININE_DISTRIBUTION_REPORT",
            "feminine",
            {
                "approved": len(approved),
                "blocked_needs_pricing": len(blocked),
                "marketplace_drafts": 0,
                "external_writes": False,
            },
            parent_task_id=manager.task_id,
            depends_on=[sync.task_id],
        )

    def _execute_report(self, command: Command, parsed: ParsedCommand) -> None:
        products = self._products()
        if parsed.handler == "operational_audit":
            self._execute_operational_audit(command, products, parsed.channels)
            return
        if parsed.handler == "refresh_mercado_livre":
            manager = self._run_task(
                command,
                "general-manager",
                "PLAN_MERCADO_LIVRE_READ_ONLY_VALIDATION",
                "mercado-livre",
                {"mode": "READ_ONLY", "products": len(products)},
            )
            distribution = self._run_task(
                command,
                "distribution-supervisor",
                "COORDINATE_MERCADO_LIVRE_READINESS",
                "mercado-livre",
                {"external_writes": "BLOCKED", "publish_enabled": False},
                parent_task_id=manager.task_id,
                depends_on=[manager.task_id],
            )
            if self.connection_service is not None:
                status = self.connection_service.inspect_mercado(products)
            else:
                from .marketplace_clients import connection_status

                status = connection_status()["mercado-livre"]
            marketplace = self._run_task(
                command,
                "mercado-livre-agent",
                "VALIDATE_READ_ONLY_CONNECTION",
                "mercado-livre",
                status,
                parent_task_id=distribution.task_id,
                depends_on=[distribution.task_id],
            )
            conflicts = (
                self.connection_service.read_only_sync(products)
                if self.connection_service is not None
                else []
            )
            sync = self._run_task(
                command,
                "sync-monitor-agent",
                "SYNC_MERCADO_LIVRE_READINESS",
                "mercado-livre",
                {
                    "connection": status.get("readiness", "NOT_READY"),
                    "conflicts": conflicts,
                    "external_write": False,
                },
                parent_task_id=marketplace.task_id,
                depends_on=[marketplace.task_id],
            )
            self._run_task(
                command,
                "general-manager",
                "CONSOLIDATE_MERCADO_LIVRE_READINESS",
                "mercado-livre",
                {
                    "status": status,
                    "read_only_connection": status.get("readiness")
                    == "CONNECTED_READ_ONLY",
                    "ready_for_first_live_publish": False,
                    "external_writes": False,
                },
                parent_task_id=manager.task_id,
                depends_on=[sync.task_id],
            )
            return
        if parsed.handler == "refresh_connections":
            if self.connection_service is not None:
                statuses = self.connection_service.inspect_all(products)
            else:
                from .marketplace_clients import connection_status

                statuses = connection_status()
            manager = self._run_task(
                command,
                "general-manager",
                "PLAN_READ_ONLY_CONNECTION_AUDIT",
                "marketplaces",
                {"mode": "READ_ONLY", "products": len(products)},
            )
            channel_tasks = []
            for channel, agent in self.CHANNEL_AGENTS.items():
                channel_tasks.append(
                    self._run_task(
                        command,
                        agent,
                        "READ_ONLY_CONNECTION_CHECK",
                        channel,
                        statuses[channel],
                        parent_task_id=manager.task_id,
                        depends_on=[manager.task_id],
                    )
                )
            conflicts = (
                self.connection_service.read_only_sync(products)
                if self.connection_service is not None
                else []
            )
            sync = self._run_task(
                command,
                "sync-monitor-agent",
                "READ_ONLY_SYNC_CHECK",
                "marketplaces",
                {
                    "remote_reads": sum(
                        len(item.get("api_reads", [])) for item in statuses.values()
                    ),
                    "reason": "READ_ONLY",
                    "conflicts": conflicts,
                },
                parent_task_id=manager.task_id,
                depends_on=[task.task_id for task in channel_tasks],
            )
            publish_plan = (
                self.connection_service.first_publish_plan(products)
                if self.connection_service is not None
                else None
            )
            self._run_task(
                command,
                "general-manager",
                "CONSOLIDATE_CONNECTION_READINESS",
                "marketplaces",
                {
                    "channels": statuses,
                    "first_real_publish_candidate": publish_plan,
                    "ready_for_first_live_publish": False,
                },
                parent_task_id=manager.task_id,
                depends_on=[sync.task_id],
            )
            return
        handler_agent = (
            "sync-monitor-agent" if parsed.handler == "sync" else "general-manager"
        )
        if parsed.handler == "products_incomplete":
            _, blocked = self._readiness_inventory(products)
            result = {"products": blocked}
        elif parsed.handler == "products_ready":
            eligible, _ = self._readiness_inventory(products)
            result = {"products": [p.get("sku") or p.get("id") for p in eligible]}
        elif parsed.handler == "channels":
            result = {
                "channels": {channel: "NOT_TESTED" for channel in self.CHANNEL_AGENTS}
            }
        elif parsed.handler == "approvals":
            result = {
                "approvals": [
                    asdict(item)
                    for item in self.engine.approvals.requests.values()
                    if item.status is ApprovalStatus.PENDING
                ]
            }
        elif parsed.handler == "errors":
            result = {
                "errors": [
                    {"task_id": task.task_id, "error": task.last_error}
                    for task in self.engine.tasks.tasks.values()
                    if task.last_error
                ]
            }
        elif parsed.handler == "channel_pending":
            result = {
                "channel": parsed.channels[0],
                "pending": "CATEGORY_DISCOVERY_BLOCKED_BY_CREDENTIALS",
            }
        else:
            result = {"sync": "COMPLETED", "conflicts": [], "external_write": False}
        self._run_task(
            command,
            handler_agent,
            parsed.handler.upper(),
            parsed.channels[0] if parsed.channels else "veratus-os",
            result,
        )

    def _execute_operational_audit(
        self,
        command: Command,
        products: list[dict[str, Any]],
        channels: tuple[str, ...],
    ) -> None:
        eligible, blocked = self._readiness_inventory(products)
        identifiers = [str(item.get("sku") or item.get("id")) for item in eligible]
        manager = self._run_task(
            command,
            "general-manager",
            "PLAN_OPERATIONAL_AUDIT",
            "veratus",
            {
                "mode": "READ_ONLY",
                "products_total": len(products),
                "external_writes": "BLOCKED",
            },
        )
        product_supervisor = self._run_task(
            command,
            "product-supervisor",
            "COORDINATE_PRODUCT_AUDIT",
            "product-master",
            {"eligible": len(eligible), "blocked": len(blocked)},
            parent_task_id=manager.task_id,
            depends_on=[manager.task_id],
        )
        product_tasks = [
            self._run_task(
                command,
                agent,
                action,
                "product-master",
                result,
                parent_task_id=product_supervisor.task_id,
                depends_on=[product_supervisor.task_id],
            )
            for agent, action, result in (
                ("product-intake", "AUDIT_ACTIVE_PRODUCTS", {"active": len(products)}),
                (
                    "copy-agent",
                    "AUDIT_PRODUCT_COPY",
                    {
                        "missing_description": [
                            str(item.get("sku") or item.get("id"))
                            for item in products
                            if not self._field_value(item, "description")
                        ]
                    },
                ),
                (
                    "creative-agent",
                    "AUDIT_PRODUCT_ASSETS",
                    {
                        "missing_assets": [
                            item["product"]
                            for item in blocked
                            if "images" in item["missing_fields"]
                        ]
                    },
                ),
                (
                    "pricing-agent",
                    "AUDIT_PRODUCT_PRICING",
                    {
                        "missing_pricing": [
                            item["product"]
                            for item in blocked
                            if any(
                                field in item["missing_fields"]
                                for field in ("sale_price", "cost")
                            )
                        ]
                    },
                ),
                (
                    "quality-agent",
                    "AUDIT_PRODUCT_READINESS",
                    {"eligible": identifiers, "blocked": blocked},
                ),
            )
        ]
        distribution = self._run_task(
            command,
            "distribution-supervisor",
            "COORDINATE_CONNECTION_AUDIT",
            "marketplaces",
            {"channels": list(channels), "external_writes": "BLOCKED"},
            parent_task_id=manager.task_id,
            depends_on=[task.task_id for task in product_tasks],
        )
        if self.connection_service is not None:
            statuses = self.connection_service.inspect_all(products)
        else:
            from .marketplace_clients import connection_status

            statuses = connection_status()
        channel_tasks = [
            self._run_task(
                command,
                self.CHANNEL_AGENTS[channel],
                "READ_ONLY_CONNECTION_CHECK",
                channel,
                statuses[channel],
                parent_task_id=distribution.task_id,
                depends_on=[distribution.task_id],
            )
            for channel in channels
        ]
        sync = self._run_task(
            command,
            "sync-monitor-agent",
            "AUDIT_SYNC_STATE",
            "marketplaces",
            {"checked_channels": list(channels), "external_write": False},
            parent_task_id=distribution.task_id,
            depends_on=[task.task_id for task in channel_tasks],
        )
        next_actions = []
        if blocked:
            next_actions.append("COMPLETE_BLOCKING_PRODUCT_DATA")
        for channel, status in statuses.items():
            if status.get("readiness") != "CONNECTED_READ_ONLY":
                next_actions.append(f"CONNECT_{channel.upper().replace('-', '_')}")
        next_actions.append("KEEP_EXTERNAL_WRITE_GUARD_ENABLED")
        self._run_task(
            command,
            "general-manager",
            "CONSOLIDATE_OPERATIONAL_AUDIT",
            "veratus",
            {
                "products_total": len(products),
                "eligible_products": identifiers,
                "blocked_products": blocked,
                "connections": statuses,
                "next_actions": list(dict.fromkeys(next_actions)),
                "external_writes": False,
            },
            parent_task_id=manager.task_id,
            depends_on=[sync.task_id],
        )

    def _report(self, command: Command) -> dict[str, Any]:
        tasks = [
            task
            for task in self.engine.tasks.tasks.values()
            if task.command_id == command.command_id
        ]
        invoked = list(dict.fromkeys(task.agent for task in tasks))
        return {
            "command_id": command.command_id,
            "status": command.status.value,
            "agents_invoked": invoked,
            "tasks": [asdict(task) for task in tasks],
            "audit_events": [
                asdict(event)
                for event in self.engine.tasks.audit
                if event.command_id == command.command_id
            ],
            "external_writes": "BLOCKED",
        }
