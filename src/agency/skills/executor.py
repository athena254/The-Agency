"""Agency-native skill execution.

Resolves a published skill's opaque ``implementation`` logical operation name
to a registered callable, validates permissions, and runs it. Skills remain
metadata in the registry; this module is the single execution seam, so no
skill ever imports or executes arbitrary code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SkillRunResult:
    """Outcome of executing one skill."""

    skill_id: str
    version: str
    success: bool
    output: Any = None
    error: str | None = None


class OperationRegistry:
    """Maps logical operation names to callables."""

    def __init__(self) -> None:
        self._ops: dict[str, Callable[[dict[str, Any]], Any]] = {}

    def register(self, name: str, fn: Callable[[dict[str, Any]], Any]) -> None:
        if not name or not name.strip():
            raise ValueError("operation name must be nonblank")
        self._ops[name.strip()] = fn

    def get(self, name: str) -> Callable[[dict[str, Any]], Any] | None:
        return self._ops.get(name.strip())

    def names(self) -> list[str]:
        return sorted(self._ops)


class SkillExecutor:
    """Execute published skills against an OperationRegistry."""

    def __init__(
        self,
        operations: OperationRegistry,
        granted_permissions: frozenset[str] = frozenset(),
    ) -> None:
        self._operations = operations
        self._granted = granted_permissions

    def run(
        self,
        skill: Any,
        inputs: dict[str, Any] | None = None,
    ) -> SkillRunResult:
        inputs = inputs or {}
        skill_id = getattr(skill, "skill_id", "")
        version = getattr(skill, "version", "")
        status = getattr(skill, "status", None)
        status_val = getattr(status, "value", status)
        if str(status_val).upper() != "PUBLISHED":
            return SkillRunResult(skill_id=skill_id, version=version, success=False, error="skill not published")
        perms = tuple(getattr(skill, "permissions", ()) or ())
        if any(p not in self._granted for p in perms):
            return SkillRunResult(skill_id=skill_id, version=version, success=False, error="permission denied")
        impl = getattr(skill, "implementation", "")
        fn = self._operations.get(impl)
        if fn is None:
            return SkillRunResult(skill_id=skill_id, version=version, success=False, error=f"unknown operation: {impl!r}")
        try:
            output = fn(inputs)
            return SkillRunResult(skill_id=skill_id, version=version, success=True, output=output)
        except Exception as exc:  # noqa: BLE001 - surface as failure
            return SkillRunResult(skill_id=skill_id, version=version, success=False, error=f"{type(exc).__name__}: {exc}")