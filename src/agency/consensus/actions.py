"""Wave 1 real-action primitives for the consensus actuator.

Hermetic, deterministic, no clock/network. Defines action specs, a registry
of executors (built-in deterministic + optional AgentExecutor adapter), and
the outcome record shape. AgentExecutor integration is opt-in and only
activated when a provider is configured.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """A bounded, content-addressed action to execute under a grant."""

    proposal_id: str
    task_id: str
    name: str
    args: dict[str, Any]
    affected_consumers: tuple[str, ...]

    @property
    def args_digest(self) -> str:
        return json.dumps(self.args, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    ok: bool
    value: Any
    error: str | None = None


Executor = Callable[[ActionSpec], Any]


class ActionRegistry:
    """Maps action names to executor callables."""

    def __init__(self) -> None:
        self._executors: dict[str, Executor] = {}
        self.register("echo", _echo)
        self.register("calculator", _calculator)

    def register(self, name: str, fn: Executor) -> None:
        self._executors[name] = fn

    def has(self, name: str) -> bool:
        return name in self._executors

    def run(self, spec: ActionSpec) -> ActionOutcome:
        fn = self._executors.get(spec.name)
        if fn is None:
            return ActionOutcome(False, None, f"unknown action {spec.name}")
        try:
            value = fn(spec)
        except Exception as exc:  # noqa: BLE001 - fail closed
            return ActionOutcome(False, None, str(exc))
        return ActionOutcome(True, value)


def _echo(spec: ActionSpec) -> Any:
    return spec.args.get("text", "")


def _calculator(spec: ActionSpec) -> Any:
    op = spec.args.get("op")
    a = float(spec.args["a"])
    b = float(spec.args["b"])
    if op == "add":
        return a + b
    if op == "sub":
        return a - b
    if op == "mul":
        return a * b
    if op == "div":
        if b == 0:
            raise ValueError("division by zero")
        return a / b
    raise ValueError(f"unknown op {op}")


def make_agent_executor_adapter(executor: Any) -> Executor:
    """Wrap an AgentExecutor into an action Executor (opt-in)."""

    def _run(spec: ActionSpec) -> Any:
        result = executor.execute(
            spec.args.get("task", spec.name),
            task_id=spec.task_id,
        )
        return getattr(result, "value", result)

    return _run
