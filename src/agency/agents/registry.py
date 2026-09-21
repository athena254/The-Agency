"""Agent runtime registry — manages agent lifecycle for execution.

This registry is the *runtime* counterpart to
:class:`agency.kernel.registry.AgentRegistry` (which owns identity). It tracks
which agents are available to do work right now: registration, liveness
(``status`` / heartbeats), domain filtering, and capability matching used by
the :class:`~agency.agents.planner.AgentPlanner` to assign subtasks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from threading import RLock
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.kernel.identity import Agent

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgentStatus(str, Enum):
    """Runtime liveness of an agent worker."""

    IDLE = "idle"
    BUSY = "busy"
    DRAINING = "draining"
    OFFLINE = "offline"
    ERROR = "error"

    def __str__(self) -> str:
        return self.value


class AgentRecord(BaseModel):
    """A registered agent plus its mutable runtime state."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    agent: Agent
    status: AgentStatus = Field(default=AgentStatus.IDLE)
    current_task_id: str | None = Field(default=None)
    last_heartbeat: datetime = Field(default_factory=_utcnow)
    consecutive_failures: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def agent_id(self) -> str:
        return self.agent.id

    @property
    def available(self) -> bool:
        """Whether the agent can accept a new subtask right now."""
        return self.status is AgentStatus.IDLE and not self.agent.revoked

    def touch(self) -> AgentRecord:
        self.last_heartbeat = _utcnow()
        return self


class AgentRegistry:
    """Thread-safe, in-memory registry of runtime agent workers.

    Methods are synchronous (fast dict operations) and guarded by an RLock so
    the registry is callable from both sync and async contexts.
    """

    def __init__(self) -> None:
        self._records: dict[str, AgentRecord] = {}
        self._lock = RLock()
        self._log = structlog.get_logger(__name__)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def register(self, agent: Agent, *, metadata: dict[str, Any] | None = None) -> AgentRecord:
        """Register an agent identity as an available runtime worker.

        Raises
        ------
        ValueError
            If ``agent.id`` is already registered.
        """
        with self._lock:
            if agent.id in self._records:
                raise ValueError(f"agent {agent.id!r} is already registered")
            record = AgentRecord(agent=agent, metadata=dict(metadata or {}))
            self._records[agent.id] = record
        self._log.info("agents.register", agent_id=agent.id, domain=agent.domain)
        return record

    def deregister(self, agent_id: str) -> bool:
        """Remove an agent from the runtime pool.

        Returns ``True`` if an entry was removed, ``False`` if unknown.
        A ``BUSY`` agent is force-released; callers should cancel its task
        via :class:`~agency.agents.executor.AgentExecutor` first.
        """
        with self._lock:
            record = self._records.pop(agent_id, None)
        if record is None:
            return False
        self._log.info("agents.deregister", agent_id=agent_id)
        return True

    def get_agent(self, agent_id: str) -> AgentRecord | None:
        """Fetch a runtime record by id, or ``None`` if unknown."""
        with self._lock:
            return self._records.get(agent_id)

    def get_required(self, agent_id: str) -> AgentRecord:
        """Fetch a runtime record by id; raise :class:`KeyError` if unknown."""
        record = self.get_agent(agent_id)
        if record is None:
            raise KeyError(f"unknown agent {agent_id!r}")
        return record

    def list_agents(self, domain: str | None = None) -> list[AgentRecord]:
        """List registered agents, optionally restricted to one domain.

        Results are ordered by agent id for determinism.
        """
        with self._lock:
            records = list(self._records.values())
        if domain is not None:
            records = [r for r in records if r.agent.domain == domain]
        return sorted(records, key=lambda r: r.agent_id)

    # ------------------------------------------------------------------ #
    # Capability matching
    # ------------------------------------------------------------------ #

    def find_agents_by_capability(
        self,
        capability: str,
        *,
        available_only: bool = True,
        domain: str | None = None,
    ) -> list[AgentRecord]:
        """Return agents claiming ``capability``, preferring healthy workers.

        Parameters
        ----------
        capability:
            Capability name (case-insensitive, e.g. ``"code-review"``).
        available_only:
            When ``True`` (default) only ``IDLE`` non-revoked agents match.
        domain:
            Optional domain restriction applied after capability matching.

        Ordering: available first, then fewest consecutive failures, then id.
        """
        needle = capability.strip().lower()
        if not needle:
            raise ValueError("capability must not be empty.")
        with self._lock:
            records = list(self._records.values())
        matched = [r for r in records if r.agent.has_capability(needle)]
        if domain is not None:
            matched = [r for r in matched if r.agent.domain == domain]
        if available_only:
            matched = [r for r in matched if r.available]
        return sorted(matched, key=lambda r: (not r.available, r.consecutive_failures, r.agent_id))

    # ------------------------------------------------------------------ #
    # Status / health
    # ------------------------------------------------------------------ #

    def update_agent_status(
        self,
        agent_id: str,
        status: AgentStatus,
        *,
        current_task_id: str | None = None,
    ) -> AgentRecord:
        """Set an agent's runtime status; refreshes its heartbeat.

        ``current_task_id`` is set explicitly so ``BUSY`` always carries the
        task it is working on; transitioning away from ``BUSY`` clears it
        unless a new value is supplied.
        """
        with self._lock:
            record = self.get_required(agent_id)
            record.status = status
            if status is AgentStatus.BUSY or current_task_id is not None:
                record.current_task_id = current_task_id
            else:
                record.current_task_id = None
            if status is AgentStatus.IDLE:
                record.consecutive_failures = 0
            record.touch()
        self._log.info("agents.status", agent_id=agent_id, status=status.value)
        return record

    def heartbeat(self, agent_id: str) -> AgentRecord:
        """Refresh an agent's liveness timestamp."""
        with self._lock:
            return self.get_required(agent_id).touch()

    def report_failure(self, agent_id: str) -> AgentRecord:
        """Record a task failure; trips the agent to ``ERROR`` after 3 in a row."""
        with self._lock:
            record = self.get_required(agent_id)
            record.consecutive_failures += 1
            if record.consecutive_failures >= 3:
                record.status = AgentStatus.ERROR
                record.current_task_id = None
            record.touch()
        self._log.warning(
            "agents.failure",
            agent_id=agent_id,
            consecutive_failures=record.consecutive_failures,
            status=record.status.value,
        )
        return record

    # ------------------------------------------------------------------ #
    # Dunder helpers
    # ------------------------------------------------------------------ #

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def __contains__(self, agent_id: str) -> bool:
        with self._lock:
            return agent_id in self._records

    def __repr__(self) -> str:
        return f"AgentRegistry(agents={len(self)})"


__all__ = ["AgentRecord", "AgentRegistry", "AgentStatus"]
