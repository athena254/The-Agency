"""Durable peer-local assignment journal for bounded autonomy (A5 slice 3a).

This is the task/assignment foundation beside
:mod:`agency.admission.store`, not a second global task authority and not a
parallel ownership model. One SQLite database holds assignments, an ordered
transactional outbox and the integrity state that makes the two auditable.

Every state change and its single outbox row commit in one ``BEGIN IMMEDIATE``
transaction. Replaying an identical event is a no-op that returns the stored
record, so it never re-dispatches the work. Each task carries one monotonically
advancing generation: a caller whose generation is behind the task's current
one is rejected before any state changes.

The module reads no clock, generates no randomness and opens no network. All
time values arrive from the caller. At open, assignments left ``RUNNING`` are
flipped to ``UNCERTAIN`` with their own last time value; they are never
auto-resumed. The outbox is an at-least-once reconciliation hint, not proof of
exactly-once external effects: consumers must still deduplicate, and this
module performs no effects itself.

Rows carry canonical integrity digests and the outbox is an append-only
hash chain, so edits, gaps and reordering make opening fail closed. As with
any single-store chain, an owner that controls the database can recompute the
chain after editing it; independently held signed heads are not provided here.
No record carries free text, prompts, credentials or private paths: only
bounded identifiers, digests, integer times and an enumerated state.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Self

from agency.peers.canonical import (
    SchemaError,
    canonical_json,
    digest_json,
    validate_closed,
    validate_digest,
    validate_enum,
    validate_optional_digest,
    validate_timestamp,
    validate_u64,
)

__all__ = [
    "STATES",
    "AssignmentJournal",
    "AssignmentJournalError",
    "AssignmentRecord",
    "OutboxEntry",
    "Transition",
]

STATES: Final = frozenset({"ASSIGNED", "RUNNING", "COMPLETED", "FAILED", "UNCERTAIN", "CANCELLED"})

_SCHEMA_VERSION: Final = 1
_EVENT_DOMAIN: Final = "assignment-journal-event-v1"
_ASSIGNMENT_DOMAIN: Final = "assignment-journal-assignment-v1"
_CHAIN_DOMAIN: Final = "assignment-journal-chain-v1"
_ZERO_CHAIN: Final = "0" * 64
_ID_RE: Final = re.compile(r"[a-z]{2,16}_[0-9a-f]{32}\Z")

_EVENT_FIELDS: Final = frozenset(
    {
        "domain",
        "assignment_id",
        "task_id",
        "worker_id",
        "generation",
        "state",
        "at",
        "grant_digest",
        "result_digest",
    }
)
_ASSIGNMENT_FIELDS: Final = frozenset(
    {
        "domain",
        "assignment_id",
        "task_id",
        "worker_id",
        "grant_digest",
        "generation",
        "state",
        "result_digest",
        "created_at",
        "updated_at",
    }
)

_ALLOWED: Final = {
    "ASSIGNED": frozenset({"RUNNING", "FAILED", "CANCELLED", "UNCERTAIN"}),
    "RUNNING": frozenset({"COMPLETED", "FAILED", "UNCERTAIN", "CANCELLED"}),
    "UNCERTAIN": frozenset({"COMPLETED", "FAILED", "CANCELLED"}),
    "COMPLETED": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
}

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS journal_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments (
    assignment_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    grant_digest TEXT,
    generation INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN
        ('ASSIGNED','RUNNING','COMPLETED','FAILED','UNCERTAIN','CANCELLED')),
    result_digest TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    integrity_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id TEXT NOT NULL,
    state TEXT NOT NULL,
    generation INTEGER NOT NULL,
    body TEXT NOT NULL,
    integrity_digest TEXT NOT NULL UNIQUE,
    chain_digest TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assignment_task ON assignments(task_id, generation);
"""


class AssignmentJournalError(SchemaError):
    """A persisted record failed closed, the store is closed, or the caller is stale."""


def _validate_id(value: Any, field: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise AssignmentJournalError(f"invalid {field}")
    return value


def _event_body(
    *,
    assignment_id: str,
    task_id: str,
    worker_id: str,
    generation: int,
    state: str,
    at: int,
    grant_digest: str | None,
    result_digest: str | None,
) -> dict[str, Any]:
    return {
        "domain": _EVENT_DOMAIN,
        "assignment_id": _validate_id(assignment_id, "assignment_id"),
        "task_id": _validate_id(task_id, "task_id"),
        "worker_id": _validate_id(worker_id, "worker_id"),
        "generation": validate_u64(generation, "generation"),
        "state": validate_enum(state, STATES, "state"),
        "at": validate_timestamp(at, "at"),
        "grant_digest": validate_optional_digest(grant_digest, "grant_digest"),
        "result_digest": validate_optional_digest(result_digest, "result_digest"),
    }


def _new_record(
    *,
    assignment_id: str,
    task_id: str,
    worker_id: str,
    grant_digest: str | None,
    generation: int,
    state: str,
    result_digest: str | None,
    created_at: int,
    updated_at: int,
    integrity_digest: str | None = None,
) -> AssignmentRecord:
    return AssignmentRecord(
        assignment_id=assignment_id,
        task_id=task_id,
        worker_id=worker_id,
        grant_digest=grant_digest,
        generation=generation,
        state=state,
        result_digest=result_digest,
        created_at=created_at,
        updated_at=updated_at,
        integrity_digest=integrity_digest
        if integrity_digest is not None
        else digest_json(
            {
                "domain": _ASSIGNMENT_DOMAIN,
                "assignment_id": assignment_id,
                "task_id": task_id,
                "worker_id": worker_id,
                "grant_digest": grant_digest,
                "generation": generation,
                "state": state,
                "result_digest": result_digest,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        ),
    )


def _chain(previous: str, seq: int, integrity_digest: str) -> str:
    return digest_json(
        {
            "domain": _CHAIN_DOMAIN,
            "seq": seq,
            "prev": previous,
            "integrity_digest": integrity_digest,
        }
    )


@dataclass(frozen=True, slots=True)
class AssignmentRecord:
    """One immutable-by-integrity view of a durable assignment."""

    assignment_id: str
    task_id: str
    worker_id: str
    grant_digest: str | None
    generation: int
    state: str
    result_digest: str | None
    created_at: int
    updated_at: int
    integrity_digest: str

    def __post_init__(self) -> None:
        _validate_id(self.assignment_id, "assignment_id")
        _validate_id(self.task_id, "task_id")
        _validate_id(self.worker_id, "worker_id")
        if self.grant_digest is not None:
            validate_digest(self.grant_digest, "grant_digest")
        validate_u64(self.generation, "generation")
        validate_enum(self.state, STATES, "state")
        if self.result_digest is not None:
            validate_digest(self.result_digest, "result_digest")
        validate_timestamp(self.created_at, "created_at")
        validate_timestamp(self.updated_at, "updated_at")
        validate_digest(self.integrity_digest, "integrity_digest")
        if digest_json(self.integrity_body()) != self.integrity_digest:
            raise AssignmentJournalError("assignment integrity digest mismatch")

    def integrity_body(self) -> dict[str, Any]:
        """Canonical projection hashed into ``integrity_digest``."""

        return {
            "domain": _ASSIGNMENT_DOMAIN,
            "assignment_id": self.assignment_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "grant_digest": self.grant_digest,
            "generation": self.generation,
            "state": self.state,
            "result_digest": self.result_digest,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    """One ordered, integrity-bound reconciliation record."""

    seq: int
    assignment_id: str
    state: str
    generation: int
    body: Mapping[str, Any]
    integrity_digest: str
    chain_digest: str

    def __post_init__(self) -> None:
        validate_u64(self.seq, "seq")
        _validate_id(self.assignment_id, "assignment_id")
        validate_enum(self.state, STATES, "state")
        validate_u64(self.generation, "generation")
        validate_closed(self.body, _EVENT_FIELDS, "outbox body")
        validate_digest(self.integrity_digest, "integrity_digest")
        validate_digest(self.chain_digest, "chain_digest")
        if digest_json(dict(self.body)) != self.integrity_digest:
            raise AssignmentJournalError("outbox integrity digest mismatch")


@dataclass(frozen=True, slots=True)
class Transition:
    """Result of one apply. ``outbox`` is None on an exact replay."""

    record: AssignmentRecord
    outbox: OutboxEntry | None
    replayed: bool

    @property
    def dispatched(self) -> bool:
        return not self.replayed


class AssignmentJournal:
    """SQLite-backed assignment journal with an atomic ordered outbox.

    ``:memory:`` is supported for hermetic tests; persisted deployments use a
    file path with WAL and ``synchronous=FULL``. Opening verifies every row and
    the whole outbox chain, then marks interrupted ``RUNNING`` work uncertain.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = self._connect()
        self._records: dict[str, AssignmentRecord] = {}
        self._outbox: list[OutboxEntry] = []
        self._last_chain: str = _ZERO_CHAIN
        conn = self._conn
        try:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO journal_meta(key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            self._load()
            self._recover()
        except AssignmentJournalError:
            self.close()
            raise
        except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            self.close()
            raise AssignmentJournalError(f"corrupt assignment journal: {exc}") from exc
        except BaseException:
            self.close()
            raise

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            conn.execute("PRAGMA synchronous=FULL")
            synchronous = conn.execute("PRAGMA synchronous").fetchone()
            if synchronous is None or synchronous[0] != 2:
                raise AssignmentJournalError("SQLite did not enable synchronous=FULL")
            if self._db_path != ":memory:" and (mode is None or mode[0] != "wal"):
                raise AssignmentJournalError("SQLite did not enable WAL")
            return conn
        except BaseException:
            conn.close()
            raise

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise AssignmentJournalError("assignment journal is closed")
        return self._conn

    def _load(self) -> None:
        conn = self._db()
        version = conn.execute(
            "SELECT value FROM journal_meta WHERE key='schema_version'"
        ).fetchone()
        if version is None or version[0] != str(_SCHEMA_VERSION):
            raise AssignmentJournalError("assignment journal schema version mismatch")
        records: dict[str, AssignmentRecord] = {}
        for row in conn.execute("SELECT * FROM assignments ORDER BY assignment_id"):
            record = _new_record(
                assignment_id=row["assignment_id"],
                task_id=row["task_id"],
                worker_id=row["worker_id"],
                grant_digest=row["grant_digest"],
                generation=row["generation"],
                state=row["state"],
                result_digest=row["result_digest"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                integrity_digest=row["integrity_digest"],
            )
            records[record.assignment_id] = record
        entries: list[OutboxEntry] = []
        previous = _ZERO_CHAIN
        expected_seq = 1
        for row in conn.execute("SELECT * FROM outbox ORDER BY seq"):
            try:
                body = json.loads(row["body"])
            except (TypeError, ValueError) as exc:
                raise AssignmentJournalError("undecodable outbox body") from exc
            entry = OutboxEntry(
                seq=row["seq"],
                assignment_id=row["assignment_id"],
                state=row["state"],
                generation=row["generation"],
                body=body,
                integrity_digest=row["integrity_digest"],
                chain_digest=row["chain_digest"],
            )
            if entry.seq != expected_seq:
                raise AssignmentJournalError("outbox sequence gap")
            if (
                body["assignment_id"] != entry.assignment_id
                or body["state"] != entry.state
                or body["generation"] != entry.generation
            ):
                raise AssignmentJournalError("outbox body does not match its row")
            if _chain(previous, entry.seq, entry.integrity_digest) != entry.chain_digest:
                raise AssignmentJournalError("tampered outbox chain")
            stored = records.get(entry.assignment_id)
            if stored is None or stored.generation != entry.generation:
                raise AssignmentJournalError("outbox references an unknown assignment")
            previous = entry.chain_digest
            expected_seq += 1
            entries.append(entry)
        last_state: dict[str, str] = {}
        for entry in entries:
            last_state[entry.assignment_id] = entry.state
        for assignment_id, record in records.items():
            if last_state.get(assignment_id) != record.state:
                raise AssignmentJournalError("assignment state disagrees with outbox")
        self._records = records
        self._outbox = entries
        self._last_chain = previous

    def _recover(self) -> None:
        running = [record for record in self._records.values() if record.state == "RUNNING"]
        if not running:
            return
        conn = self._db()
        conn.execute("BEGIN IMMEDIATE")
        try:
            previous = self._last_chain
            seq = self._outbox[-1].seq if self._outbox else 0
            updated: list[AssignmentRecord] = []
            logged: list[OutboxEntry] = []
            for record in running:
                body = _event_body(
                    assignment_id=record.assignment_id,
                    task_id=record.task_id,
                    worker_id=record.worker_id,
                    generation=record.generation,
                    state="UNCERTAIN",
                    at=record.updated_at,
                    grant_digest=record.grant_digest,
                    result_digest=record.result_digest,
                )
                integrity = digest_json(body)
                seq += 1
                chain = _chain(previous, seq, integrity)
                recovered = _new_record(
                    assignment_id=record.assignment_id,
                    task_id=record.task_id,
                    worker_id=record.worker_id,
                    grant_digest=record.grant_digest,
                    generation=record.generation,
                    state="UNCERTAIN",
                    result_digest=record.result_digest,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
                conn.execute(
                    "UPDATE assignments SET state=?, integrity_digest=? WHERE assignment_id=?",
                    (recovered.state, recovered.integrity_digest, record.assignment_id),
                )
                conn.execute(
                    "INSERT INTO outbox(seq, assignment_id, state, generation, body, "
                    "integrity_digest, chain_digest) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        seq,
                        record.assignment_id,
                        "UNCERTAIN",
                        record.generation,
                        canonical_json(body).decode("utf-8"),
                        integrity,
                        chain,
                    ),
                )
                previous = chain
                updated.append(recovered)
                logged.append(
                    OutboxEntry(
                        seq=seq,
                        assignment_id=record.assignment_id,
                        state="UNCERTAIN",
                        generation=record.generation,
                        body=body,
                        integrity_digest=integrity,
                        chain_digest=chain,
                    )
                )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise AssignmentJournalError(f"journal recovery failed: {exc}") from exc
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        for record in updated:
            self._records[record.assignment_id] = record
        self._outbox.extend(logged)
        self._last_chain = previous

    @staticmethod
    def _current_generation(conn: sqlite3.Connection, task_id: str) -> int | None:
        row = conn.execute(
            "SELECT MAX(generation) FROM assignments WHERE task_id=?", (task_id,)
        ).fetchone()
        value = row[0] if row is not None else None
        return None if value is None else int(value)

    def _commit(self, body: dict[str, Any]) -> Transition:
        conn = self._db()
        integrity = digest_json(body)
        assignment_id = body["assignment_id"]
        task_id = body["task_id"]
        generation = body["generation"]
        state = body["state"]
        conn.execute("BEGIN IMMEDIATE")
        try:
            replay = conn.execute(
                "SELECT seq FROM outbox WHERE integrity_digest=?", (integrity,)
            ).fetchone()
            if replay is not None:
                conn.execute("ROLLBACK")
                return Transition(self._records[assignment_id], None, True)
            current = self._current_generation(conn, task_id)
            existing = self._records.get(assignment_id)
            if existing is None:
                if state != "ASSIGNED":
                    raise AssignmentJournalError("assignment does not exist")
                if current is not None and generation <= current:
                    raise AssignmentJournalError("stale generation")
                record = _new_record(
                    assignment_id=assignment_id,
                    task_id=task_id,
                    worker_id=body["worker_id"],
                    grant_digest=body["grant_digest"],
                    generation=generation,
                    state="ASSIGNED",
                    result_digest=None,
                    created_at=body["at"],
                    updated_at=body["at"],
                )
                conn.execute(
                    "INSERT INTO assignments(assignment_id, task_id, worker_id, grant_digest, "
                    "generation, state, result_digest, created_at, updated_at, integrity_digest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.assignment_id,
                        record.task_id,
                        record.worker_id,
                        record.grant_digest,
                        record.generation,
                        record.state,
                        record.result_digest,
                        record.created_at,
                        record.updated_at,
                        record.integrity_digest,
                    ),
                )
            else:
                if state == "ASSIGNED":
                    raise AssignmentJournalError("assignment already exists")
                if existing.task_id != task_id or existing.worker_id != body["worker_id"]:
                    raise AssignmentJournalError("assignment binding conflict")
                if body["grant_digest"] != existing.grant_digest:
                    raise AssignmentJournalError("grant digest binding conflict")
                if generation != existing.generation or current is None or generation != current:
                    raise AssignmentJournalError("stale generation")
                if state not in _ALLOWED[existing.state]:
                    raise AssignmentJournalError("invalid state transition")
                if state == "COMPLETED" and body["result_digest"] is None:
                    raise AssignmentJournalError("completion requires a result digest")
                if (
                    existing.result_digest is not None
                    and body["result_digest"] != existing.result_digest
                ):
                    raise AssignmentJournalError("result digest conflict")
                record = _new_record(
                    assignment_id=assignment_id,
                    task_id=task_id,
                    worker_id=body["worker_id"],
                    grant_digest=existing.grant_digest,
                    generation=generation,
                    state=state,
                    result_digest=body["result_digest"],
                    created_at=existing.created_at,
                    updated_at=body["at"],
                )
                conn.execute(
                    "UPDATE assignments SET state=?, result_digest=?, updated_at=?, integrity_digest=? "
                    "WHERE assignment_id=?",
                    (
                        record.state,
                        record.result_digest,
                        record.updated_at,
                        record.integrity_digest,
                        assignment_id,
                    ),
                )
            seq = self._outbox[-1].seq + 1 if self._outbox else 1
            chain = _chain(self._last_chain, seq, integrity)
            conn.execute(
                "INSERT INTO outbox(seq, assignment_id, state, generation, body, "
                "integrity_digest, chain_digest) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    seq,
                    assignment_id,
                    state,
                    generation,
                    canonical_json(body).decode("utf-8"),
                    integrity,
                    chain,
                ),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise AssignmentJournalError(f"journal write failed: {exc}") from exc
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        entry = OutboxEntry(
            seq=seq,
            assignment_id=assignment_id,
            state=state,
            generation=generation,
            body=body,
            integrity_digest=integrity,
            chain_digest=chain,
        )
        self._records[assignment_id] = record
        self._outbox.append(entry)
        self._last_chain = chain
        return Transition(record, entry, False)

    def assign(
        self,
        *,
        assignment_id: str,
        task_id: str,
        worker_id: str,
        generation: int,
        at: int,
        grant_digest: str | None = None,
    ) -> Transition:
        """Create an ``ASSIGNED`` record, or replay an identical prior assign."""

        return self._commit(
            _event_body(
                assignment_id=assignment_id,
                task_id=task_id,
                worker_id=worker_id,
                generation=generation,
                state="ASSIGNED",
                at=at,
                grant_digest=grant_digest,
                result_digest=None,
            )
        )

    def transition(
        self,
        *,
        assignment_id: str,
        generation: int,
        state: str,
        at: int,
        result_digest: str | None = None,
        grant_digest: str | None = None,
    ) -> Transition:
        """Advance one assignment, failing closed on stale generation or bad edge."""

        record = self.get(assignment_id)
        return self._commit(
            _event_body(
                assignment_id=assignment_id,
                task_id=record.task_id,
                worker_id=record.worker_id,
                generation=generation,
                state=state,
                at=at,
                grant_digest=grant_digest,
                result_digest=result_digest,
            )
        )

    def get(self, assignment_id: str) -> AssignmentRecord:
        self._db()
        record = self._records.get(_validate_id(assignment_id, "assignment_id"))
        if record is None:
            raise AssignmentJournalError("assignment not found")
        return record

    def outbox(self, *, after: int = 0) -> tuple[OutboxEntry, ...]:
        """Outbox rows in commit order, optionally after an acknowledged sequence."""

        self._db()
        validate_u64(after, "after")
        return tuple(entry for entry in self._outbox if entry.seq > after)

    def head(self) -> str:
        """Current outbox chain head, for a later independently held anchor."""

        self._db()
        return self._last_chain

    def verify(self) -> None:
        """Re-read and re-verify rows against the database, detecting post-open edits."""

        self._load()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.close()
