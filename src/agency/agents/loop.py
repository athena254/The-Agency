"""Think–act–observe–reflect agent loop.

:class:`AgentLoop` drives one task to completion by repeating the core cycle:

``think → act → observe → reflect → repeat``

* **think** — propose the next action (LLM reasoner or heuristic).
* **act** — execute it via the injected ``act_fn`` (tools, executor, ...).
* **observe** — wrap the raw outcome as an :class:`Observation`.
* **reflect** — assess progress and decide whether the task is done.

All four phases are individually callable (:meth:`step`, :meth:`observe`,
:meth:`reflect`) so orchestrators can single-step, inspect, or resume loops.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import Enum
from threading import RLock
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

ThinkFn = Callable[
    [str, list["StepResult"]], str | dict[str, Any] | Awaitable[str | dict[str, Any]]
]
ActFn = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class LoopStatus(str, Enum):
    """Terminal state of an agent loop."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    MAX_STEPS = "max_steps"

    def __str__(self) -> str:
        return self.value


class Thought(BaseModel):
    """The agent's plan for the next action."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    content: str = Field(min_length=1)
    action: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Observation(BaseModel):
    """What the environment returned after an action."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    action_name: str = Field(default="")
    outcome: Any = None
    success: bool = Field(default=True)
    error: str | None = Field(default=None)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Reflection(BaseModel):
    """Post-observation assessment: progress and termination."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    summary: str = Field(default="")
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    done: bool = Field(default=False)
    reason: str = Field(default="")


class StepResult(BaseModel):
    """One full think→act→observe→reflect iteration."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    step: int
    thought: Thought
    observation: Observation
    reflection: Reflection


class LoopResult(BaseModel):
    """Terminal outcome of :meth:`AgentLoop.run`."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    task: str
    status: LoopStatus
    steps: list[StepResult] = Field(default_factory=list)
    output: Any = None
    error: str | None = Field(default=None)


async def _maybe_await(value: Any | Awaitable[Any]) -> Any:
    if asyncio.iscoroutine(value) or isinstance(value, Awaitable):
        return await value
    return value


def _default_think(task: str, history: list[StepResult]) -> dict[str, Any]:
    """Heuristic reasoner: continue until reflection says otherwise."""
    return {"name": "respond", "args": {"task": task, "step": len(history) + 1}}


async def _default_act(action: dict[str, Any]) -> dict[str, Any]:
    return {"action": action.get("name", "noop"), "args": action.get("args", {})}


class AgentLoop:
    """Runs the think–act–observe–reflect cycle for a single task.

    Parameters
    ----------
    think_fn:
        Produces the next action from ``(task, history)``; sync or async.
        Defaults to a trivial heuristic (useful for tests / tool-driven loops).
    act_fn:
        Executes an action dict and returns its raw outcome; sync or async.
    max_steps:
        Hard cap on iterations before the loop stops with ``MAX_STEPS``.
    progress_target:
        Reflection marks the task done once progress reaches this value.
    """

    def __init__(
        self,
        think_fn: ThinkFn | None = None,
        act_fn: ActFn | None = None,
        *,
        max_steps: int = 10,
        progress_target: float = 1.0,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be >= 1.")
        self._think_fn: ThinkFn = think_fn or _default_think
        self._act_fn: ActFn = act_fn or _default_act
        self._max_steps = max_steps
        self._progress_target = progress_target
        self._task: str = ""
        self._history: list[StepResult] = []
        self._pending_observation: Observation | None = None
        self._pending_thought: Thought | None = None
        self._lock = RLock()
        self._log = structlog.get_logger(__name__)

    # ------------------------------------------------------------------ #
    # Main entry points
    # ------------------------------------------------------------------ #

    async def run(self, task: str | dict[str, Any]) -> LoopResult:
        """Run the loop until done, failed, or ``max_steps`` is reached."""
        description = task if isinstance(task, str) else str(task.get("description", task))
        if not description.strip():
            raise ValueError("task must not be empty.")
        self.reset(description.strip())
        while len(self._history) < self._max_steps:
            try:
                step_result = await self.step()
            except Exception as exc:  # noqa: BLE001 — surfaced as LoopResult.error
                self._log.exception("loop.step_failed", task=self._task)
                return LoopResult(
                    task=self._task,
                    status=LoopStatus.FAILED,
                    steps=list(self._history),
                    error=f"{type(exc).__name__}: {exc}",
                )
            if step_result.reflection.done:
                self._log.info("loop.completed", task=self._task, steps=len(self._history))
                return LoopResult(
                    task=self._task,
                    status=LoopStatus.COMPLETED,
                    steps=list(self._history),
                    output=step_result.observation.outcome,
                )
        self._log.warning("loop.max_steps", task=self._task, steps=len(self._history))
        return LoopResult(
            task=self._task,
            status=LoopStatus.MAX_STEPS,
            steps=list(self._history),
            output=self._history[-1].observation.outcome if self._history else None,
        )

    async def step(self) -> StepResult:
        """Execute exactly one think→act→observe→reflect iteration."""
        if not self._task:
            raise RuntimeError("AgentLoop.reset(task) must be called before step().")
        step_no = len(self._history) + 1

        raw_thought = await _maybe_await(self._think_fn(self._task, list(self._history)))
        thought = self._coerce_thought(raw_thought)
        with self._lock:
            self._pending_thought = thought

        try:
            outcome = await _maybe_await(self._act_fn(dict(thought.action)))
            observation = Observation(
                action_name=str(thought.action.get("name", "")),
                outcome=outcome,
                success=True,
            )
        except Exception as exc:  # noqa: BLE001 — action errors become observations
            observation = Observation(
                action_name=str(thought.action.get("name", "")),
                outcome=None,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )

        with self._lock:
            self._pending_observation = observation
        reflection = self.reflect()
        result = StepResult(
            step=step_no, thought=thought, observation=observation, reflection=reflection
        )
        with self._lock:
            self._history.append(result)
            self._pending_observation = None
            self._pending_thought = None
        self._log.info(
            "loop.step",
            task=self._task,
            step=step_no,
            done=reflection.done,
            progress=reflection.progress,
        )
        return result

    def observe(self) -> Observation:
        """Return the latest observation (or the in-flight one mid-step)."""
        with self._lock:
            if self._pending_observation is not None:
                return self._pending_observation
            if self._history:
                return self._history[-1].observation
        return Observation(action_name="", outcome=None, success=True)

    def reflect(self) -> Reflection:
        """Assess progress from history and decide termination.

        Heuristic: each successful observation adds ``1 / max_steps``
        progress; any ``done: true`` / ``finish`` action completes the loop;
        a failed observation never completes it.
        """
        with self._lock:
            history = list(self._history)
            pending_thought = self._pending_thought
            pending_obs = self._pending_observation
        observation = (
            pending_obs
            if pending_obs is not None
            else (history[-1].observation if history else None)
        )
        if observation is None:
            return Reflection(summary="No observations yet.", progress=0.0, done=False)

        successes = sum(1 for s in history if s.observation.success) + (
            1 if pending_obs is not None and pending_obs.success else 0
        )
        progress = min(1.0, successes / self._max_steps)
        action_name = (
            pending_thought.action.get("name", "") if pending_thought else ""
        ) or observation.action_name
        done_signal = (
            action_name.strip().lower() in {"finish", "done", "complete", "respond"}
            and observation.success
        )
        done = bool(done_signal and progress >= min(self._progress_target, 1.0 / self._max_steps))
        if not observation.success:
            done = False
        summary = (
            f"{'Succeeded' if observation.success else 'Failed'}: {action_name or 'action'} "
            f"({successes}/{self._max_steps} successful)."
        )
        return Reflection(
            summary=summary,
            progress=progress,
            done=done,
            reason="termination signal observed" if done else "continue",
        )

    def reset(self, task: str = "") -> None:
        """Clear history and (optionally) bind a new task."""
        with self._lock:
            if task:
                self._task = task
            self._history = []
            self._pending_observation = None
            self._pending_thought = None

    @property
    def history(self) -> list[StepResult]:
        with self._lock:
            return list(self._history)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _coerce_thought(raw: str | dict[str, Any]) -> Thought:
        if isinstance(raw, str):
            return Thought(content=raw, action={"name": "respond", "args": {"text": raw}})
        action = dict(raw.get("action", {}))
        if "name" not in action:
            action["name"] = str(raw.get("name", "respond"))
        content = str(raw.get("content", raw.get("thought", action["name"])))
        confidence = float(raw.get("confidence", 1.0))
        return Thought(content=content, action=action, confidence=max(0.0, min(1.0, confidence)))

    def __repr__(self) -> str:
        return f"AgentLoop(task={self._task!r}, steps={len(self._history)})"


def new_loop_id() -> str:
    """Generate a loop/run identifier."""
    return str(uuid4())


__all__ = [
    "AgentLoop",
    "LoopResult",
    "LoopStatus",
    "Observation",
    "Reflection",
    "StepResult",
    "Thought",
]
