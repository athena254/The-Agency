"""Durable SQLite beta quota ledger (Telegram gateway quota only).

Never peer governance. Stores only routing IDs (bot_id / update_id / user_id),
epoch timestamps and reservation state. No prompts, queries, replies, tokens
or API keys are persisted here.

Durability: per-operation connections, ``BEGIN IMMEDIATE`` with busy timeout,
WAL journal mode and ``synchronous=FULL``. Blocking sqlite work is offloaded
with :func:`asyncio.to_thread` so the async API never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import math
import os
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import ClassVar

_HOUR_SECONDS = 3600.0
_DAY_SECONDS = 86400.0
_RETENTION_SECONDS = 172800.0  # 48h Telegram duplicate retention cover
_BUSY_TIMEOUT_MS = 5000
_CONNECT_TIMEOUT_S = 5.0
_MAX_BOT_ID_LEN = 128

# Fixed caller-facing message: never interpolates the DB path or sqlite text.
_UNAVAILABLE = "beta budget unavailable; request denied"

_VALID_STATES = frozenset({"RESERVED", "RUNNING", "COMPLETED", "FAILED"})


class BudgetUnavailable(Exception):
    """Infra failure (DB or clock). Callers must deny fail-closed."""


@dataclass(frozen=True)
class BudgetLimits:
    """Strict positive-int quota caps. Overrides may only lower ceilings."""

    requests_per_hour: int = 5
    requests_per_day: int = 20
    model_calls_per_hour: int = 40
    model_calls_per_day: int = 160
    model_calls_per_request: int = 8

    CEILINGS: ClassVar[dict[str, int]] = {
        "requests_per_hour": 5,
        "requests_per_day": 20,
        "model_calls_per_hour": 40,
        "model_calls_per_day": 160,
        "model_calls_per_request": 8,
    }

    def __post_init__(self) -> None:
        for field, ceiling in BudgetLimits.CEILINGS.items():
            value = getattr(self, field)
            if type(value) is not int:
                raise TypeError(f"{field} must be an int, got {type(value).__name__}")
            if value <= 0:
                raise ValueError(f"{field} must be positive, got {value}")
            if value > ceiling:
                raise ValueError(f"{field} exceeds hard ceiling {ceiling}, got {value}")


@dataclass(frozen=True)
class Reservation:
    """Outcome of a quota reservation attempt. ``allowed`` only for new keys."""

    allowed: bool
    duplicate: bool
    state: str
    capability: str | None = dataclass_field(default=None, repr=False, compare=False)


def _validate_bot_id(bot_id: str) -> str:
    if type(bot_id) is not str:
        raise TypeError(f"bot_id must be a str, got {type(bot_id).__name__}")
    if not bot_id.strip() or len(bot_id) > _MAX_BOT_ID_LEN:
        raise ValueError("bot_id must be a nonempty local identifier")
    if ":" in bot_id and len(bot_id) >= 20:
        head, _, tail = bot_id.partition(":")
        if head.isdigit() and len(tail) >= 20:
            raise ValueError("bot_id must be a stable local identifier, not a token")
    return bot_id


def _validate_update_id(update_id: int) -> int:
    if type(update_id) is not int:
        raise TypeError(f"update_id must be an int, got {type(update_id).__name__}")
    if update_id < 0:
        raise ValueError(f"update_id must be nonnegative, got {update_id}")
    return update_id


def _validate_user_id(user_id: int) -> int:
    if type(user_id) is not int:
        raise TypeError(f"user_id must be an int, got {type(user_id).__name__}")
    if user_id <= 0:
        raise ValueError(f"user_id must be positive, got {user_id}")
    return user_id


class BetaBudgetStore:
    """Gateway-local durable quota ledger keyed by ``(bot_id, update_id)``."""

    def __init__(
        self,
        db_path: str,
        *,
        limits: BudgetLimits | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if type(db_path) is not str:
            raise TypeError(f"db_path must be a str, got {type(db_path).__name__}")
        if not db_path.strip():
            raise ValueError("db_path must be nonempty")
        if db_path.strip() == ":memory:":
            raise ValueError("db_path must be a persistent file path")
        if limits is not None and not isinstance(limits, BudgetLimits):
            raise TypeError("limits must be a BudgetLimits or None")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be a callable returning epoch seconds")
        self._db_path = db_path
        self._limits = limits if limits is not None else BudgetLimits()
        self._clock: Callable[[], float] = clock if clock is not None else time.time
        self._closed = False
        # Process-local execution ownership. Never persisted or included in repr/logs.
        self._owners: dict[tuple[str, int, int], str] = {}

    def _owns(self, bot_id: str, update_id: int, user_id: int, capability: str | None) -> bool:
        if self._closed or not isinstance(capability, str) or not capability:
            return False
        expected = self._owners.get((bot_id, update_id, user_id))
        return expected is not None and secrets.compare_digest(expected, capability)

    # -- internal helpers (sync, run in threads) --

    def _read_clock(self) -> float:
        try:
            now = self._clock()
        except Exception as exc:
            raise BudgetUnavailable(_UNAVAILABLE) from exc
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise BudgetUnavailable(_UNAVAILABLE)
        moment = float(now)
        if not math.isfinite(moment) or moment < 0:
            raise BudgetUnavailable(_UNAVAILABLE)
        return moment

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(
                self._db_path,
                timeout=_CONNECT_TIMEOUT_S,
                isolation_level=None,
                check_same_thread=False,
            )
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            return conn
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise BudgetUnavailable(_UNAVAILABLE) from exc

    def _init_sync(self) -> None:
        if self._closed:
            raise BudgetUnavailable(_UNAVAILABLE)
        try:
            parent = os.path.dirname(os.path.abspath(self._db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            raise BudgetUnavailable(_UNAVAILABLE) from exc
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect()
            conn.execute(
                "CREATE TABLE IF NOT EXISTS requests("
                "bot_id TEXT NOT NULL, update_id INTEGER NOT NULL, "
                "user_id INTEGER NOT NULL, created_at REAL NOT NULL, "
                "state TEXT NOT NULL, PRIMARY KEY(bot_id, update_id))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS model_attempts("
                "bot_id TEXT NOT NULL, update_id INTEGER NOT NULL, "
                "user_id INTEGER NOT NULL, attempt_index INTEGER NOT NULL, "
                "created_at REAL NOT NULL, "
                "PRIMARY KEY(bot_id, update_id, attempt_index))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS quota_clock("
                "bot_id TEXT PRIMARY KEY, last_seen REAL NOT NULL)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_user_time "
                "ON requests(bot_id, user_id, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_user_time "
                "ON model_attempts(bot_id, user_id, created_at)"
            )
        except (sqlite3.Error, OSError) as exc:
            raise BudgetUnavailable(_UNAVAILABLE) from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass

    @staticmethod
    def _advance_clock_txn(conn: sqlite3.Connection, bot_id: str, now: float) -> None:
        row = conn.execute(
            "SELECT last_seen FROM quota_clock WHERE bot_id = ?", (bot_id,)
        ).fetchone()
        if row is not None and now < float(row[0]):
            raise BudgetUnavailable(_UNAVAILABLE)
        conn.execute(
            "INSERT OR REPLACE INTO quota_clock(bot_id, last_seen) VALUES (?, ?)",
            (bot_id, now),
        )

    @staticmethod
    def _prune_txn(conn: sqlite3.Connection, now: float) -> None:
        cutoff = now - _RETENTION_SECONDS
        conn.execute(
            "DELETE FROM model_attempts WHERE created_at < ? AND ("
            "NOT EXISTS (SELECT 1 FROM requests r WHERE r.bot_id = model_attempts.bot_id "
            "AND r.update_id = model_attempts.update_id) OR EXISTS ("
            "SELECT 1 FROM requests r WHERE r.bot_id = model_attempts.bot_id "
            "AND r.update_id = model_attempts.update_id "
            "AND r.state IN ('COMPLETED', 'FAILED')))",
            (cutoff,),
        )
        conn.execute(
            "DELETE FROM requests WHERE created_at < ? AND state IN ('COMPLETED', 'FAILED') "
            "AND NOT EXISTS (SELECT 1 FROM model_attempts m WHERE m.bot_id = requests.bot_id "
            "AND m.update_id = requests.update_id)",
            (cutoff,),
        )

    def _reserve_request_sync(self, bot_id: str, update_id: int, user_id: int) -> Reservation:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = self._read_clock()
                self._advance_clock_txn(conn, bot_id, now)
                self._prune_txn(conn, now)
                existing = conn.execute(
                    "SELECT state, user_id FROM requests WHERE bot_id = ? AND update_id = ?",
                    (bot_id, update_id),
                ).fetchone()
                if existing is not None:
                    if int(existing[1]) != user_id:
                        conn.execute("COMMIT")
                        return Reservation(allowed=False, duplicate=False, state="DENIED")
                    conn.execute("COMMIT")
                    return Reservation(allowed=False, duplicate=True, state=str(existing[0]))
                hour = conn.execute(
                    "SELECT COUNT(*) FROM requests WHERE bot_id = ? AND user_id = ? "
                    "AND created_at > ?",
                    (bot_id, user_id, now - _HOUR_SECONDS),
                ).fetchone()[0]
                day = conn.execute(
                    "SELECT COUNT(*) FROM requests WHERE bot_id = ? AND user_id = ? "
                    "AND created_at > ?",
                    (bot_id, user_id, now - _DAY_SECONDS),
                ).fetchone()[0]
                if (
                    int(hour) >= self._limits.requests_per_hour
                    or int(day) >= self._limits.requests_per_day
                ):
                    conn.execute("COMMIT")
                    return Reservation(allowed=False, duplicate=False, state="DENIED")
                conn.execute(
                    "INSERT INTO requests(bot_id, update_id, user_id, created_at, state) "
                    "VALUES (?, ?, ?, ?, 'RESERVED')",
                    (bot_id, update_id, user_id, now),
                )
                conn.execute("COMMIT")
                return Reservation(allowed=True, duplicate=False, state="RESERVED")
            except (KeyError, ValueError, TypeError, BudgetUnavailable):
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            except (sqlite3.Error, OSError) as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise BudgetUnavailable(_UNAVAILABLE) from exc
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def _mark_running_sync(self, bot_id: str, update_id: int, user_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = self._read_clock()
                self._advance_clock_txn(conn, bot_id, now)
                row = conn.execute(
                    "SELECT state, user_id FROM requests WHERE bot_id = ? AND update_id = ?",
                    (bot_id, update_id),
                ).fetchone()
                if row is None:
                    raise KeyError("unknown reservation")
                if int(row[1]) != user_id:
                    raise ValueError("user mismatch")
                if str(row[0]) != "RESERVED":
                    raise ValueError(f"invalid state {row[0]}; expected RESERVED")
                conn.execute(
                    "UPDATE requests SET state = 'RUNNING' WHERE bot_id = ? AND update_id = ?",
                    (bot_id, update_id),
                )
                conn.execute("COMMIT")
            except (KeyError, ValueError, TypeError, BudgetUnavailable):
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            except (sqlite3.Error, OSError) as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise BudgetUnavailable(_UNAVAILABLE) from exc
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def _reserve_model_call_sync(self, bot_id: str, update_id: int, user_id: int) -> bool:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = self._read_clock()
                self._advance_clock_txn(conn, bot_id, now)
                self._prune_txn(conn, now)
                row = conn.execute(
                    "SELECT state, user_id FROM requests WHERE bot_id = ? AND update_id = ?",
                    (bot_id, update_id),
                ).fetchone()
                if row is None or int(row[1]) != user_id or str(row[0]) != "RUNNING":
                    conn.execute("COMMIT")
                    return False
                per_request = conn.execute(
                    "SELECT COUNT(*) FROM model_attempts WHERE bot_id = ? AND update_id = ?",
                    (bot_id, update_id),
                ).fetchone()[0]
                hour = conn.execute(
                    "SELECT COUNT(*) FROM model_attempts "
                    "WHERE bot_id = ? AND user_id = ? AND created_at > ?",
                    (bot_id, user_id, now - _HOUR_SECONDS),
                ).fetchone()[0]
                day = conn.execute(
                    "SELECT COUNT(*) FROM model_attempts "
                    "WHERE bot_id = ? AND user_id = ? AND created_at > ?",
                    (bot_id, user_id, now - _DAY_SECONDS),
                ).fetchone()[0]
                if (
                    int(per_request) >= self._limits.model_calls_per_request
                    or int(hour) >= self._limits.model_calls_per_hour
                    or int(day) >= self._limits.model_calls_per_day
                ):
                    conn.execute("COMMIT")
                    return False
                peak = conn.execute(
                    "SELECT COALESCE(MAX(attempt_index), -1) FROM model_attempts "
                    "WHERE bot_id = ? AND update_id = ?",
                    (bot_id, update_id),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO model_attempts("
                    "bot_id, update_id, user_id, attempt_index, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (bot_id, update_id, user_id, int(peak) + 1, now),
                )
                conn.execute("COMMIT")
                return True
            except (KeyError, ValueError, TypeError, BudgetUnavailable):
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            except (sqlite3.Error, OSError) as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise BudgetUnavailable(_UNAVAILABLE) from exc
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def _finish_request_sync(
        self, bot_id: str, update_id: int, user_id: int, success: bool
    ) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = self._read_clock()
                self._advance_clock_txn(conn, bot_id, now)
                row = conn.execute(
                    "SELECT state, user_id FROM requests WHERE bot_id = ? AND update_id = ?",
                    (bot_id, update_id),
                ).fetchone()
                if row is None:
                    raise KeyError("unknown reservation")
                if int(row[1]) != user_id:
                    raise ValueError("user mismatch")
                if str(row[0]) != "RUNNING":
                    raise ValueError(f"invalid state {row[0]}; expected RUNNING")
                terminal = "COMPLETED" if success else "FAILED"
                conn.execute(
                    "UPDATE requests SET state = ? WHERE bot_id = ? AND update_id = ?",
                    (terminal, bot_id, update_id),
                )
                conn.execute("COMMIT")
            except (KeyError, ValueError, TypeError, BudgetUnavailable):
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            except (sqlite3.Error, OSError) as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise BudgetUnavailable(_UNAVAILABLE) from exc
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def _inspect_reservation_sync(self, bot_id: str, update_id: int) -> str | None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state FROM requests WHERE bot_id = ? AND update_id = ?",
                (bot_id, update_id),
            ).fetchone()
            conn.execute("COMMIT")
            return str(row[0]) if row is not None else None
        except (sqlite3.Error, OSError) as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise BudgetUnavailable(_UNAVAILABLE) from exc
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def _crash_recovery_sync(self, bot_id: str, update_id: int, user_id: int) -> Reservation:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state, user_id FROM requests WHERE bot_id = ? AND update_id = ?",
                (bot_id, update_id),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return Reservation(allowed=False, duplicate=False, state="UNKNOWN")
            if int(row[1]) != user_id:
                conn.execute("COMMIT")
                return Reservation(allowed=False, duplicate=False, state="DENIED")
            state = str(row[0])
            conn.execute("COMMIT")
            return Reservation(allowed=False, duplicate=True, state=state)
        except (sqlite3.Error, OSError) as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise BudgetUnavailable(_UNAVAILABLE) from exc
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    # -- async API --

    async def inspect_reservation(self, bot_id: str, update_id: int) -> str | None:
        """Read-only inspection of reservation state. Does not modify or prune."""
        _validate_bot_id(bot_id)
        _validate_update_id(update_id)
        if self._closed:
            raise BudgetUnavailable(_UNAVAILABLE)
        return await asyncio.to_thread(self._inspect_reservation_sync, bot_id, update_id)

    async def crash_recovery(self, bot_id: str, update_id: int, user_id: int) -> Reservation:
        """Crash recovery: return duplicate/unknown for existing reservations
        without executing again. Stage1b integration must reconcile RUNNING
        against provider/Telegram before any manual retry; never auto-prune
        or auto-mark another process's live request as crashed."""
        _validate_bot_id(bot_id)
        _validate_update_id(update_id)
        _validate_user_id(user_id)
        if self._closed:
            raise BudgetUnavailable(_UNAVAILABLE)
        return await asyncio.to_thread(self._crash_recovery_sync, bot_id, update_id, user_id)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._init_sync)

    async def reserve_request(self, bot_id: str, update_id: int, user_id: int) -> Reservation:
        _validate_bot_id(bot_id)
        _validate_update_id(update_id)
        _validate_user_id(user_id)
        if self._closed:
            raise BudgetUnavailable(_UNAVAILABLE)
        reservation = await asyncio.to_thread(
            self._reserve_request_sync, bot_id, update_id, user_id
        )
        # A worker may commit after close(); never issue an execution grant then.
        if self._closed:
            raise BudgetUnavailable(_UNAVAILABLE)
        if reservation.state not in _VALID_STATES and reservation.state != "DENIED":
            raise BudgetUnavailable(_UNAVAILABLE)
        if reservation.allowed and not reservation.duplicate and reservation.state == "RESERVED":
            capability = secrets.token_urlsafe(32)
            self._owners[(bot_id, update_id, user_id)] = capability
            return Reservation(True, False, "RESERVED", capability)
        return reservation

    async def mark_running(
        self, bot_id: str, update_id: int, user_id: int, *, capability: str | None = None
    ) -> None:
        _validate_bot_id(bot_id)
        _validate_update_id(update_id)
        _validate_user_id(user_id)
        if not self._owns(bot_id, update_id, user_id, capability):
            raise ValueError("execution ownership denied")
        await asyncio.to_thread(self._mark_running_sync, bot_id, update_id, user_id)

    async def reserve_model_call(
        self, bot_id: str, update_id: int, user_id: int, *, capability: str | None = None
    ) -> bool:
        _validate_bot_id(bot_id)
        _validate_update_id(update_id)
        _validate_user_id(user_id)
        if not self._owns(bot_id, update_id, user_id, capability):
            return False
        reserved = await asyncio.to_thread(
            self._reserve_model_call_sync, bot_id, update_id, user_id
        )
        # close() can revoke ownership while a SQLite worker is in flight.
        # The attempt may be counted, but it must not authorize a provider call.
        return reserved and self._owns(bot_id, update_id, user_id, capability)

    async def finish_request(
        self,
        bot_id: str,
        update_id: int,
        user_id: int,
        *,
        success: bool,
        capability: str | None = None,
    ) -> None:
        _validate_bot_id(bot_id)
        _validate_update_id(update_id)
        _validate_user_id(user_id)
        if type(success) is not bool:
            raise TypeError(f"success must be a bool, got {type(success).__name__}")
        if not self._owns(bot_id, update_id, user_id, capability):
            raise ValueError("execution ownership denied")
        await asyncio.to_thread(self._finish_request_sync, bot_id, update_id, user_id, success)
        self._owners.pop((bot_id, update_id, user_id), None)

    async def close(self) -> None:
        self._closed = True
        self._owners.clear()
