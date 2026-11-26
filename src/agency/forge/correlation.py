"""Task correlation and idempotency for parallel Forge evidence.

Every queue entry, worktree slot, gate report and approval event must carry the
same task id. This module adds that key plus an append-only, idempotent event
log; duplicate events with the same key are ignored, never duplicated.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_TEXT_CHARS = 2000


class CorrelationEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1)
    transition: str = Field(min_length=1)
    source_sha: str = ""
    key: str = ""
    created_at: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


def make_key(task_id: str, transition: str, source_sha: str = "") -> str:
    raw = f"{task_id}|{transition}|{source_sha}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def _bounded(value: Any) -> str:
    return str(value)[:MAX_TEXT_CHARS]


def correlate(
    report: dict[str, Any], *, task_id: str, transition: str, source_sha: str = ""
) -> dict[str, Any]:
    """Return a copy of a report stamped with its task id and idempotency key."""
    if not str(task_id).strip() or not str(transition).strip():
        raise ValueError("task_id and transition are required")
    stamped = dict(report)
    stamped["task_id"] = _bounded(task_id)
    stamped["transition"] = _bounded(transition)
    stamped["source_sha"] = _bounded(source_sha)
    stamped["idempotency_key"] = make_key(str(task_id), str(transition), str(source_sha))
    return stamped


class CorrelationStore:
    """Append-only JSONL log with in-memory key dedup."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._keys: set[str] = set()
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    self._keys.add(json.loads(line)["key"])
                except (ValueError, KeyError):
                    continue

    def append(
        self,
        *,
        task_id: str,
        transition: str,
        source_sha: str = "",
        payload: dict[str, Any] | None = None,
    ) -> CorrelationEvent | None:
        """Append an event; return None when the same key was already recorded."""
        key = make_key(task_id, transition, source_sha)
        with self._lock:
            if key in self._keys:
                return None
            event = CorrelationEvent(
                task_id=_bounded(task_id),
                transition=_bounded(transition),
                source_sha=_bounded(source_sha),
                key=key,
                created_at=datetime.now(UTC).isoformat(),
                payload=payload or {},
            )
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(event.model_dump_json() + "\n")
            self._keys.add(key)
            return event

    def events(self) -> tuple[CorrelationEvent, ...]:
        with self._lock:
            return tuple(
                CorrelationEvent.model_validate_json(line)
                for line in self.path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
