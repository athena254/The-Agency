"""Agency-native versioned skill registry.

Durable metadata store for reusable capability definitions. This module
records *metadata only* — it never executes, imports, evaluates, or
fetches the named implementation. ``implementation`` is an opaque logical
operation name resolved elsewhere.
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

MAX_SERIALIZED_BYTES = 64 * 1024

_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
_IMPLEMENTATION_RE = re.compile(r"[a-z][a-z0-9_.-]*")

_EVIDENCE_STATUSES = frozenset({"APPROVED", "PUBLISHED"})


class SkillStatus(str, Enum):
    """Lifecycle states for a versioned skill definition."""

    DRAFT = "DRAFT"
    TESTING = "TESTING"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"


_ALLOWED_TRANSITIONS: dict[SkillStatus, frozenset[SkillStatus]] = {
    SkillStatus.DRAFT: frozenset({SkillStatus.TESTING}),
    SkillStatus.TESTING: frozenset({SkillStatus.APPROVED}),
    SkillStatus.APPROVED: frozenset({SkillStatus.PUBLISHED}),
    SkillStatus.PUBLISHED: frozenset({SkillStatus.DEPRECATED}),
    SkillStatus.DEPRECATED: frozenset({SkillStatus.RETIRED}),
    SkillStatus.RETIRED: frozenset(),
}


def _is_nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class SkillSpec:
    """Immutable definition of one versioned skill."""

    skill_id: str
    version: str
    name: str
    description: str
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    permissions: tuple[str, ...] = ()
    implementation: str = ""
    provenance: str = ""
    status: SkillStatus = SkillStatus.DRAFT

    def __post_init__(self) -> None:
        object.__setattr__(self, "inputs", copy.deepcopy(self.inputs))
        object.__setattr__(self, "outputs", copy.deepcopy(self.outputs))
        if not isinstance(self.permissions, (list, tuple)):
            raise TypeError("permissions must be a tuple or list of strings.")
        perms = tuple(self.permissions)
        object.__setattr__(self, "permissions", perms)
        if isinstance(self.status, str) and not isinstance(self.status, SkillStatus):
            try:
                object.__setattr__(self, "status", SkillStatus(self.status))
            except ValueError:
                pass

    def to_dict(self) -> dict[str, Any]:
        """Return a defensive deep copy of this spec as plain data."""
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "inputs": copy.deepcopy(self.inputs),
            "outputs": copy.deepcopy(self.outputs),
            "permissions": list(self.permissions),
            "implementation": self.implementation,
            "provenance": self.provenance,
            "status": self.status.value if isinstance(self.status, SkillStatus) else self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillSpec:
        """Build a spec from plain data without aliasing caller structures."""
        snapshot = copy.deepcopy(dict(data))
        status = snapshot.get("status", SkillStatus.DRAFT)
        if isinstance(status, str) and not isinstance(status, SkillStatus):
            status = SkillStatus(status)
        perms = snapshot.get("permissions", ())
        return cls(
            skill_id=snapshot["skill_id"],
            version=snapshot["version"],
            name=snapshot["name"],
            description=snapshot.get("description", ""),
            inputs=copy.deepcopy(snapshot.get("inputs", {})),
            outputs=copy.deepcopy(snapshot.get("outputs", {})),
            permissions=perms,
            implementation=snapshot["implementation"],
            provenance=snapshot["provenance"],
            status=status,
        )


def _copy_spec(spec: SkillSpec) -> SkillSpec:
    return SkillSpec(
        skill_id=spec.skill_id,
        version=spec.version,
        name=spec.name,
        description=spec.description,
        inputs=copy.deepcopy(spec.inputs),
        outputs=copy.deepcopy(spec.outputs),
        permissions=tuple(spec.permissions),
        implementation=spec.implementation,
        provenance=spec.provenance,
        status=spec.status,
    )


def _validate_spec(spec: SkillSpec, allowed_permissions: frozenset[str]) -> None:
    if not _is_nonblank(spec.skill_id):
        raise ValueError("skill_id must be a nonblank string.")
    if not isinstance(spec.version, str) or _VERSION_RE.fullmatch(spec.version) is None:
        raise ValueError("version must be a dotted numeric triplet 'major.minor.patch'.")
    if not _is_nonblank(spec.name):
        raise ValueError("name must be a nonblank string.")
    if not isinstance(spec.description, str):
        raise ValueError("description must be a string.")  # noqa: TRY004
    if not _is_nonblank(spec.implementation):
        raise ValueError("implementation must be a nonblank operation name.")
    if _IMPLEMENTATION_RE.fullmatch(spec.implementation) is None:
        raise ValueError(
            "implementation must be a logical operation name "
            "('[a-z][a-z0-9_.-]*'), never a path/import/URL."
        )
    if not _is_nonblank(spec.provenance):
        raise ValueError("provenance must be a nonblank string.")
    if not isinstance(spec.status, SkillStatus):
        raise ValueError("status must be a SkillStatus.")  # noqa: TRY004
    if spec.status is not SkillStatus.DRAFT:
        raise ValueError("new skill versions must begin in DRAFT status.")
    for label, mapping in (("inputs", spec.inputs), ("outputs", spec.outputs)):
        if not isinstance(mapping, dict):
            raise ValueError(f"{label} must be a mapping.")  # noqa: TRY004
        try:
            serialized = json.dumps(mapping, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be JSON serializable: {exc}") from None
        if len(serialized.encode("utf-8")) > MAX_SERIALIZED_BYTES:
            raise ValueError(f"{label} exceeds 64KB serialized cap.")
    if not isinstance(spec.permissions, (tuple, list)):
        raise ValueError("permissions must be a tuple of strings.")  # noqa: TRY004
    for perm in spec.permissions:
        if not isinstance(perm, str) or not perm:
            raise ValueError("permissions entries must be non-empty strings.")
        if perm not in allowed_permissions:
            raise ValueError(f"unrecognized permission: {perm!r}.")


def _version_key(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return (int(major), int(minor), int(patch))


class SkillRegistry:
    """SQLite-backed immutable store of versioned skill metadata."""

    def __init__(
        self,
        db_path: str,
        allowed_permissions: frozenset[str] | set[str] = frozenset(),
    ) -> None:
        self._db_path = db_path
        self._allowed_permissions = frozenset(allowed_permissions)
        if db_path != ":memory:":
            parent = Path(db_path).parent
            if str(parent) and not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS skills ("
            "skill_id TEXT NOT NULL, "
            "version TEXT NOT NULL, "
            "name TEXT NOT NULL, "
            "description TEXT NOT NULL, "
            "inputs_json TEXT NOT NULL, "
            "outputs_json TEXT NOT NULL, "
            "permissions_json TEXT NOT NULL, "
            "implementation TEXT NOT NULL, "
            "provenance TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "PRIMARY KEY (skill_id, version))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS skill_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id TEXT NOT NULL, "
            "version TEXT NOT NULL, from_status TEXT NOT NULL, "
            "to_status TEXT NOT NULL, evidence TEXT NOT NULL, at TEXT NOT NULL)"
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection (idempotent)."""
        conn, self._conn = self._conn, None  # type: ignore[assignment]
        if conn is not None:
            conn.close()

    def register(self, spec: SkillSpec) -> SkillSpec:
        """Store a new immutable skill version; duplicates raise ValueError."""
        _validate_spec(spec, self._allowed_permissions)
        try:
            self._conn.execute(
                "INSERT INTO skills (skill_id, version, name, description,"
                " inputs_json, outputs_json, permissions_json,"
                " implementation, provenance, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    spec.skill_id,
                    spec.version,
                    spec.name,
                    spec.description,
                    json.dumps(spec.inputs),
                    json.dumps(spec.outputs),
                    json.dumps(list(spec.permissions)),
                    spec.implementation,
                    spec.provenance,
                    spec.status.value,
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"duplicate skill version: {(spec.skill_id, spec.version)!r}."
            ) from exc
        return _copy_spec(spec)

    def get(self, skill_id: str, version: str) -> SkillSpec:
        """Return a defensive copy of one versioned skill; unknown raises KeyError."""
        row = self._conn.execute(
            "SELECT skill_id, version, name, description, inputs_json, outputs_json,"
            " permissions_json, implementation, provenance, status"
            " FROM skills WHERE skill_id = ? AND version = ?",
            (skill_id, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown skill version: {(skill_id, version)!r}.")
        return self._row_to_spec(row)

    def list_versions(self, skill_id: str) -> list[SkillSpec]:
        """Return defensive copies of all versions for ``skill_id``, sorted."""
        rows = self._conn.execute(
            "SELECT skill_id, version, name, description, inputs_json, outputs_json,"
            " permissions_json, implementation, provenance, status"
            " FROM skills WHERE skill_id = ?",
            (skill_id,),
        ).fetchall()
        specs = [self._row_to_spec(row) for row in rows]
        specs.sort(key=lambda s: _version_key(s.version))
        return specs

    def transition(
        self,
        skill_id: str,
        version: str,
        to_status: SkillStatus | str,
        evidence: str = "",
    ) -> SkillSpec:
        """Move one record forward a single lifecycle step; only status changes."""
        try:
            target = to_status if isinstance(to_status, SkillStatus) else SkillStatus(to_status)
        except ValueError as exc:
            raise ValueError(f"unknown status: {to_status!r}.") from exc
        current = self.get(skill_id, version)
        if target not in _ALLOWED_TRANSITIONS[current.status]:
            raise ValueError(f"illegal transition: {current.status.value} -> {target.value}.")
        if target.value in _EVIDENCE_STATUSES and not _is_nonblank(evidence):
            raise ValueError(f"transition to {target.value} requires nonempty evidence.")
        with self._conn:
            updated = self._conn.execute(
                "UPDATE skills SET status = ? WHERE skill_id = ? AND version = ? AND status = ?",
                (target.value, skill_id, version, current.status.value),
            )
            if updated.rowcount != 1:
                raise ValueError("skill state changed concurrently; transition rejected.")
            self._conn.execute(
                "INSERT INTO skill_events (skill_id, version, from_status, to_status, evidence, at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    skill_id,
                    version,
                    current.status.value,
                    target.value,
                    evidence,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return self.get(skill_id, version)

    def list_events(self, skill_id: str, version: str) -> list[dict[str, str]]:
        """Return durable evidence for lifecycle transitions in order."""
        self.get(skill_id, version)
        rows = self._conn.execute(
            "SELECT from_status, to_status, evidence, at FROM skill_events "
            "WHERE skill_id = ? AND version = ? ORDER BY id",
            (skill_id, version),
        ).fetchall()
        return [dict(zip(("from_status", "to_status", "evidence", "at"), row)) for row in rows]

    def publish(self, skill_id: str, version: str, evidence: str) -> SkillSpec:
        """Publish an APPROVED skill; requires nonempty evidence, status-only update."""
        if not _is_nonblank(evidence):
            raise ValueError("publish requires nonempty evidence.")
        current = self.get(skill_id, version)
        if current.status is not SkillStatus.APPROVED:
            raise ValueError(f"publish requires status APPROVED, found {current.status.value}.")
        return self.transition(skill_id, version, SkillStatus.PUBLISHED, evidence)

    @staticmethod
    def _row_to_spec(row: tuple[Any, ...]) -> SkillSpec:
        (
            skill_id,
            version,
            name,
            description,
            inputs_json,
            outputs_json,
            permissions_json,
            implementation,
            provenance,
            status,
        ) = row
        return SkillSpec(
            skill_id=skill_id,
            version=version,
            name=name,
            description=description,
            inputs=copy.deepcopy(json.loads(inputs_json)),
            outputs=copy.deepcopy(json.loads(outputs_json)),
            permissions=tuple(json.loads(permissions_json)),
            implementation=implementation,
            provenance=provenance,
            status=SkillStatus(status),
        )


__all__ = ["MAX_SERIALIZED_BYTES", "SkillRegistry", "SkillSpec", "SkillStatus"]
