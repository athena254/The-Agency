"""Versioned prompt library for the governed Agent Factory.

Durable, versioned prompt records used by factory blueprints to describe the
instructions an activated agent receives. Prompt bodies are *inert data*: this
module never executes, imports, evaluates, or fetches anything. Composition
into a final system prompt is deterministic (:mod:`agency.factory.compose`).

Records are keyed by ``(prompt_id, version)`` and immutable once written. New
versions of an existing prompt must be registered separately. Built-in seed
records are loaded with provenance that starts with ``builtin:`` and are
published through :meth:`PromptRegistry.seed_builtin` idempotently.

REAP (REA Investigation Prompts) packs ship inside this library. Domain packs
must be paired with the ``reap-safety-scope`` segment; the factory enforces
that pairing fail-closed at validation.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

MAX_PROMPT_BODY_BYTES = 64 * 1024
MAX_PROMPT_NAME_LEN = 200
MAX_PROMPT_DESCRIPTION_LEN = 2000
MAX_PROMPT_PROVENANCE_LEN = 1024

REAP_PREFIX = "reap-"
REAP_SAFETY_PROMPT_ID = "reap-safety-scope"
REAP_EXEMPT_PROMPT_IDS = frozenset(
    {REAP_SAFETY_PROMPT_ID, "reap-wildcard", "reap-prompt-writing-tips", "reap-prompts"}
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_BUILTIN_PREFIX = "builtin:"
_EVIDENCE_STATUSES = frozenset({"APPROVED", "PUBLISHED"})


class PromptKind(str, Enum):
    """Structural role of a prompt record."""

    BASE = "BASE"
    SEGMENT = "SEGMENT"
    PACK = "PACK"


class PromptStatus(str, Enum):
    """Lifecycle states for a versioned prompt record."""

    DRAFT = "DRAFT"
    TESTING = "TESTING"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"


_ALLOWED_TRANSITIONS: dict[PromptStatus, frozenset[PromptStatus]] = {
    PromptStatus.DRAFT: frozenset({PromptStatus.TESTING}),
    PromptStatus.TESTING: frozenset({PromptStatus.APPROVED}),
    PromptStatus.APPROVED: frozenset({PromptStatus.PUBLISHED}),
    PromptStatus.PUBLISHED: frozenset({PromptStatus.DEPRECATED}),
    PromptStatus.DEPRECATED: frozenset({PromptStatus.RETIRED}),
    PromptStatus.RETIRED: frozenset(),
}


class PromptResolver(Protocol):
    """Structural contract for looking up a published prompt record."""

    def get(self, prompt_id: str, version: str) -> PromptSpec: ...


def _is_nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_id(value: Any, kind: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{kind} must be a nonblank string.")
    text = value.strip()
    if len(text) > 128:
        raise ValueError(f"{kind} exceeds 128 chars.")
    if _ID_RE.match(text) is None:
        raise ValueError(f"invalid {kind} {text!r}.")
    return text


def _check_version(value: Any) -> str:
    if not isinstance(value, str) or _VERSION_RE.match(value) is None:
        raise ValueError(f"invalid version {value!r}: must be exact X.Y.Z.")
    return value


@dataclass(frozen=True)
class PromptSpec:
    """Immutable definition of one versioned prompt record."""

    prompt_id: str
    version: str
    name: str
    description: str
    domain: str
    kind: PromptKind
    body: str
    provenance: str
    status: PromptStatus = PromptStatus.DRAFT

    def to_dict(self) -> dict[str, Any]:
        """Return a defensive copy of this spec as plain data."""
        return {
            "prompt_id": self.prompt_id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "kind": self.kind.value,
            "body": self.body,
            "provenance": self.provenance,
            "status": self.status.value,
        }


def _validate_spec(spec: PromptSpec, *, seeded: bool) -> None:
    _check_id(spec.prompt_id, "prompt_id")
    _check_version(spec.version)
    if not _is_nonblank(spec.name):
        raise ValueError("name must be a nonblank string.")
    if len(spec.name) > MAX_PROMPT_NAME_LEN:
        raise ValueError(f"name exceeds {MAX_PROMPT_NAME_LEN} chars.")
    if not isinstance(spec.description, str):
        raise TypeError("description must be a string.")
    if len(spec.description) > MAX_PROMPT_DESCRIPTION_LEN:
        raise ValueError(f"description exceeds {MAX_PROMPT_DESCRIPTION_LEN} chars.")
    _check_id(spec.domain, "domain")
    if not isinstance(spec.kind, PromptKind):
        raise TypeError("kind must be a PromptKind.")
    if not isinstance(spec.body, str) or not spec.body.strip():
        raise ValueError("body must be a nonblank string.")
    if len(spec.body.encode("utf-8")) > MAX_PROMPT_BODY_BYTES:
        raise ValueError(f"body exceeds {MAX_PROMPT_BODY_BYTES} bytes.")
    if not _is_nonblank(spec.provenance):
        raise ValueError("provenance must be a nonblank string.")
    if len(spec.provenance) > MAX_PROMPT_PROVENANCE_LEN:
        raise ValueError(f"provenance exceeds {MAX_PROMPT_PROVENANCE_LEN} chars.")
    if not isinstance(spec.status, PromptStatus):
        raise TypeError("status must be a PromptStatus.")
    if seeded:
        if spec.status is not PromptStatus.PUBLISHED:
            raise ValueError("seeded prompts must be PUBLISHED.")
        if not spec.provenance.startswith(_BUILTIN_PREFIX):
            raise ValueError("seeded prompts require 'builtin:' provenance.")
    elif spec.status is not PromptStatus.DRAFT:
        raise ValueError("new prompt versions must begin in DRAFT status.")


def _copy_spec(spec: PromptSpec) -> PromptSpec:
    return PromptSpec(
        prompt_id=spec.prompt_id,
        version=spec.version,
        name=spec.name,
        description=spec.description,
        domain=spec.domain,
        kind=spec.kind,
        body=spec.body,
        provenance=spec.provenance,
        status=spec.status,
    )


def _version_key(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return (int(major), int(minor), int(patch))


def reap_packs_missing_safety(prompt_ids: frozenset[str] | set[str]) -> set[str]:
    """Return REAP domain packs pinned without the mandatory safety segment."""
    packs = {
        pid
        for pid in prompt_ids
        if pid.startswith(REAP_PREFIX) and pid not in REAP_EXEMPT_PROMPT_IDS
    }
    if packs and REAP_SAFETY_PROMPT_ID not in prompt_ids:
        return packs
    return set()


class PromptRegistry:
    """SQLite-backed immutable store of versioned prompt records."""

    def __init__(self, db_path: str) -> None:
        if db_path != ":memory:":
            parent = Path(db_path).parent
            if str(parent) and not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS prompts ("
            "prompt_id TEXT NOT NULL, version TEXT NOT NULL, "
            "name TEXT NOT NULL, description TEXT NOT NULL, domain TEXT NOT NULL, "
            "kind TEXT NOT NULL, body TEXT NOT NULL, provenance TEXT NOT NULL, "
            "status TEXT NOT NULL, PRIMARY KEY (prompt_id, version))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS prompt_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, prompt_id TEXT NOT NULL, "
            "version TEXT NOT NULL, from_status TEXT NOT NULL, "
            "to_status TEXT NOT NULL, evidence TEXT NOT NULL, at TEXT NOT NULL)"
        )
        self._conn.commit()

    def _conn_required(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("prompt registry is closed.")
        return self._conn

    def close(self) -> None:
        """Close the underlying SQLite connection (idempotent)."""
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()

    def register(self, spec: PromptSpec) -> PromptSpec:
        """Store a new prompt version in DRAFT; duplicates raise ValueError."""
        _validate_spec(spec, seeded=False)
        conn = self._conn_required()
        now = datetime.now(UTC).isoformat()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO prompts (prompt_id, version, name, description,"
                    " domain, kind, body, provenance, status)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        spec.prompt_id,
                        spec.version,
                        spec.name,
                        spec.description,
                        spec.domain,
                        spec.kind.value,
                        spec.body,
                        spec.provenance,
                        spec.status.value,
                    ),
                )
                conn.execute(
                    "INSERT INTO prompt_events (prompt_id, version, from_status,"
                    " to_status, evidence, at) VALUES (?, ?, ?, ?, ?, ?)",
                    (spec.prompt_id, spec.version, "", spec.status.value, "", now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"duplicate prompt version: {(spec.prompt_id, spec.version)!r}."
            ) from exc
        return self.get(spec.prompt_id, spec.version)

    def seed_builtin(self, spec: PromptSpec, *, evidence: str) -> bool:
        """Insert a built-in prompt as PUBLISHED; idempotent.

        Existing rows are never overwritten, so re-running the loader cannot
        mutate a prompt that was already published or retired.
        """
        _validate_spec(spec, seeded=True)
        if not _is_nonblank(evidence):
            raise ValueError("builtin seed requires nonempty evidence.")
        conn = self._conn_required()
        now = datetime.now(UTC).isoformat()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO prompts (prompt_id, version, name, description,"
                    " domain, kind, body, provenance, status)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        spec.prompt_id,
                        spec.version,
                        spec.name,
                        spec.description,
                        spec.domain,
                        spec.kind.value,
                        spec.body,
                        spec.provenance,
                        spec.status.value,
                    ),
                )
                conn.execute(
                    "INSERT INTO prompt_events (prompt_id, version, from_status,"
                    " to_status, evidence, at) VALUES (?, ?, ?, ?, ?, ?)",
                    (spec.prompt_id, spec.version, "", spec.status.value, evidence, now),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def get(self, prompt_id: str, version: str) -> PromptSpec:
        """Return one prompt record; unknown raises KeyError."""
        conn = self._conn_required()
        row = conn.execute(
            "SELECT prompt_id, version, name, description, domain, kind, body,"
            " provenance, status FROM prompts WHERE prompt_id = ? AND version = ?",
            (prompt_id, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown prompt version: {(prompt_id, version)!r}.")
        return self._row_to_spec(row)

    def list_versions(self, prompt_id: str) -> list[PromptSpec]:
        """Return all versions for ``prompt_id`` sorted by semver."""
        conn = self._conn_required()
        rows = conn.execute(
            "SELECT prompt_id, version, name, description, domain, kind, body,"
            " provenance, status FROM prompts WHERE prompt_id = ?",
            (prompt_id,),
        ).fetchall()
        specs = [self._row_to_spec(row) for row in rows]
        specs.sort(key=lambda spec: _version_key(spec.version))
        return specs

    def is_published(self, prompt_id: str, version: str) -> bool:
        """Return whether the pinned prompt exists and is PUBLISHED."""
        try:
            spec = self.get(prompt_id, version)
        except KeyError:
            return False
        return spec.status is PromptStatus.PUBLISHED

    def transition(
        self,
        prompt_id: str,
        version: str,
        to_status: PromptStatus | str,
        evidence: str = "",
    ) -> PromptSpec:
        """Move one record forward a single lifecycle step; only status changes."""
        try:
            target = to_status if isinstance(to_status, PromptStatus) else PromptStatus(to_status)
        except ValueError as exc:
            raise ValueError(f"unknown status: {to_status!r}.") from exc
        current = self.get(prompt_id, version)
        if target not in _ALLOWED_TRANSITIONS[current.status]:
            raise ValueError(f"illegal transition: {current.status.value} -> {target.value}.")
        if target.value in _EVIDENCE_STATUSES and not _is_nonblank(evidence):
            raise ValueError(f"transition to {target.value} requires nonempty evidence.")
        conn = self._conn_required()
        with conn:
            updated = conn.execute(
                "UPDATE prompts SET status = ? WHERE prompt_id = ? AND version = ? AND status = ?",
                (target.value, prompt_id, version, current.status.value),
            )
            if updated.rowcount != 1:
                raise ValueError("prompt state changed concurrently; transition rejected.")
            conn.execute(
                "INSERT INTO prompt_events (prompt_id, version, from_status,"
                " to_status, evidence, at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    prompt_id,
                    version,
                    current.status.value,
                    target.value,
                    evidence,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return self.get(prompt_id, version)

    def publish(self, prompt_id: str, version: str, evidence: str) -> PromptSpec:
        """Publish an APPROVED prompt; requires nonempty evidence."""
        if not _is_nonblank(evidence):
            raise ValueError("publish requires nonempty evidence.")
        current = self.get(prompt_id, version)
        if current.status is not PromptStatus.APPROVED:
            raise ValueError(f"publish requires status APPROVED, found {current.status.value}.")
        return self.transition(prompt_id, version, PromptStatus.PUBLISHED, evidence)

    def list_events(self, prompt_id: str, version: str) -> list[dict[str, str]]:
        """Return durable lifecycle evidence in order; unknown raises KeyError."""
        self.get(prompt_id, version)
        conn = self._conn_required()
        rows = conn.execute(
            "SELECT from_status, to_status, evidence, at FROM prompt_events"
            " WHERE prompt_id = ? AND version = ? ORDER BY id",
            (prompt_id, version),
        ).fetchall()
        return [dict(zip(("from_status", "to_status", "evidence", "at"), row)) for row in rows]

    @staticmethod
    def _row_to_spec(row: tuple[Any, ...]) -> PromptSpec:
        (
            prompt_id,
            version,
            name,
            description,
            domain,
            kind,
            body,
            provenance,
            status,
        ) = row
        return PromptSpec(
            prompt_id=prompt_id,
            version=version,
            name=name,
            description=description,
            domain=domain,
            kind=PromptKind(kind),
            body=body,
            provenance=provenance,
            status=PromptStatus(status),
        )


__all__ = [
    "MAX_PROMPT_BODY_BYTES",
    "REAP_EXEMPT_PROMPT_IDS",
    "REAP_PREFIX",
    "REAP_SAFETY_PROMPT_ID",
    "PromptKind",
    "PromptRegistry",
    "PromptResolver",
    "PromptSpec",
    "PromptStatus",
    "reap_packs_missing_safety",
]
