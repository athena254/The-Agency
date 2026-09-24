"""Workflow registry: immutable versioned definitions and run records.

SQLite-backed. Metadata only — never executes external code.
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from dataclasses import dataclass, field

MAX_STEPS = 64
MAX_JSON_BYTES = 64 * 1024

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_PERMISSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-:/]*$")

_MAX_ID_LEN = 128


def _check_id(value: str, kind: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{kind} must be a string.")
    if not value or not value.strip():
        raise ValueError(f"{kind} must be nonblank.")
    if len(value) > _MAX_ID_LEN:
        raise ValueError(f"{kind} exceeds {_MAX_ID_LEN} chars.")
    if not _ID_RE.match(value):
        raise ValueError(f"invalid {kind} {value!r}.")
    return value


def _check_permission(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("permission must be a string.")
    if not value or not value.strip():
        raise ValueError("permission must be nonblank.")
    if len(value) > _MAX_ID_LEN:
        raise ValueError("permission exceeds 128 chars.")
    if not _PERMISSION_RE.match(value):
        raise ValueError(f"invalid permission {value!r}.")
    return value


def _check_version(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("version must be a string.")
    if not _VERSION_RE.match(value):
        raise ValueError(
            f"invalid version {value!r}: must be exact dotted numeric X.Y.Z."
        )
    return value


def _dumps_bounded(value: object, max_bytes: int = MAX_JSON_BYTES) -> str:
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not JSON serializable: {exc}") from None
    if len(text.encode("utf-8")) > max_bytes:
        raise ValueError(f"JSON value exceeds {max_bytes} bytes.")
    return text


@dataclass(frozen=True)
class WorkflowStep:
    """One immutable DAG node."""

    step_id: str
    operation: str
    depends_on: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _check_id(self.step_id, "step_id")
        _check_id(self.operation, "operation")
        deps = tuple(self.depends_on) if self.depends_on is not None else ()
        perms = tuple(self.permissions) if self.permissions is not None else ()
        for dep in deps:
            _check_id(dep, "depends_on entry")
        for perm in perms:
            _check_permission(perm)
        object.__setattr__(self, "depends_on", deps)
        object.__setattr__(self, "permissions", perms)


@dataclass(frozen=True)
class WorkflowDefinition:
    """Immutable versioned workflow definition."""

    workflow_id: str
    version: str
    steps: tuple[WorkflowStep, ...] = ()

    def __post_init__(self) -> None:
        _check_id(self.workflow_id, "workflow_id")
        _check_version(self.version)
        steps = tuple(self.steps) if self.steps is not None else ()
        for step in steps:
            if not isinstance(step, WorkflowStep):
                raise TypeError("steps must be WorkflowStep instances.")
        object.__setattr__(self, "steps", steps)


@dataclass(frozen=True)
class WorkflowRun:
    """Immutable run record."""

    run_id: str
    workflow_id: str
    version: str
    actor_id: str
    status: str
    step_results: dict[str, dict] = field(default_factory=dict)
    created_at: str = ""
    completed_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id must be nonblank.")
        _check_id(self.workflow_id, "workflow_id")
        _check_version(self.version)
        if not isinstance(self.actor_id, str) or not self.actor_id.strip():
            raise ValueError("actor_id must be nonblank.")
        if self.status not in ("PENDING", "RUNNING", "COMPLETED", "FAILED"):
            raise ValueError(f"invalid status {self.status!r}.")
        copied = copy.deepcopy(self.step_results)
        object.__setattr__(self, "step_results", copied)


def _validate_dag(definition: WorkflowDefinition) -> None:
    steps = list(definition.steps)
    if len(steps) > MAX_STEPS:
        raise ValueError(f"too many steps: {len(steps)} > {MAX_STEPS}.")
    seen: set[str] = set()
    for step in steps:
        if step.step_id in seen:
            raise ValueError(f"duplicate step_id {step.step_id!r}.")
        seen.add(step.step_id)
    for step in steps:
        for dep in step.depends_on:
            if dep == step.step_id:
                raise ValueError(f"step {step.step_id!r} depends on itself.")
            if dep not in seen:
                raise ValueError(
                    f"step {step.step_id!r} has unknown dependency {dep!r}."
                )
    # Cycle detection (DFS).
    adjacency = {step.step_id: list(step.depends_on) for step in steps}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, stack: list[str]) -> None:
        if node in visited:
            return
        if node in visiting:
            cycle = " -> ".join([*stack, node])
            raise ValueError(f"dependency cycle detected: {cycle}.")
        visiting.add(node)
        stack.append(node)
        for dep in adjacency[node]:
            visit(dep, stack)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for step in steps:
        visit(step.step_id, [])


def _definition_to_json(definition: WorkflowDefinition) -> str:
    payload = {
        "workflow_id": definition.workflow_id,
        "version": definition.version,
        "steps": [
            {
                "step_id": step.step_id,
                "operation": step.operation,
                "depends_on": list(step.depends_on),
                "permissions": list(step.permissions),
            }
            for step in definition.steps
        ],
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _definition_from_json(text: str) -> WorkflowDefinition:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"corrupt definition record: {exc}") from None
    steps = tuple(
        WorkflowStep(
            step_id=item["step_id"],
            operation=item["operation"],
            depends_on=tuple(item.get("depends_on", ())),
            permissions=tuple(item.get("permissions", ())),
        )
        for item in payload.get("steps", [])
    )
    return WorkflowDefinition(
        workflow_id=payload["workflow_id"],
        version=payload["version"],
        steps=steps,
    )


class WorkflowRegistry:
    """SQLite store for definitions and run records."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS workflow_definitions ("
            "workflow_id TEXT NOT NULL, "
            "version TEXT NOT NULL, "
            "definition_json TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "PRIMARY KEY (workflow_id, version))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS workflow_runs ("
            "run_id TEXT PRIMARY KEY, "
            "workflow_id TEXT NOT NULL, "
            "version TEXT NOT NULL, "
            "actor_id TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "step_results_json TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "completed_at TEXT)"
        )
        self._conn.commit()

    def register(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        """Validate and store an immutable definition version."""
        if not isinstance(definition, WorkflowDefinition):
            raise TypeError("definition must be a WorkflowDefinition.")
        _validate_dag(definition)
        stored = WorkflowDefinition(
            workflow_id=definition.workflow_id,
            version=definition.version,
            steps=tuple(
                WorkflowStep(
                    step_id=s.step_id,
                    operation=s.operation,
                    depends_on=tuple(s.depends_on),
                    permissions=tuple(s.permissions),
                )
                for s in definition.steps
            ),
        )
        existing = self._conn.execute(
            "SELECT 1 FROM workflow_definitions WHERE workflow_id = ? AND version = ?",
            (stored.workflow_id, stored.version),
        ).fetchone()
        if existing is not None:
            raise ValueError(
                f"duplicate version {stored.workflow_id!r} {stored.version!r}."
            )
        import datetime as _dt

        created_at = _dt.datetime.now(_dt.UTC).isoformat()
        self._conn.execute(
            "INSERT INTO workflow_definitions (workflow_id, version, "
            "definition_json, created_at) VALUES (?, ?, ?, ?)",
            (
                stored.workflow_id,
                stored.version,
                _definition_to_json(stored),
                created_at,
            ),
        )
        self._conn.commit()
        return stored

    def get(self, workflow_id: str, version: str) -> WorkflowDefinition:
        """Return the exact pinned version; raises KeyError if missing."""
        _check_id(workflow_id, "workflow_id")
        _check_version(version)
        row = self._conn.execute(
            "SELECT definition_json FROM workflow_definitions "
            "WHERE workflow_id = ? AND version = ?",
            (workflow_id, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown workflow {workflow_id!r} version {version!r}.")
        return _definition_from_json(row[0])

    def list_versions(self, workflow_id: str) -> list[WorkflowDefinition]:
        """Return versions in insertion order."""
        _check_id(workflow_id, "workflow_id")
        rows = self._conn.execute(
            "SELECT definition_json FROM workflow_definitions "
            "WHERE workflow_id = ? ORDER BY rowid",
            (workflow_id,),
        ).fetchall()
        return [_definition_from_json(row[0]) for row in rows]

    def record_run(self, run: WorkflowRun) -> WorkflowRun:
        """Insert or replace a run record (used by the executor)."""
        if not isinstance(run, WorkflowRun):
            raise TypeError("run must be a WorkflowRun.")
        # Each step output has a 64KB cap; a whole run can legitimately
        # contain many such outputs and must not fail at the single-step cap.
        # Leave one step-sized envelope for IDs, status, and metadata around
        # 64 individually bounded outputs; otherwise valid final runs strand
        # their last persisted state at RUNNING.
        step_results_json = _dumps_bounded(run.step_results, MAX_JSON_BYTES * (MAX_STEPS + 1))
        self._conn.execute(
            "INSERT OR REPLACE INTO workflow_runs (run_id, workflow_id, version, "
            "actor_id, status, step_results_json, created_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.run_id,
                run.workflow_id,
                run.version,
                run.actor_id,
                run.status,
                step_results_json,
                run.created_at,
                run.completed_at,
            ),
        )
        self._conn.commit()
        return WorkflowRun(
            run_id=run.run_id,
            workflow_id=run.workflow_id,
            version=run.version,
            actor_id=run.actor_id,
            status=run.status,
            step_results=copy.deepcopy(run.step_results),
            created_at=run.created_at,
            completed_at=run.completed_at,
        )

    def save_run(self, run: WorkflowRun) -> WorkflowRun:
        """Alias of record_run for executor compatibility."""
        return self.record_run(run)

    def get_run(self, run_id: str) -> WorkflowRun:
        """Return a run record; raises KeyError if missing."""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be nonblank.")
        row = self._conn.execute(
            "SELECT run_id, workflow_id, version, actor_id, status, "
            "step_results_json, created_at, completed_at "
            "FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown run {run_id!r}.")
        try:
            step_results = json.loads(row[5])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"corrupt run record: {exc}") from None
        if not isinstance(step_results, dict):
            raise TypeError("corrupt run record: step_results must be an object.")
        return WorkflowRun(
            run_id=row[0],
            workflow_id=row[1],
            version=row[2],
            actor_id=row[3],
            status=row[4],
            step_results=step_results,
            created_at=row[6],
            completed_at=row[7],
        )

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()


__all__ = [
    "MAX_JSON_BYTES",
    "MAX_STEPS",
    "WorkflowDefinition",
    "WorkflowRegistry",
    "WorkflowRun",
    "WorkflowStep",
]
