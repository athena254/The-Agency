"""Per-peer durable, append-only, hash-chained decision log (§16).

Each peer owns one log in its own store; there is no shared network database.
Every record pins ``prev_record_digest``, the membership epoch, and the
membership root. Appends run in ``BEGIN IMMEDIATE`` transactions with
``synchronous=FULL`` so a committed record survives a crash.

Honest limitation (§16): this chain detects edits, gaps, truncation, and
reordering by anyone **other than the store's owner**. A peer that controls its
own store can recompute every digest from an edited point forward, and the local
chain will verify. Tamper-evidence against the owner requires independently held
signed chain heads (anchoring), which is a later slice and is **not** claimed
here. :func:`chain_head_record` produces the exact object such a slice would
sign; nothing in this module signs or anchors it.

The verify/derive functions are pure: they take records or rows and return a
result, with no store access.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Self

from agency.peers.canonical import (
    SchemaError,
    canonical_json,
    digest_hex,
    digest_json,
    validate_closed,
    validate_digest,
    validate_enum,
    validate_integer,
    validate_mapping,
    validate_peer_id,
    validate_timestamp,
    validate_u64,
)

__all__ = [
    "LOG_SCHEMA_VERSION",
    "RECORD_FIELDS",
    "RECORD_KINDS",
    "ZERO_DIGEST",
    "ChainVerification",
    "LogIntegrityError",
    "LogRecord",
    "LogScan",
    "PeerLog",
    "build_record",
    "chain_head_record",
    "derive_record_digest",
    "genesis_record",
    "now_ms",
    "scan_records",
    "verify_chain",
]

LOG_SCHEMA_VERSION: Final = 1
ZERO_DIGEST: Final = "0" * 64

RECORD_KINDS: Final = frozenset(
    {
        "genesis",
        "proposal",
        "ballot",
        "attestation",
        "derivation",
        "certificate",
        "grant",
        "intent",
        "outcome",
        "reconciliation",
        "executed_grant",
        "recovery",
        # §18 step 3: read-only observations of legacy (unsigned, unverified)
        # artifacts. A mirror record is never a protocol transition, carries no
        # authority, and is written only by the mirror shim.
        "mirror",
    }
)

_RECORD_PROJECTION_FIELDS: Final = (
    "seq",
    "prev",
    "kind",
    "membership_epoch",
    "membership_root",
    "payload_digest",
    "envelope_digest",
    "raw_digest",
    "ts",
)
RECORD_FIELDS: Final = frozenset(_RECORD_PROJECTION_FIELDS) | {"digest"}

_TABLE: Final = "peer_log"
_META_TABLE: Final = "peer_log_meta"
_MAX_RECORDS_READ: Final = 100_000


class LogIntegrityError(RuntimeError):
    """The durable log is missing, torn, or inconsistent with its pinned genesis."""


@dataclass(frozen=True, slots=True)
class LogRecord:
    """One immutable log record.

    ``payload`` is bound through ``payload_digest``; ``raw`` (the exact signed
    wire bytes, when retained) is bound through ``raw_digest``. Neither is part
    of the digest projection directly, so the digest stays stable regardless of
    how the payload is serialized on disk.
    """

    seq: int
    prev: str
    kind: str
    membership_epoch: int
    membership_root: str
    payload_digest: str
    envelope_digest: str | None
    raw_digest: str | None
    ts: int
    digest: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        validate_integer(self.seq, 0, (1 << 64) - 1, "seq")
        validate_digest(self.prev, "prev")
        validate_enum(self.kind, RECORD_KINDS, "kind")
        validate_u64(self.membership_epoch, "membership_epoch")
        validate_digest(self.membership_root, "membership_root")
        validate_digest(self.payload_digest, "payload_digest")
        if self.envelope_digest is not None:
            validate_digest(self.envelope_digest, "envelope_digest")
        if self.raw_digest is not None:
            validate_digest(self.raw_digest, "raw_digest")
        validate_timestamp(self.ts, "ts")
        validate_digest(self.digest, "digest")
        validate_mapping(self.payload, "payload")
        expected = derive_record_digest(self.to_projection())
        if self.digest != expected:
            raise SchemaError("record digest does not match its contents")
        if digest_json(dict(self.payload)) != self.payload_digest:
            raise SchemaError("payload digest does not match its payload")

    def to_projection(self) -> dict[str, Any]:
        """Canonical projection hashed into ``digest``."""

        return {
            "seq": self.seq,
            "prev": self.prev,
            "kind": self.kind,
            "membership_epoch": self.membership_epoch,
            "membership_root": self.membership_root,
            "payload_digest": self.payload_digest,
            "envelope_digest": self.envelope_digest,
            "raw_digest": self.raw_digest,
            "ts": self.ts,
        }

    def to_record(self) -> dict[str, Any]:
        """Full record including ``digest``, suitable for serialization."""

        return {**self.to_projection(), "digest": self.digest}

    def to_storage_row(self) -> dict[str, Any]:
        """Storage form with canonical payload text."""

        return {
            "seq": self.seq,
            "prev": self.prev,
            "kind": self.kind,
            "membership_epoch": self.membership_epoch,
            "membership_root": self.membership_root,
            "payload_digest": self.payload_digest,
            "envelope_digest": self.envelope_digest,
            "raw_digest": self.raw_digest,
            "ts": self.ts,
            "digest": self.digest,
            "payload": canonical_json(dict(self.payload)).decode("utf-8"),
        }


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """Result of replaying a chain. ``ok`` is not an authority decision."""

    ok: bool
    records_checked: int
    head_seq: int | None
    head_digest: str | None
    first_bad_seq: int | None
    reason: str | None
    torn_tail: bool

    @property
    def healthy(self) -> bool:
        return self.ok


@dataclass(frozen=True, slots=True)
class LogScan:
    """Result of scanning stored rows: the chain view plus the torn tail."""

    verification: ChainVerification
    records: tuple[LogRecord, ...]
    row_count: int
    torn_record: LogRecord | None = None
    torn_row: Mapping[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.verification.ok

    @property
    def last_good_seq(self) -> int | None:
        return self.verification.head_seq


def now_ms() -> int:
    """Current Unix milliseconds. Local clock; never ordering authority."""

    return time.time_ns() // 1_000_000


def derive_record_digest(projection: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical record projection."""

    validate_closed(projection, RECORD_FIELDS - {"digest"}, "record projection")
    return digest_json(dict(projection))


def build_record(
    *,
    seq: int,
    prev: str,
    kind: str,
    membership_epoch: int,
    membership_root: str,
    payload: Mapping[str, Any],
    envelope_digest: str | None = None,
    raw: bytes | None = None,
    ts: int | None = None,
) -> LogRecord:
    """Derive a record and its digest. Pure; writes nothing."""

    validate_mapping(payload, "payload")
    projection: dict[str, Any] = {
        "seq": validate_integer(seq, 0, (1 << 64) - 1, "seq"),
        "prev": validate_digest(prev, "prev"),
        "kind": validate_enum(kind, RECORD_KINDS, "kind"),
        "membership_epoch": validate_u64(membership_epoch, "membership_epoch"),
        "membership_root": validate_digest(membership_root, "membership_root"),
        "payload_digest": digest_json(dict(payload)),
        "envelope_digest": (
            validate_digest(envelope_digest, "envelope_digest")
            if envelope_digest is not None
            else None
        ),
        "raw_digest": digest_hex(raw) if raw is not None else None,
        "ts": validate_timestamp(now_ms() if ts is None else ts, "ts"),
    }
    validate_closed(projection, RECORD_FIELDS - {"digest"}, "record projection")
    return LogRecord(
        seq=projection["seq"],
        prev=projection["prev"],
        kind=projection["kind"],
        membership_epoch=projection["membership_epoch"],
        membership_root=projection["membership_root"],
        payload_digest=projection["payload_digest"],
        envelope_digest=projection["envelope_digest"],
        raw_digest=projection["raw_digest"],
        ts=projection["ts"],
        digest=derive_record_digest(projection),
        payload=dict(payload),
    )


def genesis_record(
    *, membership_epoch: int, membership_root: str, policy_version: str, ts: int | None = None
) -> LogRecord:
    """The epoch-1 record that pins the genesis membership root."""

    payload = {
        "epoch": validate_u64(membership_epoch, "membership_epoch"),
        "membership_root": validate_digest(membership_root, "membership_root"),
        "policy_version": policy_version,
    }
    return build_record(
        seq=0,
        prev=ZERO_DIGEST,
        kind="genesis",
        membership_epoch=membership_epoch,
        membership_root=membership_root,
        payload=payload,
        ts=ts,
    )


def verify_chain(records: Sequence[LogRecord]) -> ChainVerification:
    """Replay a chain and detect gaps, edits, truncation, and reordering.

    Pure. A record that fails any check makes the chain unhealthy from that
    point; records after it are not treated as trusted.
    """

    if not records:
        return ChainVerification(
            ok=False,
            records_checked=0,
            head_seq=None,
            head_digest=None,
            first_bad_seq=None,
            reason="empty log",
            torn_tail=False,
        )
    previous_digest = ZERO_DIGEST
    expected_seq = 0
    for index, record in enumerate(records):
        is_last = index == len(records) - 1
        problem: str | None = None
        is_gap = False
        if record.seq != expected_seq:
            problem = f"sequence gap at seq {record.seq}: expected {expected_seq}"
            is_gap = True
        elif record.prev != previous_digest:
            problem = f"prev_record_digest mismatch at seq {record.seq}"
        elif record.digest != derive_record_digest(record.to_projection()):
            problem = f"record digest mismatch at seq {record.seq}"
        elif digest_json(dict(record.payload)) != record.payload_digest:
            problem = f"payload digest mismatch at seq {record.seq}"
        elif index == 0 and (
            record.kind != "genesis"
            or record.seq != 0
            or record.prev != ZERO_DIGEST
            or record.payload.get("membership_root") != record.membership_root
        ):
            problem = "genesis record does not pin the membership root"
        if problem is not None:
            return ChainVerification(
                ok=False,
                records_checked=index,
                head_seq=records[index - 1].seq if index else None,
                head_digest=records[index - 1].digest if index else None,
                first_bad_seq=record.seq,
                reason=problem,
                # A gap is missing history, not a partially written record.
                # Only an inconsistent last record is reported as a torn tail.
                torn_tail=is_last and not is_gap,
            )
        previous_digest = record.digest
        expected_seq = record.seq + 1
    last = records[-1]
    return ChainVerification(
        ok=True,
        records_checked=len(records),
        head_seq=last.seq,
        head_digest=last.digest,
        first_bad_seq=None,
        reason=None,
        torn_tail=False,
    )


def scan_records(rows: Sequence[Mapping[str, Any]]) -> LogScan:
    """Decode stored rows and verify the chain they form.

    Rows are ``peer_log`` table mappings. An undecodable row is a torn tail
    when it is the last row, and a broken chain otherwise.
    """

    records: list[LogRecord] = []
    for index, row in enumerate(rows):
        try:
            payload = json.loads(row["payload"])
            record = LogRecord(
                seq=int(row["seq"]),
                prev=str(row["prev"]),
                kind=str(row["kind"]),
                membership_epoch=int(row["membership_epoch"]),
                membership_root=str(row["membership_root"]),
                payload_digest=str(row["payload_digest"]),
                envelope_digest=(
                    str(row["envelope_digest"]) if row["envelope_digest"] is not None else None
                ),
                raw_digest=str(row["raw_digest"]) if row["raw_digest"] is not None else None,
                ts=int(row["ts"]),
                digest=str(row["digest"]),
                payload=payload,
            )
            raw = row.get("raw")
            if raw is not None and record.raw_digest != digest_hex(bytes(raw)):
                raise SchemaError("raw bytes do not match raw_digest")
        except (KeyError, TypeError, ValueError, SchemaError) as exc:
            is_last = index == len(rows) - 1
            verification = ChainVerification(
                ok=False,
                records_checked=len(records),
                head_seq=records[-1].seq if records else None,
                head_digest=records[-1].digest if records else None,
                first_bad_seq=_safe_int(row.get("seq")),
                reason=f"undecodable record at row {index}: {exc}",
                torn_tail=is_last,
            )
            return LogScan(
                verification=verification,
                records=tuple(records),
                row_count=len(rows),
                torn_row=row,
            )
        records.append(record)
    verification = verify_chain(records)
    torn = records[-1] if not verification.ok and verification.torn_tail and records else None
    return LogScan(
        verification=verification,
        records=tuple(records),
        row_count=len(rows),
        torn_record=torn,
    )


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def chain_head_record(peer_id: str, head: LogRecord) -> dict[str, Any]:
    """The object a later slice would sign and anchor (§16).

    Unsigned and unanchored here. It carries no authority and proves nothing on
    its own; it exists so the anchoring slice has one frozen shape to sign.
    """

    validate_peer_id(peer_id, "peer_id")
    return {
        "peer_id": peer_id,
        "last_seq": head.seq,
        "last_record_digest": head.digest,
        "membership_root": head.membership_root,
        "ts": head.ts,
    }


_SCHEMA_STATEMENTS: Final = (
    f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
        seq INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        digest TEXT NOT NULL,
        prev TEXT NOT NULL,
        membership_epoch INTEGER NOT NULL,
        membership_root TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        envelope_digest TEXT,
        raw_digest TEXT,
        ts INTEGER NOT NULL,
        payload TEXT NOT NULL,
        raw BLOB
    )""",
    f"""CREATE TABLE IF NOT EXISTS {_META_TABLE} (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_kind ON {_TABLE}(kind)",
    f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_prev ON {_TABLE}(prev)",
)


class PeerLog:
    """Append-only per-peer durable log backed by one SQLite database.

    Use :meth:`open` to create or reopen a store, :meth:`append` to add a
    record, :meth:`recover` to scan it, and :meth:`verify` to replay the chain.
    A log that fails its recovery scan is marked unhealthy and refuses further
    appends until an operator resolves it; it never silently truncates or
    rewrites history.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        peer_id: str,
        genesis: Mapping[str, Any] | None = None,
        expected_genesis_root: str | None = None,
    ) -> None:
        validate_peer_id(peer_id, "peer_id")
        self._path = Path(path)
        self._peer_id = peer_id
        self._genesis = dict(genesis) if genesis is not None else None
        self._expected_genesis_root = (
            validate_digest(expected_genesis_root, "expected_genesis_root")
            if expected_genesis_root is not None
            else None
        )
        self._conn: sqlite3.Connection | None = None
        self._healthy = True

    # -- lifecycle ---------------------------------------------------- #

    @property
    def path(self) -> Path:
        return self._path

    @property
    def peer_id(self) -> str:
        return self._peer_id

    @property
    def is_healthy(self) -> bool:
        return self._healthy

    @property
    def durability(self) -> str:
        """The SQLite synchronous mode in force. ``FULL`` fsyncs each commit."""

        self._require_open()
        assert self._conn is not None
        return {0: "OFF", 1: "NORMAL", 2: "FULL"}.get(
            self._query_synchronous(self._conn), "UNKNOWN"
        )

    def _check_genesis_input(self) -> None:
        """Fail before touching the store when genesis and pinned root disagree."""

        if self._genesis is None or self._expected_genesis_root is None:
            return
        payload_root = self._genesis.get("membership_root")
        if payload_root != self._expected_genesis_root:
            raise LogIntegrityError(
                "genesis payload membership root does not match the pinned root"
            )

    @staticmethod
    def _query_synchronous(conn: sqlite3.Connection) -> int:
        row = conn.execute("PRAGMA synchronous").fetchone()
        return int(row[0]) if row is not None else 0

    def open(self) -> None:
        """Open the store, enforce durability pragmas, and recover the chain."""

        if self._conn is not None:
            return
        self._check_genesis_input()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            if self._query_synchronous(conn) != 2:  # pragma: no cover - pragma is reliable
                raise LogIntegrityError("SQLite did not enable synchronous=FULL")
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
            self._conn = conn
            self._initialize_meta()
            self._initialize_genesis()
        except BaseException:
            conn.close()
            self._conn = None
            raise
        self.recover()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _require_open(self) -> None:
        if self._conn is None:
            raise LogIntegrityError("PeerLog is not open")

    def _initialize_meta(self) -> None:
        assert self._conn is not None
        row = self._conn.execute(
            f"SELECT value FROM {_META_TABLE} WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self._conn.execute(
                f"INSERT INTO {_META_TABLE} (key, value) VALUES ('schema_version', ?)",
                (str(LOG_SCHEMA_VERSION),),
            )
            self._conn.execute(
                f"INSERT INTO {_META_TABLE} (key, value) VALUES ('peer_id', ?)",
                (self._peer_id,),
            )
            if self._expected_genesis_root is not None:
                self._conn.execute(
                    f"INSERT INTO {_META_TABLE} (key, value) VALUES ('genesis_root', ?)",
                    (self._expected_genesis_root,),
                )
            return
        if int(row["value"]) != LOG_SCHEMA_VERSION:
            raise LogIntegrityError("peer log schema version mismatch")
        stored_peer = self._conn.execute(
            f"SELECT value FROM {_META_TABLE} WHERE key = 'peer_id'"
        ).fetchone()
        if stored_peer is not None and stored_peer["value"] != self._peer_id:
            raise LogIntegrityError("peer log belongs to a different peer")
        stored_root = self._conn.execute(
            f"SELECT value FROM {_META_TABLE} WHERE key = 'genesis_root'"
        ).fetchone()
        if self._expected_genesis_root is not None and (
            stored_root is None or stored_root["value"] != self._expected_genesis_root
        ):
            raise LogIntegrityError("peer log genesis root does not match the pinned root")

    def _initialize_genesis(self) -> None:
        assert self._conn is not None
        count = int(self._conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0])
        if count:
            return
        if self._genesis is None:
            raise LogIntegrityError("a new peer log requires a genesis record")
        payload = dict(self._genesis)
        try:
            membership_epoch = int(payload["epoch"])
            membership_root = validate_digest(str(payload["membership_root"]), "membership_root")
            policy_version = str(payload["policy_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LogIntegrityError("invalid genesis payload") from exc
        if (
            self._expected_genesis_root is not None
            and membership_root != self._expected_genesis_root
        ):
            raise LogIntegrityError(
                "genesis payload membership root does not match the pinned root"
            )
        record = genesis_record(
            membership_epoch=membership_epoch,
            membership_root=membership_root,
            policy_version=policy_version,
        )
        self._insert(record, raw=None)
        self._conn.execute(
            f"INSERT OR REPLACE INTO {_META_TABLE} (key, value) VALUES ('genesis_root', ?)",
            (membership_root,),
        )

    # -- appends ------------------------------------------------------ #

    def append(
        self,
        kind: str,
        payload: Mapping[str, Any],
        *,
        membership_epoch: int,
        membership_root: str,
        envelope_digest: str | None = None,
        raw: bytes | None = None,
        ts: int | None = None,
    ) -> LogRecord:
        """Derive and durably append one record.

        The append is a single ``BEGIN IMMEDIATE`` transaction with
        ``synchronous=FULL``; the record is not returned until the commit's
        fsync has succeeded. An unhealthy log refuses to append.
        """

        self._require_open()
        if not self._healthy:
            raise LogIntegrityError("peer log is unhealthy; resolve the torn tail first")
        assert self._conn is not None
        head = self._head_record()
        record = build_record(
            seq=0 if head is None else head.seq + 1,
            prev=ZERO_DIGEST if head is None else head.digest,
            kind=kind,
            membership_epoch=membership_epoch,
            membership_root=membership_root,
            payload=payload,
            envelope_digest=envelope_digest,
            raw=raw,
            ts=ts,
        )
        self._insert(record, raw=raw)
        return record

    def _insert(self, record: LogRecord, *, raw: bytes | None) -> None:
        assert self._conn is not None
        row = record.to_storage_row()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                f"""INSERT INTO {_TABLE}
                    (seq, kind, digest, prev, membership_epoch, membership_root,
                     payload_digest, envelope_digest, raw_digest, ts, payload, raw)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["seq"],
                    row["kind"],
                    row["digest"],
                    row["prev"],
                    row["membership_epoch"],
                    row["membership_root"],
                    row["payload_digest"],
                    row["envelope_digest"],
                    row["raw_digest"],
                    row["ts"],
                    row["payload"],
                    raw,
                ),
            )
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    # -- reads -------------------------------------------------------- #

    def _head_record(self) -> LogRecord | None:
        rows = self._read_rows(limit=1, desc=True)
        return rows[0] if rows else None

    def _read_rows(self, *, limit: int | None = None, desc: bool = False) -> list[LogRecord]:
        assert self._conn is not None
        order = "DESC" if desc else "ASC"
        query = f"SELECT * FROM {_TABLE} ORDER BY seq {order}"
        params: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (min(limit, _MAX_RECORDS_READ),)
        cursor = self._conn.execute(query, params)
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def _row_to_record(self, row: sqlite3.Row) -> LogRecord:
        payload = json.loads(row["payload"])
        return LogRecord(
            seq=int(row["seq"]),
            prev=str(row["prev"]),
            kind=str(row["kind"]),
            membership_epoch=int(row["membership_epoch"]),
            membership_root=str(row["membership_root"]),
            payload_digest=str(row["payload_digest"]),
            envelope_digest=(
                str(row["envelope_digest"]) if row["envelope_digest"] is not None else None
            ),
            raw_digest=str(row["raw_digest"]) if row["raw_digest"] is not None else None,
            ts=int(row["ts"]),
            digest=str(row["digest"]),
            payload=payload,
        )

    def records(self, *, limit: int | None = None) -> list[LogRecord]:
        self._require_open()
        return self._read_rows(limit=limit)

    def head(self) -> LogRecord | None:
        self._require_open()
        return self._head_record()

    def chain_head(self) -> dict[str, Any] | None:
        """The unsigned head object a later anchoring slice would sign."""

        head = self.head()
        if head is None:
            return None
        return chain_head_record(self._peer_id, head)

    def verify(self) -> ChainVerification:
        """Replay the stored chain. Pure verification over stored rows.

        Corruption is reported, not raised: an undecodable or mismatched row
        makes the result unhealthy instead of propagating a schema error.
        """

        self._require_open()
        return scan_records(self._raw_rows()).verification

    def _raw_rows(self) -> list[dict[str, Any]]:
        assert self._conn is not None
        cursor = self._conn.execute(f"SELECT * FROM {_TABLE} ORDER BY seq ASC")
        rows: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            entry = dict(row)
            raw = entry.get("raw")
            if raw is not None:
                entry["raw"] = bytes(raw)
            rows.append(entry)
        return rows

    def recover(self) -> LogScan:
        """Explicit startup scan: decode rows, verify the chain, report health.

        A torn tail is retained, not removed. The log is marked unhealthy so
        further appends fail closed until an operator resolves it.
        """

        self._require_open()
        scan = scan_records(self._raw_rows())
        self._healthy = scan.ok
        return scan

    def flush(self) -> None:
        """Force a WAL checkpoint. Committed records are already fsynced."""

        self._require_open()
        assert self._conn is not None
        self._conn.execute("PRAGMA wal_checkpoint(FULL)")

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
