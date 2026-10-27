"""Forge Phase 6 cost helper: record token usage and aggregate costs.

Records per-run token usage (model, tokens, cost) into a SQLite store and
produces bounded aggregate reports. Capture-only: does not call any provider.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

MAX_RUN_ID_CHARS = 128
MAX_MODEL_CHARS = 128
MAX_ENTRIES = 100_000

CostGate = Literal["PASS", "FAIL"]

CAVEATS: tuple[str, ...] = (
    "cost_capture_only",
    "no_provider_call",
    "release_not_authorized",
)


class CostEntry(BaseModel):
    """A single recorded cost entry for one run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str = Field(min_length=1)
    created_at: datetime
    run_id: str = Field(min_length=1, max_length=MAX_RUN_ID_CHARS)
    model: str = Field(min_length=1, max_length=MAX_MODEL_CHARS)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost: float = Field(default=0.0, ge=0)

    @property
    def cost_usd(self) -> float:
        return self.cost

    @property
    def tokens(self) -> int:
        return self.total_tokens


class CostReport(BaseModel):
    """Immutable bounded aggregate cost report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    entries: tuple[CostEntry, ...] = ()
    total_tokens: int = Field(default=0, ge=0)
    total_cost: float = Field(default=0.0, ge=0)
    by_model: dict[str, dict[str, float]] = Field(default_factory=dict)
    by_model_cost: dict[str, float] = Field(default_factory=dict)
    by_model_tokens: dict[str, int] = Field(default_factory=dict)
    gate: CostGate = "PASS"
    caveats: tuple[str, ...] = CAVEATS
    note: str = "Bounded cost capture; does not call providers or authorize release."

    @property
    def entry(self) -> CostEntry | None:
        return self.entries[-1] if self.entries else None

    @property
    def latest_entry(self) -> CostEntry | None:
        return self.entry


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forge_cost_entries (
    entry_id   TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    run_id     TEXT NOT NULL,
    model      TEXT NOT NULL,
    document   TEXT NOT NULL
);
"""


class CostStore:
    """Synchronous SQLite persistence for cost entries."""

    def __init__(self, db_path: str | Path = "forge_cost.db") -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, entry: CostEntry) -> str:
        if not isinstance(entry, CostEntry):
            raise TypeError("entry must be a CostEntry")
        self._conn.execute(
            "INSERT INTO forge_cost_entries (entry_id, created_at, run_id, model, document) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                entry.entry_id,
                entry.created_at.isoformat(),
                entry.run_id,
                entry.model,
                entry.model_dump_json(),
            ),
        )
        self._conn.commit()
        logger.info("forge.cost_saved", entry_id=entry.entry_id, model=entry.model)
        return entry.entry_id

    def list_entries(self) -> list[CostEntry]:
        cursor = self._conn.execute("SELECT document FROM forge_cost_entries ORDER BY created_at")
        return [CostEntry.model_validate_json(row[0]) for row in cursor.fetchall()]

    def entries(self) -> list[CostEntry]:
        return self.list_entries()

    def aggregate(self) -> CostReport:
        entries = self.list_entries()
        if len(entries) > MAX_ENTRIES:
            entries = entries[-MAX_ENTRIES:]
        total_tokens = sum(e.total_tokens for e in entries)
        total_cost = sum(e.cost for e in entries)
        by_cost: dict[str, float] = {}
        by_tokens: dict[str, int] = {}
        by_model: dict[str, dict[str, float]] = {}
        for e in entries:
            by_cost[e.model] = by_cost.get(e.model, 0.0) + e.cost
            by_tokens[e.model] = by_tokens.get(e.model, 0) + e.total_tokens
            by_model[e.model] = {
                "cost": by_cost[e.model],
                "tokens": float(by_tokens[e.model]),
            }
        report_id = str(uuid4())
        created_at = datetime.now(UTC)
        return CostReport(
            report_id=report_id,
            created_at=created_at,
            entries=tuple(entries),
            total_tokens=total_tokens,
            total_cost=total_cost,
            by_model=by_model,
            by_model_cost=by_cost,
            by_model_tokens=by_tokens,
            gate="PASS",
            caveats=CAVEATS,
        )

    def record(
        self,
        *,
        run_id: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int | None = None,
        cost: float = 0.0,
        cost_usd: float | None = None,
    ) -> CostEntry:
        if cost_usd is not None:
            cost = cost_usd
        if total_tokens is None:
            total_tokens = int(prompt_tokens) + int(completion_tokens)
        entry = CostEntry(
            entry_id=str(uuid4()),
            created_at=datetime.now(UTC),
            run_id=run_id,
            model=model,
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
            total_tokens=int(total_tokens),
            cost=float(cost),
        )
        self.save(entry)
        return entry

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _coerce_store(store_or_db: CostStore | str | Path) -> tuple[CostStore, bool]:
    if isinstance(store_or_db, CostStore):
        return store_or_db, False
    store = CostStore(store_or_db)
    return store, True


def record_cost(
    store_or_db: CostStore | str | Path,
    *,
    run_id: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int | None = None,
    cost: float = 0.0,
    cost_usd: float | None = None,
) -> CostReport:
    store, owned = _coerce_store(store_or_db)
    try:
        store.record(
            run_id=run_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost=cost,
            cost_usd=cost_usd,
        )
        report = store.aggregate()
    finally:
        if owned:
            store.close()
    logger.info("forge.cost_recorded", run_id=run_id, model=model)
    return report
