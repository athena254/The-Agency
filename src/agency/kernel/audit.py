"""Immutable audit log backed by SQLite (async via ``aiosqlite``).

Every agent action that touches a target must be recorded here. The log is
write-once: entries are ``frozen`` Pydantic models, the storage layer only
ever issues ``INSERT`` statements, and ``entry_id`` is the PRIMARY KEY — there
is no update or delete path in this module by design.

The schema mirrors :class:`AuditEntry` exactly so the database is the
system of record even when application code changes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

import aiosqlite
import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.kernel.identity import utcnow
from agency.kernel.policies import ActionClass


class AuditEntry(BaseModel):
    """A single, immutable audit record.

    ``entry_id`` is generated at creation and impossible to mutate afterwards
    (``frozen=True``), preserving a tamper-evident chain.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=utcnow)
    agent: str | None = Field(default=None, description="Agent id that performed the action.")
    task: str | None = Field(default=None, description="Task id the action belongs to.")
    target: str | None = Field(default=None, description="Scope or resource the action targeted.")
    authorization: str | None = Field(
        default=None, description="Match of the decision, e.g. 'granted' / 'denied' / permission id."
    )
    capability: str | None = Field(default=None, description="Capability exercised.")
    action: ActionClass | str | None = Field(default=None, description="Action class attempted.")
    result: str = Field(default="allowed", description="Outcome, e.g. 'allowed', 'denied', 'failed'.")
    evidence: dict[str, Any] | None = Field(default=None, description="Supporting evidence payload.")
    model: str | None = Field(default=None, description="Model that proposed/executed the action.")
    model_version: str | None = Field(default=None)
    tool_version: str | None = Field(default=None)
    environment: dict[str, Any] | None = Field(default=None)

    def as_ts(self) -> str:
        """Return a stable ISO-8601 timestamp for storage."""
        ts = self.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts.isoformat()


class AuditFilter(BaseModel):
    """Query filter for :meth:`AuditLog.query`; all fields are optional."""

    agent: str | None = None
    task: str | None = None
    target: str | None = None
    action: str | None = None
    result: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=100, ge=1, le=10_000)
    offset: int = Field(default=0, ge=0)

    def _clauses(self) -> tuple[list[str], list[Any]]:
        where: list[str] = []
        params: list[Any] = []
        if self.agent is not None:
            where.append("agent = ?")
            params.append(self.agent)
        if self.task is not None:
            where.append("task = ?")
            params.append(self.task)
        if self.target is not None:
            where.append("target = ?")
            params.append(self.target)
        if self.action is not None:
            where.append("action = ?")
            params.append(self.action)
        if self.result is not None:
            where.append("result = ?")
            params.append(self.result)
        if self.since is not None:
            where.append("timestamp >= ?")
            params.append(_iso(self.since))
        if self.until is not None:
            where.append("timestamp <= ?")
            params.append(_iso(self.until))
        return where, params


class AuditLog:
    """Append-only audit store.

    Usage
    -----
    .. code-block:: python

        log = AuditLog("audit.db")
        await log.initialize()
        await log.append(AuditEntry(agent="red-1", action="L2_CONTROLLED_TESTING", result="allowed"))
        rows = await log.query(AuditFilter(agent="red-1"))
        await log.close()
    """

    def __init__(self, db_path: str | Path, *, table: str = "audit_entries") -> None:
        self._db_path = Path(db_path)
        self._table = table
        self._conn: aiosqlite.Connection | None = None
        self._log = structlog.get_logger(__name__)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def initialize(self) -> None:
        """Open the SQLite connection and create the schema if needed."""
        if self._conn is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                entry_id       TEXT PRIMARY KEY,
                timestamp      TEXT NOT NULL,
                agent          TEXT,
                task           TEXT,
                target         TEXT,
                authorization  TEXT,
                capability     TEXT,
                action         TEXT,
                result         TEXT NOT NULL,
                evidence       TEXT,
                model          TEXT,
                model_version  TEXT,
                tool_version   TEXT,
                environment    TEXT
            )
            """
        )
        await self._conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self._table}_timestamp ON {self._table} (timestamp)"
        )
        await self._conn.commit()
        self._log.info("audit.ready", path=str(self._db_path))

    async def close(self) -> None:
        """Flush and close the underlying connection."""
        if self._conn is not None:
            await self._conn.commit()
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> Self:
        await self.initialize()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # ------------------------------------------------------------------ #
    # Write path (append-only by construction)
    # ------------------------------------------------------------------ #

    async def append(self, entry: AuditEntry) -> str:
        """Record an entry; returns its ``entry_id``.

        Raises ``ValueError`` on duplicate ``entry_id`` (PK collision), which
        signals a tampering attempt against the chain.
        """
        conn = self._require_ready()
        sql = (
            f"INSERT INTO {self._table} "
            "(entry_id, timestamp, agent, task, target, authorization, capability, "
            " action, result, evidence, model, model_version, tool_version, environment) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        row = (
            entry.entry_id,
            entry.as_ts(),
            entry.agent,
            entry.task,
            entry.target,
            entry.authorization,
            entry.capability,
            _enum_value(entry.action),
            entry.result,
            _json(entry.evidence),
            entry.model,
            entry.model_version,
            entry.tool_version,
            _json(entry.environment),
        )
        try:
            await conn.execute(sql, row)
            await conn.commit()
        except aiosqlite.IntegrityError as exc:
            if "entry_id" in str(exc):
                raise ValueError(f"duplicate audit entry_id {entry.entry_id!r} — chain tampering?") from exc
            raise
        self._log.info(
            "audit.append",
            entry_id=entry.entry_id,
            agent=entry.agent,
            action=_enum_value(entry.action),
            result=entry.result,
        )
        return entry.entry_id

    # ------------------------------------------------------------------ #
    # Read path
    # ------------------------------------------------------------------ #

    async def query(self, filters: AuditFilter | None = None) -> list[AuditEntry]:
        """Fetch entries matching optional filters, newest first."""
        conn = self._require_ready()
        filters = filters or AuditFilter()
        where, params = filters._clauses()
        sql = f"SELECT * FROM {self._table}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params += [filters.limit, filters.offset]
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [self._row_to_entry(tuple(row)) for row in rows]

    async def count(self, filters: AuditFilter | None = None) -> int:
        """Return the number of entries matching optional filters."""
        conn = self._require_ready()
        filters = filters or AuditFilter()
        where, params = filters._clauses()
        sql = f"SELECT COUNT(*) FROM {self._table}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        cursor = await conn.execute(sql, params)
        row = await cursor.fetchone()
        total = row[0] if row is not None else 0
        return int(total)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _require_ready(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("AuditLog not initialized — call initialize() first")
        return self._conn

    def _row_to_entry(self, row: tuple[Any, ...]) -> AuditEntry:
        columns = [
            "entry_id",
            "timestamp",
            "agent",
            "task",
            "target",
            "authorization",
            "capability",
            "action",
            "result",
            "evidence",
            "model",
            "model_version",
            "tool_version",
            "environment",
        ]
        raw = dict(zip(columns, row))
        return AuditEntry(
            entry_id=raw["entry_id"],
            timestamp=datetime.fromisoformat(raw["timestamp"]),
            agent=raw["agent"],
            task=raw["task"],
            target=raw["target"],
            authorization=raw["authorization"],
            capability=raw["capability"],
            action=_parse_action(raw["action"]),
            result=raw["result"],
            evidence=_unjson(raw["evidence"]),
            model=raw["model"],
            model_version=raw["model_version"],
            tool_version=raw["tool_version"],
            environment=_unjson(raw["environment"]),
        )

    def __repr__(self) -> str:
        return f"AuditLog(path={str(self._db_path)!r})"


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True, default=str)


def _unjson(value: str | None) -> Any:
    return None if value is None else json.loads(value)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _enum_value(value: Any) -> Any:
    if isinstance(value, ActionClass):
        return value.value
    return value


def _parse_action(value: str | None) -> ActionClass | str | None:
    if value is None:
        return None
    try:
        return ActionClass(value)
    except ValueError:
        return value