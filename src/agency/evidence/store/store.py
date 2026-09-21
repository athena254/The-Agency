"""SQLite-backed, append-only evidence store.

Design notes
------------
* ``findings`` are mutable records whose full JSON document is stored in a
  ``document`` column; a handful of denormalised columns (``target``,
  ``severity``, ``confidence``, ...) support cheap filtered listing.
* ``evidence`` is strictly **append-only**: the table uses an ``AUTOINCREMENT``
  sequence for monotonic ordering and SQLite ``BEFORE UPDATE`` / ``BEFORE
  DELETE`` triggers raise ``ABORT`` for any stray mutation attempt. The public
  API only ever inserts.
* All writes go through a single connection guarded by an ``asyncio.Lock``, so
  concurrent callers converge on a serialised history.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, Self

import aiosqlite
from pydantic import BaseModel, Field
from structlog import get_logger

from .models import EvidenceEntry, EvidenceLevel, Finding, Severity, VerificationState, utcnow

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    id                  TEXT PRIMARY KEY,
    target              TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    severity            TEXT,
    verification_status TEXT,
    affected_component  TEXT,
    confidence          REAL NOT NULL,
    document            TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_target ON findings (target);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings (severity);
CREATE INDEX IF NOT EXISTS idx_findings_timestamp ON findings (timestamp);

CREATE TABLE IF NOT EXISTS evidence (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT UNIQUE NOT NULL,
    finding_id  TEXT NOT NULL REFERENCES findings (id),
    level       TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    source_agent TEXT,
    document    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_finding ON evidence (finding_id);
CREATE INDEX IF NOT EXISTS idx_evidence_level  ON evidence (level);

CREATE TRIGGER IF NOT EXISTS trg_evidence_no_update
BEFORE UPDATE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence is append-only: updates are not permitted');
END;

CREATE TRIGGER IF NOT EXISTS trg_evidence_no_delete
BEFORE DELETE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence is append-only: deletes are not permitted');
END;
"""


class EvidenceNotFoundError(KeyError):
    """Raised when an operation references a finding that does not exist."""


class FindingsFilter(BaseModel):
    """Query specification for :meth:`EvidenceStore.list_findings`."""

    target: str | None = Field(default=None)
    component: str | None = Field(default=None)
    severity: Severity | None = Field(default=None)
    verification_status: VerificationState | None = Field(default=None)
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    max_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    since: datetime | None = Field(default=None)
    until: datetime | None = Field(default=None)
    order_by: Literal["timestamp", "severity", "confidence"] = Field(default="timestamp")
    descending: bool = Field(default=True)
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _coerce_utc(value).isoformat()


class EvidenceStore:
    """Persistent store for findings and their append-only evidence trail."""

    _ORDER_COLUMNS: ClassVar[dict[str, str]] = {
        "timestamp": "timestamp",
        "severity": "severity",
        "confidence": "confidence",
    }

    _UPDATABLE: ClassVar[frozenset[str]] = frozenset(
        {
            "target",
            "evidence",
            "methodology",
            "confidence",
            "affected_component",
            "reproduction_status",
            "severity",
            "remediation",
            "verification_status",
            "provenance",
        }
    )

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = str(database)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> Self:
        """Open the database connection and apply the schema. Idempotent."""
        if self._conn is not None:
            return self

        self._conn = await aiosqlite.connect(self.database)
        if self.database != ":memory:":
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA foreign_keys=ON")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        logger.info("evidence_store_initialized", database=self.database)
        return self

    async def close(self) -> None:
        """Close the underlying connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> Self:
        return await self.initialize()

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    # -- findings ------------------------------------------------------------

    async def add_finding(self, finding: Finding) -> Finding:
        """Persist a new finding. Existing findings are rejected."""
        conn = self._require_conn()
        async with self._lock:
            await conn.execute(
                """
                INSERT INTO findings (
                    id, target, timestamp, severity, verification_status,
                    affected_component, confidence, document, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    finding.id,
                    finding.target,
                    _iso(finding.timestamp),
                    finding.severity.value if finding.severity else None,
                    finding.verification_status.value,
                    finding.affected_component,
                    finding.confidence,
                    finding.model_dump_json(),
                    _iso(utcnow()),
                ),
            )
            await conn.commit()
        logger.info("finding_added", finding_id=finding.id, target=finding.target)
        return finding

    async def get_finding(self, finding_id: str) -> Finding | None:
        """Return a finding by id, or ``None`` if it does not exist."""
        conn = self._require_conn()
        cursor = await conn.execute("SELECT document FROM findings WHERE id = ?", (finding_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return Finding.model_validate_json(row[0])

    async def list_findings(self, filters: FindingsFilter | None = None) -> list[Finding]:
        """List findings matching the given filters."""
        filters = filters or FindingsFilter()
        conn = self._require_conn()

        clauses: list[str] = []
        params: list[Any] = []
        if filters.target is not None:
            clauses.append("target LIKE ?")
            params.append(f"%{filters.target}%")
        if filters.component is not None:
            clauses.append("affected_component LIKE ?")
            params.append(f"%{filters.component}%")
        if filters.severity is not None:
            clauses.append("severity = ?")
            params.append(filters.severity.value)
        if filters.verification_status is not None:
            clauses.append("verification_status = ?")
            params.append(filters.verification_status.value)
        clauses.append("confidence >= ?")
        params.append(filters.min_confidence)
        clauses.append("confidence <= ?")
        params.append(filters.max_confidence)
        if filters.since is not None:
            clauses.append("timestamp >= ?")
            params.append(_iso(filters.since))
        if filters.until is not None:
            clauses.append("timestamp <= ?")
            params.append(_iso(filters.until))

        column = self._ORDER_COLUMNS[filters.order_by]
        direction = "DESC" if filters.descending else "ASC"
        query = f"""
            SELECT document FROM findings
            WHERE {' AND '.join(clauses)}
            ORDER BY {column} {direction}
            LIMIT ? OFFSET ?
        """
        params.extend([filters.limit, filters.offset])

        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()
        return [Finding.model_validate_json(row[0]) for row in rows]

    async def update_finding(self, finding_id: str, updates: dict[str, Any]) -> Finding:
        """Apply a partial update to a finding's mutable metadata.

        ``id`` and ``timestamp`` are immutable; evidence is never mutated.
        """
        current = await self.get_finding(finding_id)
        if current is None:
            raise EvidenceNotFoundError(finding_id)

        allowed = {key: value for key, value in updates.items() if key in self._UPDATABLE}
        unknown = set(updates) - set(self._UPDATABLE)
        if unknown:
            logger.warning("finding_update_ignored_fields", finding_id=finding_id, fields=sorted(unknown))

        merged = current.model_copy(update=allowed)
        await self._replace_finding(merged)
        logger.info("finding_updated", finding_id=finding_id, fields=sorted(allowed))
        return merged

    async def _replace_finding(self, finding: Finding) -> None:
        conn = self._require_conn()
        async with self._lock:
            await conn.execute(
                """
                UPDATE findings SET
                    target = ?, timestamp = ?, severity = ?,
                    verification_status = ?, affected_component = ?,
                    confidence = ?, document = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    finding.target,
                    _iso(finding.timestamp),
                    finding.severity.value if finding.severity else None,
                    finding.verification_status.value,
                    finding.affected_component,
                    finding.confidence,
                    finding.model_dump_json(),
                    _iso(utcnow()),
                    finding.id,
                ),
            )
            await conn.commit()

    # -- evidence (append-only) ---------------------------------------------

    async def add_evidence(self, entry: EvidenceEntry) -> EvidenceEntry:
        """Append an evidence entry. Idempotent per entry id."""
        if await self.get_finding(entry.finding_id) is None:
            raise EvidenceNotFoundError(entry.finding_id)

        conn = self._require_conn()
        async with self._lock:
            try:
                await conn.execute(
                    """
                    INSERT INTO evidence (id, finding_id, level, timestamp, source_agent, document)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.id,
                        entry.finding_id,
                        entry.level.value,
                        _iso(entry.timestamp),
                        entry.source_agent,
                        entry.model_dump_json(),
                    ),
                )
            except (aiosqlite.IntegrityError, aiosqlite.OperationalError) as exc:
                # UNIQUE id collisions are ignored rather than promoted to the caller;
                # a replayed write is not *new* evidence.
                if isinstance(exc, aiosqlite.IntegrityError):
                    logger.warning("evidence_insert_duplicate", evidence_id=entry.id)
                    return entry
                raise
            await conn.commit()
        logger.info(
            "evidence_appended",
            evidence_id=entry.id,
            finding_id=entry.finding_id,
            level=entry.level.value,
        )
        return entry

    async def evidence_for_finding(self, finding_id: str) -> list[EvidenceEntry]:
        """Return all evidence for a finding in strictly append order."""
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT document FROM evidence WHERE finding_id = ? ORDER BY seq ASC",
            (finding_id,),
        )
        rows = await cursor.fetchall()
        return [EvidenceEntry.model_validate_json(row[0]) for row in rows]

    async def latest_evidence_level(self, finding_id: str) -> EvidenceLevel | None:
        """Return the highest evidence level recorded for a finding."""
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT MAX(level) FROM evidence WHERE finding_id = ?",
            (finding_id,),
        )
        row = await cursor.fetchone()
        if row is None or row[0] is None:
            return None
        return EvidenceLevel(row[0])

    # -- internals -----------------------------------------------------------

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("EvidenceStore.initialize() must be called before use")
        return self._conn


__all__ = ["EvidenceNotFoundError", "EvidenceStore", "FindingsFilter"]