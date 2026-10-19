"""Local recovery helpers: OS file lock, digests, recoverable run record.

Single-host SQLite bookkeeping only. No peer authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, BinaryIO, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agency.workflows.registry import WorkflowDefinition


class _DefinitionLike(Protocol):
    workflow_id: str
    version: str
    steps: Sequence[Any]


RECOVERY_SCHEMA_VERSION = 1
RECOVERABLE_STATUSES = ("RUNNING", "INDETERMINATE", "COMPLETED", "FAILED")


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not JSON serializable: {exc}") from None


def input_digest(inputs: dict[str, object]) -> str:
    """SHA-256 over canonical bounded JSON of inputs (digest only, never raw)."""
    if not isinstance(inputs, dict):
        raise TypeError("inputs must be a dict.")
    text = _canonical_json(inputs)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def definition_digest(definition: WorkflowDefinition | _DefinitionLike) -> str:
    """Digest of pinned definition identity + topology (rejects silent change)."""
    steps = definition.steps
    payload = {
        "workflow_id": definition.workflow_id,
        "version": definition.version,
        "steps": [
            {
                "step_id": s.step_id,
                "operation": s.operation,
                "depends_on": list(s.depends_on),
                "permissions": list(s.permissions),
            }
            for s in steps
        ],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def ops_binding_digest(binding: dict[str, str]) -> str:
    return hashlib.sha256(_canonical_json(binding).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RecoverableRun:
    """Durable recoverable run record (versioned, bound, never legacy)."""

    run_id: str
    workflow_id: str
    version: str
    actor_id: str
    status: str
    step_results: dict[str, dict[str, object]] = field(default_factory=dict)
    input_digest: str = ""
    definition_digest: str = ""
    ops_binding: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str | None = None
    schema_version: int = RECOVERY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id must be nonblank.")
        if self.status not in RECOVERABLE_STATUSES:
            raise ValueError(f"invalid recoverable status {self.status!r}.")
        if self.schema_version != RECOVERY_SCHEMA_VERSION:
            raise ValueError("unsupported recovery schema version.")
        object.__setattr__(self, "step_results", copy.deepcopy(self.step_results))
        object.__setattr__(self, "ops_binding", dict(self.ops_binding))


class RecoverableLock:
    """Nonblocking OS file lock adjacent to the concrete DB file.

    Windows: ``msvcrt.locking``; POSIX: ``flock(LOCK_EX | LOCK_NB)``.
    A live (even hung) holder retains the lock; never steal on timeout.
    The kernel releases it on process kill. ``:memory:`` is rejected.
    """

    def __init__(self, db_path: str) -> None:
        if db_path == ":memory:":
            raise ValueError(":memory: is rejected for recoverable operations.")
        self._path = db_path + ".recover.lock"
        self._handle: BinaryIO | None = None

    @property
    def path(self) -> str:
        return self._path

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        handle: BinaryIO = open(self._path, "a+b")  # noqa: SIM115 - lock handle
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    handle.close()
                    return False
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    handle.close()
                    return False
        except Exception:
            handle.close()
            raise
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            handle.close()


__all__ = [
    "RECOVERABLE_STATUSES",
    "RECOVERY_SCHEMA_VERSION",
    "RecoverableLock",
    "RecoverableRun",
    "definition_digest",
    "input_digest",
    "ops_binding_digest",
]
