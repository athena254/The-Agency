"""Single-task execution with retry, timeout, and error recovery.

:class:`AgentExecutor` wraps an LLM (or any async/sync callable) with
production hardening:

* per-attempt timeouts via :func:`asyncio.wait_for`,
* bounded retries with exponential backoff on transient failures,
* cancellation through :meth:`AgentExecutor.cancel`,
* observable per-task status via :meth:`AgentExecutor.get_status`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from threading import RLock
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.llm.adapter import LLMAdapter

logger = structlog.get_logger(__name__)

# Callable invoked as ``llm(prompt, context)``; may be sync or async and may
# return a plain string or a structured payload.
LLMCallable = Callable[[str, dict[str, Any]], Any | Awaitable[Any]]

_NON_RETRYABLE = (ValueError, TypeError, KeyError, AttributeError)


class ExecutionStatus(str, Enum):
    """Lifecycle state of one executed task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"

    def __str__(self) -> str:
        return self.value


class ExecutionContext(BaseModel):
    """Knobs carried alongside a single execution."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    timeout_s: float = Field(default=60.0, gt=0, description="Per-attempt timeout in seconds.")
    max_retries: int = Field(default=2, ge=0, description="Retries after the initial attempt.")
    backoff_base_s: float = Field(default=0.5, ge=0, description="Base backoff between retries.")
    model: str = Field(default="", description="Preferred model name, if any.")
    agent_id: str | None = Field(default=None, description="Agent executing this task.")
    subtask_id: str | None = Field(
        default=None, description="Subtask identifier, if part of a plan."
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    """Outcome of :meth:`AgentExecutor.execute`."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    task_id: str
    status: ExecutionStatus
    output: Any = None
    error: str | None = None
    attempts: int = Field(default=1, ge=1)
    duration_s: float = Field(default=0.0, ge=0.0)
    model: str = Field(default="")


async def _maybe_await(value: Any | Awaitable[Any]) -> Any:
    if asyncio.iscoroutine(value) or isinstance(value, Awaitable):
        return await value
    return value


class AgentExecutor:
    """Runs a single agent task against an LLM backend with hardening.

    Parameters
    ----------
    llm:
        Either a callable ``(prompt, context_dict) -> output`` (sync or
        async) or an :class:`~agency.llm.adapter.LLMAdapter` instance (in
        which case ``llm.generate`` is used). When omitted a default
        ``LLMAdapter`` is created — it serves real LLM calls when API keys
        are configured and falls back to deterministic echo mode otherwise.
    default_timeout_s / default_max_retries / default_backoff_base_s:
        Defaults applied when :class:`ExecutionContext` leaves them unset.
    """

    def __init__(
        self,
        llm: LLMCallable | LLMAdapter | None = None,
        *,
        default_timeout_s: float = 60.0,
        default_max_retries: int = 2,
        default_backoff_base_s: float = 0.5,
    ) -> None:
        self._adapter: LLMAdapter | None
        if llm is None:
            self._adapter = LLMAdapter()
            self._llm: LLMCallable = self._adapter.generate
        elif isinstance(llm, LLMAdapter):
            self._adapter = llm
            self._llm = llm.generate
        elif hasattr(llm, "generate"):
            # Duck-typed LLM adapter (e.g. test doubles exposing generate()).
            self._adapter = None
            self._llm = llm.generate
        else:
            self._adapter = None
            self._llm = llm
        self._default_timeout_s = default_timeout_s
        self._default_max_retries = default_max_retries
        self._default_backoff_base_s = default_backoff_base_s
        self._statuses: dict[str, ExecutionStatus] = {}
        self._running: dict[str, asyncio.Task[Any]] = {}
        self._cancelled: set[str] = set()
        self._lock = RLock()
        self._log = structlog.get_logger(__name__)

    @property
    def adapter(self) -> LLMAdapter | None:
        """The backing :class:`LLMAdapter`, if this executor uses one."""
        return self._adapter

    @property
    def llm(self) -> LLMCallable:
        """The underlying LLM callable invoked as ``llm(prompt, context)``."""
        return self._llm

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    async def execute(
        self,
        task: str | dict[str, Any],
        context: ExecutionContext | dict[str, Any] | None = None,
        *,
        task_id: str | None = None,
    ) -> ExecutionResult:
        """Run ``task`` to completion (or terminal failure).

        Retries transient errors up to ``max_retries`` with exponential
        backoff; non-retryable programmer errors (``ValueError``,
        ``TypeError``, ``KeyError``, ``AttributeError``) fail fast.
        """
        ctx = self._coerce_context(context)
        prompt, payload = self._coerce_task(task)
        tid = task_id or payload.get("task_id", str(uuid4()))
        timeout = ctx.timeout_s or self._default_timeout_s
        max_retries = ctx.max_retries if context is not None else self._default_max_retries
        backoff = ctx.backoff_base_s or self._default_backoff_base_s

        self._set_status(tid, ExecutionStatus.RUNNING)
        with self._lock:
            current = asyncio.current_task()
            if current is not None:
                self._running[tid] = current

        started = time.perf_counter()
        attempts = 0
        last_error: str | None = None
        try:
            for attempt in range(max_retries + 1):
                attempts = attempt + 1
                with self._lock:
                    if tid in self._cancelled:
                        self._set_status(tid, ExecutionStatus.CANCELLED)
                        return self._result(
                            tid,
                            ExecutionStatus.CANCELLED,
                            None,
                            "cancelled",
                            attempts,
                            started,
                            ctx,
                        )
                try:
                    output = await asyncio.wait_for(
                        _maybe_await(self._llm(prompt, {"task_id": tid, **ctx.metadata})),
                        timeout=timeout,
                    )
                    self._set_status(tid, ExecutionStatus.COMPLETED)
                    self._log.info("executor.completed", task_id=tid, attempts=attempts)
                    return self._result(
                        tid, ExecutionStatus.COMPLETED, output, None, attempts, started, ctx
                    )
                except asyncio.CancelledError:
                    self._set_status(tid, ExecutionStatus.CANCELLED)
                    self._log.warning("executor.cancelled", task_id=tid, attempts=attempts)
                    raise
                except TimeoutError as exc:
                    last_error = f"timed out after {timeout}s: {exc}"
                    self._log.warning("executor.timeout", task_id=tid, attempt=attempts)
                    if attempt >= max_retries:
                        self._set_status(tid, ExecutionStatus.TIMEOUT)
                        return self._result(
                            tid, ExecutionStatus.TIMEOUT, None, last_error, attempts, started, ctx
                        )
                except _NON_RETRYABLE as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    self._log.exception("executor.non_retryable", task_id=tid)
                    self._set_status(tid, ExecutionStatus.FAILED)
                    return self._result(
                        tid, ExecutionStatus.FAILED, None, last_error, attempts, started, ctx
                    )
                except Exception as exc:  # noqa: BLE001 — transient by definition here
                    last_error = f"{type(exc).__name__}: {exc}"
                    self._log.warning(
                        "executor.retry", task_id=tid, attempt=attempts, error=last_error
                    )
                    if attempt >= max_retries:
                        self._set_status(tid, ExecutionStatus.FAILED)
                        return self._result(
                            tid, ExecutionStatus.FAILED, None, last_error, attempts, started, ctx
                        )
                await asyncio.sleep(backoff * (2**attempt))
            self._set_status(tid, ExecutionStatus.FAILED)
            return self._result(
                tid, ExecutionStatus.FAILED, None, last_error, attempts, started, ctx
            )
        finally:
            with self._lock:
                self._running.pop(tid, None)

    def cancel(self, task_id: str) -> bool:
        """Request cancellation of a running task.

        Returns ``True`` if the task was running (or queued) and is now
        marked cancelled; ``False`` if the id is unknown or terminal.
        """
        with self._lock:
            status = self._statuses.get(task_id)
            if status not in (ExecutionStatus.PENDING, ExecutionStatus.RUNNING):
                return False
            self._cancelled.add(task_id)
            self._statuses[task_id] = ExecutionStatus.CANCELLED
            running = self._running.get(task_id)
        if running is not None and not running.done():
            running.cancel()
        self._log.warning("executor.cancel", task_id=task_id)
        return True

    def get_status(self, task_id: str) -> ExecutionStatus | None:
        """Return the last known status, or ``None`` if never seen."""
        with self._lock:
            return self._statuses.get(task_id)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _set_status(self, task_id: str, status: ExecutionStatus) -> None:
        with self._lock:
            self._statuses[task_id] = status

    @staticmethod
    def _result(
        task_id: str,
        status: ExecutionStatus,
        output: Any,
        error: str | None,
        attempts: int,
        started: float,
        ctx: ExecutionContext,
    ) -> ExecutionResult:
        return ExecutionResult(
            task_id=task_id,
            status=status,
            output=output,
            error=error,
            attempts=attempts,
            duration_s=max(0.0, time.perf_counter() - started),
            model=ctx.model,
        )

    @staticmethod
    def _coerce_task(task: str | dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if isinstance(task, str):
            if not task.strip():
                raise ValueError("task must not be empty.")
            return task, {}
        # Handle TaskMessage dataclass (from kernel.tasks)
        if hasattr(task, "content") and hasattr(task, "task_id"):
            prompt = str(getattr(task, "content", ""))
            if not prompt.strip():
                raise ValueError("task must not be empty.")
            return prompt, {"task_id": task.task_id, "type": getattr(task, "type", "")}
        prompt = str(task.get("prompt", task.get("description", "")))
        if not prompt.strip():
            raise ValueError("task dict must carry a 'prompt' or 'description'.")
        return prompt, dict(task)

    def _coerce_context(
        self, context: ExecutionContext | dict[str, Any] | None
    ) -> ExecutionContext:
        if context is None:
            return ExecutionContext(
                timeout_s=self._default_timeout_s,
                max_retries=self._default_max_retries,
                backoff_base_s=self._default_backoff_base_s,
            )
        if isinstance(context, ExecutionContext):
            return context
        merged: dict[str, Any] = {
            "timeout_s": self._default_timeout_s,
            "max_retries": self._default_max_retries,
            "backoff_base_s": self._default_backoff_base_s,
            **context,
        }
        return ExecutionContext.model_validate(merged)

    @staticmethod
    async def _echo_backend(prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        """Deprecated legacy echo backend.

        Kept for backwards compatibility only. New code paths use
        :class:`~agency.llm.adapter.LLMAdapter` (which itself falls back
        to echo mode when no credentials are configured).
        """
        return {"echo": prompt, "task_id": context.get("task_id")}


__all__ = ["AgentExecutor", "ExecutionContext", "ExecutionResult", "ExecutionStatus"]
