"""Deterministic workflow executor (workflow v1).

Executes only pre-registered deterministic callables in stable topological
order. Fails closed on unknown operations, denied permissions, oversized or
non-JSON data, and callback errors. Never claims automatic resume or
exactly-once execution.
"""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Callable, Mapping

from agency.workflows.registry import (
    MAX_JSON_BYTES,
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowRun,
    WorkflowStep,
)

Authorizer = Callable[[str, str], bool]
Operation = Callable[[dict, dict[str, object]], object]


def _utcnow_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.UTC).isoformat()


def _check_actor(actor_id: str) -> str:
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise ValueError("actor_id must be nonblank.")
    if len(actor_id) > 256:
        raise ValueError("actor_id exceeds 256 chars.")
    return actor_id


def _check_inputs(inputs: dict) -> dict:
    if not isinstance(inputs, dict):
        raise TypeError("inputs must be a dict.")
    try:
        text = json.dumps(inputs, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"inputs are not JSON serializable: {exc}") from None
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError(f"inputs exceed {MAX_JSON_BYTES} bytes.")
    return copy.deepcopy(inputs)


def _sanitize_error(exc: BaseException) -> str:
    message = f"{type(exc).__name__}: {exc}"
    if len(message) > 1000:
        message = message[:1000]
    return message


def topological_order(definition: WorkflowDefinition) -> list[WorkflowStep]:
    """Return stable topological order (declaration index tie-break)."""
    steps = list(definition.steps)
    index = {step.step_id: pos for pos, step in enumerate(steps)}
    dependents: dict[str, list[str]] = {step.step_id: [] for step in steps}
    indegree: dict[str, int] = {step.step_id: 0 for step in steps}
    for step in steps:
        for dep in step.depends_on:
            if dep not in indegree:
                raise ValueError(f"unknown dependency {dep!r}.")
            indegree[step.step_id] += 1
            dependents[dep].append(step.step_id)
    ready = sorted(
        [sid for sid, deg in indegree.items() if deg == 0],
        key=lambda sid: index[sid],
    )
    ordered_ids: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered_ids.append(current)
        for child in sorted(dependents[current], key=lambda sid: index[sid]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
        ready.sort(key=lambda sid: index[sid])
    if len(ordered_ids) != len(steps):
        raise ValueError("dependency cycle detected.")
    by_id = {step.step_id: step for step in steps}
    return [by_id[sid] for sid in ordered_ids]


class WorkflowExecutor:
    """Sync executor over trusted operation callbacks."""

    def __init__(
        self,
        registry: WorkflowRegistry,
        operations: Mapping[str, Operation],
        authorizer: Authorizer | None = None,
    ) -> None:
        if not isinstance(registry, WorkflowRegistry):
            raise TypeError("registry must be a WorkflowRegistry.")
        self._registry = registry
        self._operations: dict[str, Operation] = dict(operations or {})
        self._authorizer = authorizer

    def _is_allowed(self, actor_id: str, permission: str) -> bool:
        if self._authorizer is None:
            return False
        try:
            return bool(self._authorizer(actor_id, permission))
        except Exception:  # noqa: BLE001 - deny closed on authorizer failure
            return False

    def run(
        self,
        workflow_id: str,
        version: str,
        inputs: dict,
        actor_id: str,
    ) -> WorkflowRun:
        """Execute a pinned workflow version; persists FAILED or COMPLETED."""
        actor = _check_actor(actor_id)
        safe_inputs = _check_inputs(inputs)
        definition = self._registry.get(workflow_id, version)
        order = topological_order(definition)

        run_id = uuid.uuid4().hex
        created_at = _utcnow_iso()
        pending = WorkflowRun(
            run_id=run_id,
            workflow_id=workflow_id,
            version=version,
            actor_id=actor,
            status="PENDING",
            step_results={},
            created_at=created_at,
            completed_at=None,
        )
        self._registry.record_run(pending)
        running = WorkflowRun(
            run_id=run_id,
            workflow_id=workflow_id,
            version=version,
            actor_id=actor,
            status="RUNNING",
            step_results={},
            created_at=created_at,
            completed_at=None,
        )
        self._registry.record_run(running)

        step_results: dict[str, dict] = {}
        previous_outputs: dict[str, object] = {}
        failed = False

        for step in order:
            operation = self._operations.get(step.operation)
            if operation is None:
                step_results[step.step_id] = {
                    "status": "failed",
                    "output": None,
                    "error": f"unknown operation: {step.operation}",
                }
                failed = True
                break
            denied: str | None = None
            for permission in step.permissions:
                if not self._is_allowed(actor, permission):
                    denied = permission
                    break
            if denied is not None:
                step_results[step.step_id] = {
                    "status": "failed",
                    "output": None,
                    "error": f"permission denied: {denied}",
                }
                failed = True
                break
            try:
                output = operation(
                    copy.deepcopy(safe_inputs),
                    copy.deepcopy(previous_outputs),
                )
            except Exception as exc:  # noqa: BLE001 - recorded, not raised
                step_results[step.step_id] = {
                    "status": "failed",
                    "output": None,
                    "error": _sanitize_error(exc),
                }
                failed = True
                break
            try:
                serialized = json.dumps(output, sort_keys=True, ensure_ascii=False)
            except (TypeError, ValueError):
                step_results[step.step_id] = {
                    "status": "failed",
                    "output": None,
                    "error": "non-JSON-serializable output: "
                    f"{type(output).__name__}",
                }
                failed = True
                break
            if len(serialized.encode("utf-8")) > MAX_JSON_BYTES:
                step_results[step.step_id] = {
                    "status": "failed",
                    "output": None,
                    "error": f"step output exceeds {MAX_JSON_BYTES} bytes",
                }
                failed = True
                break
            step_results[step.step_id] = {
                "status": "ok",
                "output": output,
                "error": None,
            }
            previous_outputs[step.step_id] = output

        status = "FAILED" if failed else "COMPLETED"
        completed_at = _utcnow_iso()
        final_run = WorkflowRun(
            run_id=run_id,
            workflow_id=workflow_id,
            version=version,
            actor_id=actor,
            status=status,
            step_results=step_results,
            created_at=created_at,
            completed_at=completed_at,
        )
        self._registry.record_run(final_run)
        return final_run


__all__ = ["WorkflowExecutor", "topological_order"]
