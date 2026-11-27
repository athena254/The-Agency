"""Durable loop session state for the autonomous Ralph-style loop.

One session = one goal + one work queue + one proof chain. State is
persisted to SQLite after every event so a crashed run can be resumed
without redoing verified work. This module holds no loop logic; the
driver in :mod:`agency.autonomy.loop_driver` owns the while-loop.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from agency.autonomy.proof import ProofEvent
from agency.core.sqlite_store import open_store

LoopStatus = Literal["PENDING", "RUNNING", "PAUSED", "COMPLETED", "FAILED"]
ItemStatus = Literal["pending", "active", "done", "blocked"]


class LoopItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    acceptance: tuple[str, ...] = ()
    priority: int = 0
    deps: tuple[str, ...] = ()
    status: ItemStatus = "pending"
    attempts: int = 0
    detail: str = ""


class LoopBudgets(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_hours: float | None = None
    max_iterations: int | None = None
    max_cost_usd: float | None = None


class LoopEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int
    timestamp: str
    event: str
    item_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class LoopState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    status: LoopStatus = "PENDING"
    worker: str = ""
    branch: str = ""
    worktree_path: str = ""
    queue: tuple[LoopItem, ...] = ()
    current_item_id: str = ""
    iteration: int = 0
    cost_usd: float = 0.0
    budgets: LoopBudgets = Field(default_factory=LoopBudgets)
    events: tuple[LoopEvent, ...] = ()
    proof_events: tuple[ProofEvent, ...] = ()
    proof_head: str = Field(default="0" * 64)
    report_ids: tuple[str, ...] = ()
    detail: str = ""
    created_at: datetime
    updated_at: datetime


_SCHEMA = """
CREATE TABLE IF NOT EXISTS loop_sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status     TEXT NOT NULL,
    document   TEXT NOT NULL
);
"""


class LoopStore:
    def __init__(self, db_path: str | Path = "loop.db") -> None:
        self._db_path = Path(db_path)
        self._conn = open_store(self._db_path, _SCHEMA)

    def save(self, state: LoopState) -> str:
        self._conn.execute(
            "INSERT INTO loop_sessions (session_id, created_at, updated_at, status, document) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "updated_at=excluded.updated_at, status=excluded.status, document=excluded.document",
            (
                state.session_id,
                state.created_at.isoformat(),
                state.updated_at.isoformat(),
                state.status,
                state.model_dump_json(),
            ),
        )
        self._conn.commit()
        return state.session_id

    def load(self, session_id: str) -> LoopState | None:
        cursor = self._conn.execute(
            "SELECT document FROM loop_sessions WHERE session_id = ?", (session_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return LoopState.model_validate_json(row[0])

    def list_sessions(self) -> list[dict[str, Any]]:
        cursor = self._conn.execute(
            "SELECT session_id, created_at, updated_at, status FROM loop_sessions "
            "ORDER BY updated_at DESC"
        )
        return [
            {
                "session_id": row[0],
                "created_at": row[1],
                "updated_at": row[2],
                "status": row[3],
            }
            for row in cursor.fetchall()
        ]

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = [
    "ItemStatus",
    "LoopBudgets",
    "LoopEvent",
    "LoopItem",
    "LoopState",
    "LoopStatus",
    "LoopStore",
]
