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

from agency.workflows.recovery import (
    RecoverableLock,
    RecoverableRun,
    definition_digest,
    input_digest,
)
from agency.workflows.registry import (
    MAX_JSON_BYTES,
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowRun,
    WorkflowStep,
)

Authorizer = Callable[[str, str], bool]
Operation = Callable[[dict[str, object], dict[str, object]], object]


def _utcnow_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.UTC).isoformat()


def _check_actor(actor_id: str) -> str:
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise ValueError("actor_id must be nonblank.")
    if len(actor_id) > 256:
        raise ValueError("actor_id exceeds 256 chars.")
    return actor_id


def _check_inputs(inputs: dict[str, object]) -> dict[str, object]:
    if not isinstance(inputs, dict):
        raise TypeError("inputs must be a dict.")
    try:
        text = json.dumps(inputs, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"inputs are not JSON serializable: {exc}") from None
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError(f"inputs exceed {MAX_JSON_BYTES} bytes.")
    return copy.deepcopy(inputs)


def _sanitize_error(exc: BaseException) -> str:
    # Exception messages may contain credentials or user data. Keep only the
    # exception type in durable run state; detailed diagnostics belong in a
    # separately protected audit channel.
    return type(exc).__name__


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
        replay_safe: frozenset[str] | set[str] = frozenset(),
        operation_versions: dict[str, str] | None = None,
    ) -> None:
        if not isinstance(registry, WorkflowRegistry):
            raise TypeError("registry must be a WorkflowRegistry.")
        self._registry = registry
        self._operations: dict[str, Operation] = dict(operations or {})
        self._authorizer = authorizer
        safe = frozenset(replay_safe)
        for name in safe:
            if not isinstance(name, str):
                raise TypeError("replay_safe entries must be strings.")
        unexpected = sorted(set(safe) - set(self._operations))
        if unexpected:
            raise ValueError(f"replay_safe must be a subset of operations: {unexpected!r}.")
        self._replay_safe: frozenset[str] = safe
        versions = dict(operation_versions or {})
        for key, value in versions.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("operation_versions must map strings to strings.")
        self._operation_versions: dict[str, str] = versions

    def _is_allowed(self, actor_id: str, permission: str) -> bool:
        if self._authorizer is None:
            return False
        try:
            return self._authorizer(actor_id, permission) is True
        except Exception:  # noqa: BLE001 - deny closed on authorizer failure
            return False

    def _require_file_db(self) -> str:
        db_path = self._registry.db_path
        if db_path == ":memory:":
            raise ValueError(":memory: is rejected for recoverable operations.")
        return db_path

    def _recoverable_lock(self) -> RecoverableLock:
        return RecoverableLock(self._require_file_db())

    def _strict_allowed(self, actor_id: str, permission: str) -> bool:
        if self._authorizer is None:
            return False
        try:
            return self._authorizer(actor_id, permission) is True
        except Exception:  # noqa: BLE001 - deny closed on authorizer failure
            return False

    def _ops_binding(self, definition: WorkflowDefinition) -> dict[str, str]:
        return {
            step.operation: self._operation_versions.get(step.operation, "")
            for step in definition.steps
        }

    @staticmethod
    def _snapshot_output(output: object) -> object:
        try:
            serialized = json.dumps(output, sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError(f"non-JSON-serializable output: {type(output).__name__}") from None
        if len(serialized.encode("utf-8")) > MAX_JSON_BYTES:
            raise ValueError(f"step output exceeds {MAX_JSON_BYTES} bytes")
        return json.loads(serialized)

    def _persist_recoverable_state(
        self,
        base: RecoverableRun,
        status: str,
        step_results: dict[str, dict[str, object]],
    ) -> RecoverableRun:
        record = RecoverableRun(
            run_id=base.run_id,
            workflow_id=base.workflow_id,
            version=base.version,
            actor_id=base.actor_id,
            status=status,
            step_results={sid: dict(entry) for sid, entry in step_results.items()},
            input_digest=base.input_digest,
            definition_digest=base.definition_digest,
            ops_binding=dict(base.ops_binding),
            created_at=base.created_at,
            updated_at=_utcnow_iso(),
        )
        return self._registry.record_recoverable_run(record)

    def _execute_remaining(
        self,
        *,
        definition: WorkflowDefinition,
        order: list[WorkflowStep],
        safe_inputs: dict[str, object],
        actor: str,
        stored: RecoverableRun,
    ) -> RecoverableRun:
        step_results: dict[str, dict[str, object]] = {
            sid: dict(entry) for sid, entry in stored.step_results.items()
        }
        previous_outputs: dict[str, object] = {}
        for sid, entry in step_results.items():
            if entry.get("status") == "ok":
                previous_outputs[sid] = copy.deepcopy(entry.get("output"))
        current = stored
        for step in order:
            existing = step_results.get(step.step_id)
            if existing is not None and existing.get("status") == "ok":
                continue
            if step.operation not in self._operations or step.operation not in self._replay_safe:
                step_results[step.step_id] = {
                    "status": "refused",
                    "output": None,
                    "error": "replay refused: not replay-safe",
                }
                return self._persist_recoverable_state(current, "INDETERMINATE", step_results)
            denied: str | None = None
            for permission in step.permissions:
                if not self._strict_allowed(actor, permission):
                    denied = permission
                    break
            if denied is not None:
                step_results[step.step_id] = {
                    "status": "failed",
                    "output": None,
                    "error": f"permission denied: {denied}",
                }
                return self._persist_recoverable_state(current, "FAILED", step_results)
            operation = self._operations[step.operation]
            step_results[step.step_id] = {
                "status": "running",
                "output": None,
                "error": None,
            }
            current = self._persist_recoverable_state(current, "RUNNING", step_results)
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
                return self._persist_recoverable_state(current, "FAILED", step_results)
            try:
                snapshot = self._snapshot_output(output)
            except ValueError as exc:
                step_results[step.step_id] = {
                    "status": "failed",
                    "output": None,
                    "error": str(exc),
                }
                return self._persist_recoverable_state(current, "FAILED", step_results)
            step_results[step.step_id] = {
                "status": "ok",
                "output": snapshot,
                "error": None,
            }
            previous_outputs[step.step_id] = copy.deepcopy(snapshot)
            current = self._persist_recoverable_state(current, "RUNNING", step_results)
        try:
            return self._persist_recoverable_state(current, "COMPLETED", step_results)
        except Exception:
            try:
                self._persist_recoverable_state(current, "FAILED", step_results)
            except Exception:  # noqa: BLE001,S110 - original failure must propagate
                pass
            raise

    # --- user-facing durable recovery API (SPEC_LOCAL_RECOVERY_V1 §4) ---

    def run(
        self,
        workflow_id: str,
        version: str,
        inputs: dict[str, object],
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

        step_results: dict[str, dict[str, object]] = {}
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
                serialized = json.dumps(output, sort_keys=True, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError):
                step_results[step.step_id] = {
                    "status": "failed",
                    "output": None,
                    "error": f"non-JSON-serializable output: {type(output).__name__}",
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
            # Keep a JSON snapshot, not the callback's mutable object: later
            # callbacks can still hold and mutate their own return value.
            snapshot = json.loads(serialized)
            step_results[step.step_id] = {
                "status": "ok",
                "output": snapshot,
                "error": None,
            }
            previous_outputs[step.step_id] = snapshot

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

    def run_recoverable(
        self,
        workflow_id: str,
        version: str,
        inputs: dict[str, object],
        actor_id: str,
    ) -> RecoverableRun:
        """Execute a pinned workflow durably; crash-recoverable via resume."""
        db_path = self._require_file_db()
        actor = _check_actor(actor_id)
        safe_inputs = _check_inputs(inputs)
        digest = input_digest(safe_inputs)
        definition = self._registry.get(workflow_id, version)
        order = topological_order(definition)
        lock = RecoverableLock(db_path)
        if not lock.acquire():
            raise RuntimeError("recoverable execution denied: lock held by a live worker.")
        try:
            started = RecoverableRun(
                run_id=uuid.uuid4().hex,
                workflow_id=workflow_id,
                version=version,
                actor_id=actor,
                status="RUNNING",
                step_results={},
                input_digest=digest,
                definition_digest=definition_digest(definition),
                ops_binding=self._ops_binding(definition),
                created_at=_utcnow_iso(),
                updated_at=None,
            )
            current = self._registry.record_recoverable_run(started)
            return self._execute_remaining(
                definition=definition,
                order=order,
                safe_inputs=safe_inputs,
                actor=actor,
                stored=current,
            )
        finally:
            lock.release()

    def resume(self, run_id: str, inputs: dict[str, object], actor_id: str) -> RecoverableRun:
        """Resume an INDETERMINATE run; caller re-supplies exact inputs."""
        db_path = self._require_file_db()
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be nonblank.")
        actor = _check_actor(actor_id)
        safe_inputs = _check_inputs(inputs)
        digest = input_digest(safe_inputs)
        lock = RecoverableLock(db_path)
        if not lock.acquire():
            raise RuntimeError("resume denied: lock held by a live worker.")
        try:
            stored = self._registry.get_recoverable_run(run_id)
            if stored.input_digest != digest:
                raise ValueError("resume inputs do not match recorded digest.")
            if stored.actor_id != actor:
                raise ValueError("resume actor does not match recorded actor.")
            definition = self._registry.get(stored.workflow_id, stored.version)
            if definition_digest(definition) != stored.definition_digest:
                raise ValueError("pinned definition changed; resume refused.")
            if self._ops_binding(definition) != dict(stored.ops_binding):
                raise ValueError("operation versions changed; resume refused.")
            if stored.status == "COMPLETED":
                return stored
            if stored.status != "INDETERMINATE":
                raise RuntimeError(f"run {run_id!r} is {stored.status}; reconcile before resume.")
            order = topological_order(definition)
            running = self._persist_recoverable_state(
                stored,
                "RUNNING",
                {sid: dict(entry) for sid, entry in stored.step_results.items()},
            )
            return self._execute_remaining(
                definition=definition,
                order=order,
                safe_inputs=safe_inputs,
                actor=actor,
                stored=running,
            )
        finally:
            lock.release()

    def reconcile(self) -> list[RecoverableRun]:
        """Mark crashed RUNNING recoverable runs INDETERMINATE (lock-gated)."""
        lock = self._recoverable_lock()
        if not lock.acquire():
            raise RuntimeError("reconcile denied: lock held by a live worker.")
        try:
            crashed = self._registry.list_recoverable_runs("RUNNING")
            reconciled: list[RecoverableRun] = []
            for stored in crashed:
                marked = RecoverableRun(
                    run_id=stored.run_id,
                    workflow_id=stored.workflow_id,
                    version=stored.version,
                    actor_id=stored.actor_id,
                    status="INDETERMINATE",
                    step_results={sid: dict(entry) for sid, entry in stored.step_results.items()},
                    input_digest=stored.input_digest,
                    definition_digest=stored.definition_digest,
                    ops_binding=dict(stored.ops_binding),
                    created_at=stored.created_at,
                    updated_at=_utcnow_iso(),
                )
                reconciled.append(self._registry.record_recoverable_run(marked))
            return reconciled
        finally:
            lock.release()

    def inspect_recoverable(self, run_id: str) -> RecoverableRun:
        """Read-only view of a recoverable run (never legacy rows)."""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be nonblank.")
        self._require_file_db()
        return self._registry.get_recoverable_run(run_id)


__all__ = ["WorkflowExecutor", "topological_order"]
