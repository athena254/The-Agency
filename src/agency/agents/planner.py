"""Task decomposition and subtask assignment.

:class:`AgentPlanner` turns a coarse task into a :class:`TaskGraph` of
:class:`Subtask` nodes with explicit dependencies, then matches each subtask
to a capable specialist via the :class:`~agency.agents.registry.AgentRegistry`.

Decomposition is pluggable: pass ``strategy`` to use an LLM-backed splitter,
otherwise a deterministic heuristic splits the task description into steps.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from enum import Enum
from threading import RLock
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agency.agents.registry import AgentRegistry, AgentStatus

logger = structlog.get_logger(__name__)

DecomposeStrategy = Callable[[str], list["SubtaskSpec"]]


class SubtaskStatus(str, Enum):
    """Lifecycle state of a single subtask."""

    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


class PlanStatus(str, Enum):
    """Aggregate state of a plan, derived from its subtasks."""

    DRAFT = "draft"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


class SubtaskSpec(BaseModel):
    """Proposed subtask emitted by a decomposition strategy."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    title: str = Field(min_length=1)
    description: str = Field(default="")
    required_capabilities: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _normalize_caps(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        return [str(c).strip().lower() for c in value if str(c).strip()]


class Subtask(BaseModel):
    """A single unit of delegated work inside a plan."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    subtask_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = Field(min_length=1)
    description: str = Field(default="")
    required_capabilities: list[str] = Field(default_factory=list)
    assigned_agent_id: str | None = Field(default=None)
    status: SubtaskStatus = Field(default=SubtaskStatus.PENDING)
    depends_on: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None)


class TaskGraph(BaseModel):
    """A decomposed task: subtasks plus the plan they belong to."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    task: str = Field(min_length=1, description="Original task description.")
    subtasks: list[Subtask] = Field(default_factory=list)

    def get(self, subtask_id: str) -> Subtask | None:
        for subtask in self.subtasks:
            if subtask.subtask_id == subtask_id:
                return subtask
        return None

    def ready_subtasks(self) -> list[Subtask]:
        """Subtasks whose dependencies have all completed."""
        done = {s.subtask_id for s in self.subtasks if s.status is SubtaskStatus.COMPLETED}
        return [
            s
            for s in self.subtasks
            if s.status in (SubtaskStatus.PENDING, SubtaskStatus.ASSIGNED)
            and all(dep in done for dep in s.depends_on)
        ]


class PlanSummary(BaseModel):
    """Aggregate plan state returned by :meth:`AgentPlanner.get_plan_status`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    status: PlanStatus
    total: int
    completed: int
    failed: int
    assigned: int
    pending: int


def _default_strategy(task: str) -> list[SubtaskSpec]:
    """Deterministic fallback: split the task text into step-like chunks."""
    # "Step N: ... Step M: ..." style first (also matches "step 1." / "step 2)").
    chunks = re.split(r"(?i)\bstep\s+\d+\s*[:.)]?\s*", task)
    parts = [p.strip(" ;\n\t") for p in chunks if p.strip(" ;\n\t")]
    if len(parts) <= 1:
        # Generic numbered ("1. ... 2. ...", "1: ...") or line/semicolon lists.
        parts = [p.strip() for p in re.split(r"[\n;]+|\s*\d+\s*[.):]\s+", task) if p.strip()]
    if len(parts) <= 1:
        return [SubtaskSpec(title="Execute task", description=task)]
    return [
        SubtaskSpec(title=f"Step {i + 1}: {part[:60]}", description=part)
        for i, part in enumerate(parts[:20])
    ]


class AgentPlanner:
    """Decomposes tasks and assigns subtasks to specialist agents.

    Parameters
    ----------
    registry:
        Runtime registry used to find capable agents.
    strategy:
        Optional decomposition function; defaults to a deterministic splitter.
    auto_assign:
        When ``True`` (default) each subtask is immediately matched to the
        best available agent holding the first required capability.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        strategy: DecomposeStrategy | None = None,
        auto_assign: bool = True,
    ) -> None:
        self._registry = registry
        self._strategy = strategy or _default_strategy
        self._auto_assign = auto_assign
        self._plans: dict[str, TaskGraph] = {}
        self._lock = RLock()
        self._log = structlog.get_logger(__name__)

    # ------------------------------------------------------------------ #
    # Planning
    # ------------------------------------------------------------------ #

    def plan(self, task: str | dict[str, Any]) -> TaskGraph:
        """Decompose ``task`` into subtasks and (optionally) auto-assign them.

        Raises
        ------
        ValueError
            If the task description is empty.
        """
        description = task if isinstance(task, str) else str(task.get("description", task))
        if not description.strip():
            raise ValueError("task must not be empty.")
        specs = self._strategy(description.strip())
        if not specs:
            specs = [SubtaskSpec(title="Execute task", description=description.strip())]

        graph = TaskGraph(
            task=description.strip(),
            subtasks=[
                Subtask(
                    title=spec.title,
                    description=spec.description,
                    required_capabilities=list(spec.required_capabilities),
                    depends_on=list(spec.depends_on),
                    input=dict(spec.input),
                )
                for spec in specs
            ],
        )
        # Chain dependencies linearly when the strategy left them unset and
        # produced more than one step, so execution order is well-defined.
        if len(graph.subtasks) > 1 and all(not s.depends_on for s in graph.subtasks):
            for prev, current in zip(graph.subtasks, graph.subtasks[1:]):
                current.depends_on.append(prev.subtask_id)

        with self._lock:
            self._plans[graph.plan_id] = graph

        self._log.info("planner.plan", plan_id=graph.plan_id, subtasks=len(graph.subtasks))
        if self._auto_assign:
            for subtask in graph.subtasks:
                self._try_auto_assign(graph, subtask)
        return graph

    def assign_subtask(
        self, agent_id: str, subtask: Subtask | str, *, plan_id: str | None = None
    ) -> Subtask:
        """Assign ``subtask`` to ``agent_id``, validating capability fit.

        Parameters
        ----------
        agent_id:
            Target runtime agent (must be registered and not revoked).
        subtask:
            A :class:`Subtask` instance or its id (requires ``plan_id``).
        plan_id:
            Plan to resolve a bare subtask id against.

        Raises
        ------
        KeyError
            If the agent or subtask is unknown.
        ValueError
            If the agent lacks a required capability or is revoked.
        """
        target = self._resolve_subtask(subtask, plan_id)
        record = self._registry.get_required(agent_id)
        if record.agent.revoked:
            raise ValueError(f"agent {agent_id!r} is revoked.")
        missing = [
            cap for cap in target.required_capabilities if not record.agent.has_capability(cap)
        ]
        if missing:
            raise ValueError(f"agent {agent_id!r} lacks capabilities: {sorted(missing)}")
        target.assigned_agent_id = agent_id
        if target.status is SubtaskStatus.PENDING:
            target.status = SubtaskStatus.ASSIGNED
        self._registry.update_agent_status(
            agent_id, AgentStatus.BUSY, current_task_id=target.subtask_id
        )
        self._log.info(
            "planner.assign", plan_id=plan_id, subtask_id=target.subtask_id, agent_id=agent_id
        )
        return target

    def get_plan_status(self, plan_id: str) -> PlanSummary:
        """Return the aggregate status of a plan.

        Raises
        ------
        KeyError
            If the plan is unknown.
        """
        with self._lock:
            graph = self._plans.get(plan_id)
            if graph is None:
                raise KeyError(f"unknown plan {plan_id!r}")
            counts: dict[SubtaskStatus, int] = {s: 0 for s in SubtaskStatus}
            for subtask in graph.subtasks:
                counts[subtask.status] += 1
        total = len(graph.subtasks)
        completed = counts[SubtaskStatus.COMPLETED]
        failed = counts[SubtaskStatus.FAILED]
        if failed and completed + failed == total:
            status = PlanStatus.FAILED if failed > completed else PlanStatus.COMPLETED
        elif completed == total and total > 0:
            status = PlanStatus.COMPLETED
        elif completed or failed or counts[SubtaskStatus.RUNNING]:
            status = PlanStatus.IN_PROGRESS
        elif counts[SubtaskStatus.ASSIGNED]:
            status = PlanStatus.READY
        else:
            status = PlanStatus.DRAFT
        return PlanSummary(
            plan_id=plan_id,
            status=status,
            total=total,
            completed=completed,
            failed=failed,
            assigned=counts[SubtaskStatus.ASSIGNED] + counts[SubtaskStatus.RUNNING],
            pending=counts[SubtaskStatus.PENDING],
        )

    def get_plan(self, plan_id: str) -> TaskGraph | None:
        """Fetch a stored plan, or ``None`` if unknown."""
        with self._lock:
            return self._plans.get(plan_id)

    def mark_subtask(
        self, subtask_id: str, status: SubtaskStatus, *, plan_id: str | None = None
    ) -> Subtask:
        """Update a subtask's lifecycle state (used by executor callbacks)."""
        target = self._resolve_subtask(subtask_id, plan_id)
        target.status = status
        return target

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _try_auto_assign(self, graph: TaskGraph, subtask: Subtask) -> None:
        for capability in subtask.required_capabilities:
            candidates = self._registry.find_agents_by_capability(capability)
            if candidates:
                try:
                    self.assign_subtask(candidates[0].agent_id, subtask, plan_id=graph.plan_id)
                except ValueError:
                    continue
                return
        # No capability constraint (or no match): pick any available agent.
        if not subtask.required_capabilities:
            candidates = [r for r in self._registry.list_agents() if r.available]
            if candidates:
                self.assign_subtask(candidates[0].agent_id, subtask, plan_id=graph.plan_id)

    def _resolve_subtask(self, subtask: Subtask | str, plan_id: str | None) -> Subtask:
        if isinstance(subtask, Subtask):
            return subtask
        if plan_id is None:
            raise ValueError("plan_id is required when addressing a subtask by id.")
        with self._lock:
            graph = self._plans.get(plan_id)
            if graph is None:
                raise KeyError(f"unknown plan {plan_id!r}")
            target = graph.get(subtask)
        if target is None:
            raise KeyError(f"unknown subtask {subtask!r} in plan {plan_id!r}")
        return target


__all__ = [
    "AgentPlanner",
    "PlanStatus",
    "PlanSummary",
    "Subtask",
    "SubtaskSpec",
    "SubtaskStatus",
    "TaskGraph",
]
