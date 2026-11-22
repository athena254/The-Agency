"""Task decomposition and subtask assignment.

:class:`AgentPlanner` turns a coarse task into a :class:`TaskGraph` of
:class:`Subtask` nodes with explicit dependencies, then matches each subtask
to a capable specialist via the :class:`~agency.agents.registry.AgentRegistry`.

Decomposition is pluggable: pass ``strategy`` to use your own splitter, else
the default resolver picks an LLM-backed splitter when a live provider is
configured (:func:`make_llm_strategy`) and the deterministic regex splitter
(:func:`_default_strategy`) for echo/offline — where it also remains the
guaranteed fallback for every LLM failure.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
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


# ---------------------------------------------------------------------- #
# LLM-backed decomposition (gaps #21f) — live providers only. The regex
# strategy above stays the offline floor and the guaranteed fallback.
# ---------------------------------------------------------------------- #

LLM_DECOMPOSE_MAX_SUBTASKS = 6
_LLM_TITLE_MAX = 200
_LLM_DESCRIPTION_MAX = 2000
_LLM_TASK_PROMPT_MAX = 4000
# Bounded completion budget for the strategy-owned adapter: six short
# subtasks fit inside it, and a truncated (budget-exhausted) reply is invalid
# JSON and falls back. See _resolve_default_strategy.
_LLM_PLAN_MAX_TOKENS = 1024
_LLM_PLAN_TEMPERATURE = 0.2

# Prompt hygiene: only the task text goes out — no tool definitions, no
# system/memory content — and the reply schema is pinned to exactly three
# keys per subtask.
_DECOMPOSE_PROMPT = """Decompose the task below into at most {max_subtasks} sequential subtasks that together complete it.
Reply with ONLY a JSON array - no prose, no code fences. Each element must be an object with exactly these keys:
- "title": short imperative summary (max 200 chars)
- "description": one or two sentences of concrete instructions
- "required_capabilities": array of short lowercase keywords (e.g. "research", "write", "test", "code", "review") describing the specialist role

Rules: 1 to {max_subtasks} items, keep the original task's scope, no extra keys, order items by execution order.

Example reply shape: [{{"title": "Gather data", "description": "Collect the raw inputs.", "required_capabilities": ["research"]}}]

Task: {task}"""


def _run_awaitable_blocking(awaitable: Coroutine[Any, Any, str]) -> str:
    """Drive an async adapter call to completion from sync strategy code.

    Outside an event loop this is plain ``asyncio.run``; inside a running loop
    (the orchestrator calls the sync ``plan`` seam from async methods) the
    awaitable runs on a short-lived helper thread with its own loop. Either
    way the calling thread blocks for at most the adapter timeout.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, awaitable).result()


def _extract_json_array(text: str) -> Any:
    """Parse the JSON array out of a reply; a single code fence is tolerated,
    prose around the array is not."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline == -1:
            return None
        stripped = stripped[first_newline + 1 :]
        end = stripped.rfind("```")
        if end != -1:
            stripped = stripped[:end]
        stripped = stripped.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _parse_llm_plan(text: str, *, max_subtasks: int) -> list[SubtaskSpec] | None:
    """Strictly parse and validate an LLM decomposition reply.

    Returns the mapped :class:`SubtaskSpec` list, or ``None`` on any schema,
    type, bound or count violation (count cap included) — callers fall back
    to the regex strategy instead of salvaging a partial reply.
    """
    payload = _extract_json_array(text)
    if not isinstance(payload, list) or not 1 <= len(payload) <= max_subtasks:
        return None
    specs: list[SubtaskSpec] = []
    for item in payload:
        if not isinstance(item, dict):
            return None
        if set(item) - {"title", "description", "required_capabilities"}:
            return None
        title = item.get("title")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > _LLM_TITLE_MAX:
            return None
        description = item.get("description", "")
        if not isinstance(description, str) or len(description.strip()) > _LLM_DESCRIPTION_MAX:
            return None
        caps = item.get("required_capabilities", [])
        if isinstance(caps, str):
            caps = [caps]
        if not isinstance(caps, list) or not all(isinstance(cap, str) for cap in caps):
            return None
        specs.append(
            SubtaskSpec(
                title=title.strip(), description=description.strip(), required_capabilities=caps
            )
        )
    return specs


def make_llm_strategy(
    adapter: Any,
    *,
    max_subtasks: int = LLM_DECOMPOSE_MAX_SUBTASKS,
    fallback: DecomposeStrategy | None = None,
) -> DecomposeStrategy:
    """Return a sync decomposition strategy backed by ``adapter.generate``.

    The prompt carries only the task text and output size is bounded by the
    adapter's ``max_tokens``. Any failure — malformed or over-cap reply,
    timeout, provider error, empty plan — falls back to ``fallback``
    (default the deterministic regex strategy) with an honest log line that
    never includes reply content.
    """
    floor = fallback or _default_strategy

    def strategy(task: str) -> list[SubtaskSpec]:
        prompt = _DECOMPOSE_PROMPT.format(
            max_subtasks=max_subtasks, task=task.strip()[:_LLM_TASK_PROMPT_MAX]
        )
        try:
            raw = _run_awaitable_blocking(adapter.generate(prompt))
        except Exception as exc:  # noqa: BLE001 — any failure must fall back to the regex floor
            # Provider failure must degrade to the regex floor, never
            # propagate; log the class only (replies can echo task content).
            logger.warning(
                "planner.llm_decompose_failed",
                reason="provider_error",
                error_class=type(exc).__name__,
            )
            return floor(task)
        specs = _parse_llm_plan(raw, max_subtasks=max_subtasks)
        if specs is None:
            logger.warning(
                "planner.llm_decompose_failed",
                reason="invalid_llm_output",
                reply_chars=len(raw),
            )
            return floor(task)
        logger.info("planner.llm_decomposed", subtasks=len(specs), cap=max_subtasks)
        return specs

    return strategy


def _resolve_default_strategy() -> DecomposeStrategy:
    """Pick the default decomposition strategy from the ambient LLM config.

    Echo/offline (the hermetic default) keeps the deterministic regex
    strategy; any live provider gets the LLM-backed strategy with the regex
    strategy as its guaranteed fallback. Selection only reads configuration
    and never performs a network call; the strategy-owned adapter is built
    with a bounded completion budget.
    """
    from agency.llm.adapter import LLMAdapter
    from agency.llm.config import ProviderKind, load_config

    config = load_config(load_dotenv=False)
    if config.provider is ProviderKind.ECHO:
        return _default_strategy
    bounded = config.model_copy(
        update={"max_tokens": _LLM_PLAN_MAX_TOKENS, "temperature": _LLM_PLAN_TEMPERATURE}
    )
    return make_llm_strategy(LLMAdapter(config=bounded))


class AgentPlanner:
    """Decomposes tasks and assigns subtasks to specialist agents.

    Parameters
    ----------
    registry:
        Runtime registry used to find capable agents.
    strategy:
        Optional decomposition function. When omitted, the default resolver
        picks the LLM-backed splitter for a live provider and the
        deterministic regex splitter for echo/offline (see
        :func:`_resolve_default_strategy`).
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
        self._strategy = strategy if strategy is not None else _resolve_default_strategy()
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
    "LLM_DECOMPOSE_MAX_SUBTASKS",
    "AgentPlanner",
    "PlanStatus",
    "PlanSummary",
    "Subtask",
    "SubtaskSpec",
    "SubtaskStatus",
    "TaskGraph",
    "make_llm_strategy",
]
