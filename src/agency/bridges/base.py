"""Bridge abstract base class and shared result types.

Every external AI integration (Claude Code CLI, Codex API, OpenClaw HTTP,
Hermes ACP) implements :class:`Bridge` so the
:class:`~agency.bridges.coordinator.ExternalCoordinator` can route tasks,
stream partial output, and probe health uniformly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum
from time import perf_counter
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field


class BridgeStatus(str, Enum):
    """Terminal status of a single bridged execution."""

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class BridgeUsage:
    """Token / cost accounting reported by a bridge execution."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def __add__(self, other: BridgeUsage) -> BridgeUsage:
        return BridgeUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            estimated_cost_usd=self.estimated_cost_usd + other.estimated_cost_usd,
        )


@dataclass(frozen=True, slots=True)
class BridgeResult:
    """Outcome of one :meth:`Bridge.execute` call.

    Attributes
    ----------
    output:
        Primary textual output of the external agent.
    artifacts:
        Structured side-effects (files written, tool calls, urls, ...).
    usage:
        Token / cost accounting (best effort; zero when unknown).
    status:
        Terminal :class:`BridgeStatus`.
    duration_s:
        Wall-clock seconds spent inside ``execute``.
    error:
        Human-readable failure detail; ``None`` on success.
    """

    output: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    usage: BridgeUsage = field(default_factory=BridgeUsage)
    status: BridgeStatus = BridgeStatus.SUCCESS
    duration_s: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the execution succeeded."""
        return self.status is BridgeStatus.SUCCESS

    @classmethod
    def failure(
        cls,
        error: str,
        *,
        status: BridgeStatus = BridgeStatus.FAILED,
        duration_s: float = 0.0,
        output: str = "",
        artifacts: dict[str, Any] | None = None,
    ) -> BridgeResult:
        """Build a failed result with timing attached."""
        return cls(
            output=output,
            artifacts=artifacts or {},
            status=status,
            duration_s=duration_s,
            error=error,
        )


class BridgeConfig(BaseModel):
    """Shared, validated configuration for all bridges."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="Unique bridge name used for routing.")
    enabled: bool = Field(default=True)
    timeout_s: float = Field(default=300.0, gt=0, description="Per-execution timeout.")
    max_retries: int = Field(default=3, ge=0, description="Retries on transient failures.")
    retry_backoff_s: float = Field(default=1.0, ge=0, description="Base backoff between retries.")
    extra: dict[str, Any] = Field(default_factory=dict, description="Bridge-specific options.")

    def backoff_for(self, attempt: int) -> float:
        """Exponential backoff (no jitter) for retry ``attempt`` (0-indexed)."""
        return self.retry_backoff_s * (2.0**attempt)


class Bridge(ABC):
    """Abstract base class for all external AI bridges."""

    def __init__(self, config: BridgeConfig) -> None:
        self._config = config
        self._log = structlog.get_logger(f"{__name__}.{config.name}")

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def name(self) -> str:
        """Unique bridge name used for routing."""
        return self._config.name

    @property
    def config(self) -> BridgeConfig:
        """Validated bridge configuration."""
        return self._config

    # ------------------------------------------------------------------ #
    # Abstract interface
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def execute(self, task: str, context: dict[str, Any] | None = None) -> BridgeResult:
        """Run ``task`` to completion on the external system.

        Parameters
        ----------
        task:
            Natural-language instruction / prompt for the external agent.
        context:
            Optional structured context (files, repo path, session id, ...).

        Returns
        -------
        BridgeResult
            Final output, artifacts, usage and status. Must never raise on
            *task-level* failure — encode it as ``status=FAILED`` instead.
            Transport-level catastrophes (misconfiguration) may raise.
        """
        raise NotImplementedError

    @abstractmethod
    async def stream(
        self, task: str, context: dict[str, Any] | None = None
    ) -> AsyncGenerator[str, None]:
        """Yield incremental output chunks for ``task`` as they arrive.

        Each yielded item is a decoded text delta (already stripped of
        protocol framing such as SSE envelopes). The final concatenated
        chunks should approximate what :meth:`execute` returns in
        ``BridgeResult.output``.
        """
        raise NotImplementedError
        yield ""  # pragma: no cover — keeps the function an async generator.

    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Return a JSON-serialisable capability descriptor.

        Expected keys (all optional): ``streaming`` (bool), ``tools`` /
        ``modalities`` (list), ``max_context_tokens`` (int), ``notes`` (str).
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """Lightweight liveness probe; ``True`` when the bridge can serve work."""
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Helpers for subclasses
    # ------------------------------------------------------------------ #

    def _timed_failure(self, error: str, start: float, **kwargs: Any) -> BridgeResult:
        return BridgeResult.failure(error, duration_s=perf_counter() - start, **kwargs)

    def _build_prompt(self, task: str, context: dict[str, Any] | None) -> str:
        """Combine ``task`` with an optional ``system``/``context`` preamble."""
        if not context:
            return task
        system = context.get("system")
        preamble = f"System: {system}\n\n" if system else ""
        extra_keys = {k: v for k, v in context.items() if k != "system"}
        if extra_keys:
            preamble += f"Context: {extra_keys}\n\n"
        return f"{preamble}Task: {task}"
