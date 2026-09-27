"""Durable beta budget ledger — hermetic foundation tests (offline, deterministic).

Telegram gateway quota only; never peer governance. All DBs are ``tmp_path``
test files; no live network, no provider calls, no prompt/content storage.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest


class FakeClock:
    """Injectable wall-clock returning epoch seconds."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _make_store(db_path: Path, clock: FakeClock, **over: Any) -> Any:
    from agency.telegram.budget_store import BetaBudgetStore, BudgetLimits

    limits = BudgetLimits(**over) if over else BudgetLimits()
    return BetaBudgetStore(str(db_path), limits=limits, clock=clock)


def _orphan_attempt_count(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM model_attempts m LEFT JOIN requests r "
            "ON m.bot_id = r.bot_id AND m.update_id = r.update_id "
            "WHERE r.update_id IS NULL"
        ).fetchone()[0]
    )


# --- BudgetLimits contracts ---


def test_limits_defaults_are_provisional_ceilings() -> None:
    from agency.telegram.budget_store import BudgetLimits

    lim = BudgetLimits()
    assert lim.requests_per_hour == 5
    assert lim.requests_per_day == 20
    assert lim.model_calls_per_hour == 40
    assert lim.model_calls_per_day == 160
    assert lim.model_calls_per_request == 8


def test_limits_reject_bool_nonint_and_nonpositive() -> None:
    from agency.telegram.budget_store import BudgetLimits

    for field in (
        "requests_per_hour",
        "requests_per_day",
        "model_calls_per_hour",
        "model_calls_per_day",
        "model_calls_per_request",
    ):
        for bad in (True, False, 0, -1, "5", 5.0, None):
            with pytest.raises((TypeError, ValueError)):
                BudgetLimits(**{field: bad})  # type: ignore[arg-type]


def test_limits_allow_lower_but_not_above_ceiling() -> None:
    from agency.telegram.budget_store import BudgetLimits

    lowered = BudgetLimits(requests_per_hour=2, model_calls_per_request=3)
    assert lowered.requests_per_hour == 2
    assert lowered.model_calls_per_request == 3
    with pytest.raises(ValueError):
        BudgetLimits(requests_per_hour=6)
    with pytest.raises(ValueError):
        BudgetLimits(requests_per_day=21)
    with pytest.raises(ValueError):
        BudgetLimits(model_calls_per_hour=41)
    with pytest.raises(ValueError):
        BudgetLimits(model_calls_per_day=161)
    with pytest.raises(ValueError):
        BudgetLimits(model_calls_per_request=9)


# --- strict input / duplicate ---


async def test_reserve_rejects_strict_input(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        for bad_bot in ("", "   ", 123, None, True):
            with pytest.raises((TypeError, ValueError)):
                await store.reserve_request(bad_bot, 1, 101)  # type: ignore[arg-type]
        # token-like bot_id must be rejected (stable local id, never a token)
        with pytest.raises(ValueError):
            await store.reserve_request("123456:ABCdefGHIjklMNOpqrSTUvwxYZ123456789", 1, 101)
        for bad_update in (True, False, -1, "1", 1.5, None):
            with pytest.raises((TypeError, ValueError)):
                await store.reserve_request("bot1", bad_update, 101)  # type: ignore[arg-type]
        for bad_user in (0, -5, True, False, "101", 101.5, None):
            with pytest.raises((TypeError, ValueError)):
                await store.reserve_request("bot1", 1, bad_user)  # type: ignore[arg-type]
    finally:
        await store.close()


async def test_reserve_allows_once_duplicate_never_reauthorizes(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        first = await store.reserve_request("bot1", 10, 101)
        assert first.allowed is True
        assert first.duplicate is False
        assert first.state == "RESERVED"
        assert first.capability is not None
        capability = first.capability
        # redelivery of the same update must not authorize a second execution
        dup = await store.reserve_request("bot1", 10, 101)
        assert dup.allowed is False
        assert dup.duplicate is True
        assert dup.state == "RESERVED"
        assert dup.capability is None
        await store.mark_running("bot1", 10, 101, capability=capability)
        dup2 = await store.reserve_request("bot1", 10, 101)
        assert dup2.allowed is False
        assert dup2.duplicate is True
        assert dup2.state == "RUNNING"
        assert dup2.capability is None
    finally:
        await store.close()


async def test_user_mismatch_on_same_update_denies(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        first = await store.reserve_request("bot1", 11, 101)
        assert first.allowed is True
        assert first.capability is not None
        other = await store.reserve_request("bot1", 11, 202)
        assert other.allowed is False
        assert other.capability is None
        # mismatched user must not be able to advance or finish the reservation,
        # even when presenting the capability issued for the owning user
        with pytest.raises((KeyError, ValueError)):
            await store.mark_running("bot1", 11, 202, capability=first.capability)
        assert await store.reserve_model_call("bot1", 11, 202, capability=first.capability) is False
        with pytest.raises((KeyError, ValueError)):
            await store.finish_request("bot1", 11, 202, success=True, capability=first.capability)
    finally:
        await store.close()


async def test_unknown_reservation_fails_closed(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        with pytest.raises((KeyError, ValueError)):
            await store.mark_running("bot1", 999, 101)
        assert await store.reserve_model_call("bot1", 999, 101) is False
        with pytest.raises((KeyError, ValueError)):
            await store.finish_request("bot1", 999, 101, success=True)
        # a forged capability never turns an unknown reservation into an execution
        with pytest.raises((KeyError, ValueError)):
            await store.mark_running("bot1", 999, 101, capability="forged-capability-value")
        assert (
            await store.reserve_model_call("bot1", 999, 101, capability="forged-capability-value")
            is False
        )
        with pytest.raises((KeyError, ValueError)):
            await store.finish_request(
                "bot1", 999, 101, success=True, capability="forged-capability-value"
            )
    finally:
        await store.close()


# --- rolling quotas + restart ---


async def test_request_hour_quota_rolling_window(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock, requests_per_hour=2, requests_per_day=20)
    await store.initialize()
    try:
        assert (await store.reserve_request("bot1", 1, 101)).allowed is True
        assert (await store.reserve_request("bot1", 2, 101)).allowed is True
        denied = await store.reserve_request("bot1", 3, 101)
        assert denied.allowed is False
        assert denied.duplicate is False
        # other user unaffected
        assert (await store.reserve_request("bot1", 3, 202)).allowed is True
        # rolling expiry: after one hour the oldest slot frees
        clock.advance(3601.0)
        assert (await store.reserve_request("bot1", 4, 101)).allowed is True
    finally:
        await store.close()


async def test_request_quota_counts_failed_attempts(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock, requests_per_hour=2, requests_per_day=20)
    await store.initialize()
    try:
        first = await store.reserve_request("bot1", 1, 101)
        assert first.allowed is True
        await store.mark_running("bot1", 1, 101, capability=first.capability)
        await store.finish_request("bot1", 1, 101, success=False, capability=first.capability)
        second = await store.reserve_request("bot1", 2, 101)
        assert second.allowed is True
        await store.mark_running("bot1", 2, 101, capability=second.capability)
        await store.finish_request("bot1", 2, 101, success=False, capability=second.capability)
        # failures consume quota: no refund
        assert (await store.reserve_request("bot1", 3, 101)).allowed is False
    finally:
        await store.close()


async def test_restart_persists_quota(tmp_path: Path) -> None:
    db = tmp_path / "persist.db"
    clock = FakeClock()
    store = _make_store(db, clock, requests_per_hour=1, requests_per_day=20)
    await store.initialize()
    assert (await store.reserve_request("bot1", 1, 101)).allowed is True
    await store.close()
    # reopen same file: quota must survive restart (no reset)
    store2 = _make_store(db, clock, requests_per_hour=1, requests_per_day=20)
    await store2.initialize()
    try:
        assert (await store2.reserve_request("bot1", 2, 101)).allowed is False
        # nonterminal reservation still dedups after reopen (no auto-retry)
        dup = await store2.reserve_request("bot1", 1, 101)
        assert dup.allowed is False
        assert dup.duplicate is True
    finally:
        await store2.close()


async def test_concurrent_two_connections_single_winner(tmp_path: Path) -> None:
    from agency.telegram.budget_store import BetaBudgetStore, BudgetLimits

    db = tmp_path / "race.db"
    clock = FakeClock()
    a = BetaBudgetStore(str(db), limits=BudgetLimits(), clock=clock)
    b = BetaBudgetStore(str(db), limits=BudgetLimits(), clock=clock)
    await a.initialize()
    await b.initialize()
    try:
        ra, rb = await asyncio.gather(
            a.reserve_request("bot1", 77, 101),
            b.reserve_request("bot1", 77, 101),
        )
        winners = [r for r in (ra, rb) if r.allowed]
        losers = [r for r in (ra, rb) if not r.allowed]
        assert len(winners) == 1
        assert len(losers) == 1
        assert losers[0].duplicate is True
        # only the winner receives an execution capability
        assert winners[0].capability is not None
        assert losers[0].capability is None
    finally:
        await a.close()
        await b.close()


# --- model calls ---


async def test_model_call_requires_running_reservation(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        # no reservation at all
        assert await store.reserve_model_call("bot1", 50, 101) is False
        # reserved but not running
        reserved = await store.reserve_request("bot1", 51, 101)
        assert reserved.allowed is True
        assert reserved.capability is not None
        capability = reserved.capability
        assert await store.reserve_model_call("bot1", 51, 101) is False
        assert await store.reserve_model_call("bot1", 51, 101, capability=capability) is False
        # running grants
        await store.mark_running("bot1", 51, 101, capability=capability)
        assert await store.reserve_model_call("bot1", 51, 101, capability=capability) is True
    finally:
        await store.close()


async def test_model_per_request_cap(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock, model_calls_per_request=2)
    await store.initialize()
    try:
        reserved = await store.reserve_request("bot1", 60, 101)
        assert reserved.allowed is True
        assert reserved.capability is not None
        capability = reserved.capability
        await store.mark_running("bot1", 60, 101, capability=capability)
        assert await store.reserve_model_call("bot1", 60, 101, capability=capability) is True
        assert await store.reserve_model_call("bot1", 60, 101, capability=capability) is True
        assert await store.reserve_model_call("bot1", 60, 101, capability=capability) is False
    finally:
        await store.close()


async def test_model_hour_cap_counts_failures_no_refund(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock, model_calls_per_hour=2, model_calls_per_day=160)
    await store.initialize()
    try:
        first = await store.reserve_request("bot1", 61, 101)
        assert first.allowed is True
        capability = first.capability
        await store.mark_running("bot1", 61, 101, capability=capability)
        assert await store.reserve_model_call("bot1", 61, 101, capability=capability) is True
        assert await store.reserve_model_call("bot1", 61, 101, capability=capability) is True
        assert await store.reserve_model_call("bot1", 61, 101, capability=capability) is False
        await store.finish_request("bot1", 61, 101, success=False, capability=capability)
        # new request still blocked on the model hour quota
        second = await store.reserve_request("bot1", 62, 101)
        assert second.allowed is True
        capability2 = second.capability
        await store.mark_running("bot1", 62, 101, capability=capability2)
        assert await store.reserve_model_call("bot1", 62, 101, capability=capability2) is False
    finally:
        await store.close()


async def test_mark_running_and_finish_state_machine(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        reserved = await store.reserve_request("bot1", 70, 101)
        assert reserved.allowed is True
        capability = reserved.capability
        await store.mark_running("bot1", 70, 101, capability=capability)
        with pytest.raises(ValueError):
            await store.mark_running("bot1", 70, 101, capability=capability)
        await store.finish_request("bot1", 70, 101, success=True, capability=capability)
        with pytest.raises(ValueError):
            await store.finish_request("bot1", 70, 101, success=True, capability=capability)
        # terminal reservation still dedups, never reauthorizes
        dup = await store.reserve_request("bot1", 70, 101)
        assert dup.allowed is False
        assert dup.duplicate is True
        assert dup.state == "COMPLETED"
        assert dup.capability is None
        # model calls after completion are denied
        assert await store.reserve_model_call("bot1", 70, 101, capability=capability) is False
        with pytest.raises(TypeError):
            await store.finish_request(
                "bot1",
                70,
                101,
                success="yes",
                capability=capability,  # type: ignore[arg-type]
            )
    finally:
        await store.close()


# --- clock rollback / errors ---


async def test_clock_rollback_fails_closed(tmp_path: Path) -> None:
    from agency.telegram.budget_store import BudgetUnavailable

    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        first = await store.reserve_request("bot1", 1, 101)
        assert first.allowed is True
        clock.now -= 60.0
        with pytest.raises(BudgetUnavailable):
            await store.reserve_request("bot1", 2, 101)
        with pytest.raises(BudgetUnavailable):
            await store.reserve_model_call("bot1", 1, 101, capability=first.capability)
    finally:
        await store.close()


async def test_clock_rollback_survives_restart(tmp_path: Path) -> None:
    from agency.telegram.budget_store import BudgetUnavailable

    db = tmp_path / "clock.db"
    clock = FakeClock(start=1_700_000_000.0)
    store = _make_store(db, clock)
    await store.initialize()
    assert (await store.reserve_request("bot1", 1, 101)).allowed is True
    await store.close()
    clock.now -= 3600.0  # wall clock moved backwards across restart
    store2 = _make_store(db, clock)
    await store2.initialize()
    try:
        with pytest.raises(BudgetUnavailable):
            await store2.reserve_request("bot1", 2, 101)
    finally:
        await store2.close()


async def test_bad_clock_values_fail_closed(tmp_path: Path) -> None:
    from agency.telegram.budget_store import BetaBudgetStore, BudgetLimits, BudgetUnavailable

    for bad in (float("nan"), float("inf"), "now", None, True):

        def _clock(bad=bad):  # type: ignore[no-untyped-def]
            return bad

        store = BetaBudgetStore(
            str(tmp_path / "c.db"),
            limits=BudgetLimits(),
            clock=_clock,  # type: ignore[arg-type]
        )
        await store.initialize()
        try:
            with pytest.raises(BudgetUnavailable):
                await store.reserve_request("bot1", 1, 101)
        finally:
            await store.close()


# --- locked / corrupt DB ---


async def test_db_errors_raise_budget_unavailable_without_path_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agency.telegram.budget_store import BetaBudgetStore, BudgetLimits, BudgetUnavailable

    clock = FakeClock()
    db = tmp_path / "locked.db"
    store = BetaBudgetStore(str(db), limits=BudgetLimits(), clock=clock)
    await store.initialize()
    await store.close()

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sqlite3, "connect", _boom)
    store2 = BetaBudgetStore(str(db), limits=BudgetLimits(), clock=clock)
    try:
        with pytest.raises(BudgetUnavailable) as ei:
            await store2.initialize()
        assert str(db) not in str(ei.value)
        with pytest.raises(BudgetUnavailable) as ej:
            await store2.reserve_request("bot1", 1, 101)
        assert str(db) not in str(ej.value)
    finally:
        await store2.close()


async def test_corrupt_db_fails_closed(tmp_path: Path) -> None:
    from agency.telegram.budget_store import BetaBudgetStore, BudgetLimits, BudgetUnavailable

    db = tmp_path / "corrupt.db"
    db.write_bytes(b"this is not a sqlite database at all" * 8)
    store = BetaBudgetStore(str(db), limits=BudgetLimits(), clock=FakeClock())
    try:
        with pytest.raises(BudgetUnavailable):
            await store.initialize()
    finally:
        await store.close()


async def test_unwritable_db_path_fails_closed(tmp_path: Path) -> None:
    from agency.telegram.budget_store import BetaBudgetStore, BudgetLimits, BudgetUnavailable

    blocker = tmp_path / "blocker"
    blocker.write_text("file, not a directory")
    bad_path = blocker / "nested.db"
    store = BetaBudgetStore(str(bad_path), limits=BudgetLimits(), clock=FakeClock())
    try:
        with pytest.raises(BudgetUnavailable) as ei:
            await store.initialize()
        assert str(bad_path) not in str(ei.value)
    finally:
        await store.close()


# --- privacy / retention ---


async def test_schema_stores_no_prompt_or_content(tmp_path: Path) -> None:
    db = tmp_path / "priv.db"
    clock = FakeClock()
    store = _make_store(db, clock)
    await store.initialize()
    try:
        reserved = await store.reserve_request("bot1", 5, 101)
        assert reserved.allowed is True
        assert reserved.capability is not None
        capability = reserved.capability
        await store.mark_running("bot1", 5, 101, capability=capability)
        assert await store.reserve_model_call("bot1", 5, 101, capability=capability) is True
    finally:
        await store.close()
    conn = sqlite3.connect(str(db))
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "requests" in tables
        assert "model_attempts" in tables
        schema = "\n".join(
            row[0]
            for row in conn.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
            ).fetchall()
        ).lower()
        for forbidden in (
            "prompt",
            "query",
            "reply",
            "token",
            "api_key",
            "apikey",
            "message",
            "content",
            "response",
        ):
            assert forbidden not in schema
        cols = " ".join(
            row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()
        ).lower()
        assert "user_id" in cols
        assert "update_id" in cols
        rows = conn.execute("SELECT * FROM requests").fetchall()
        assert len(rows) == 1
        blob = " ".join(str(c) for r in rows for c in r).lower()
        assert "hello" not in blob
        # execution capability is process-local: never persisted to the ledger
        assert capability not in blob
        assert capability.lower() not in blob
    finally:
        conn.close()


async def test_pruning_only_removes_terminal_states(tmp_path: Path) -> None:
    """Terminal requests age out after 48h; nonterminal must never be pruned by age."""
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        # Terminal request: should be pruned after 48h
        terminal = await store.reserve_request("bot1", 1, 101)
        assert terminal.allowed is True
        terminal_capability = terminal.capability
        await store.mark_running("bot1", 1, 101, capability=terminal_capability)
        assert (
            await store.reserve_model_call("bot1", 1, 101, capability=terminal_capability) is True
        )
        await store.finish_request("bot1", 1, 101, success=True, capability=terminal_capability)
        clock.advance(49 * 3600.0)
        assert (await store.reserve_request("bot1", 2, 101)).allowed is True
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        try:
            assert (
                conn.execute("SELECT COUNT(*) FROM requests WHERE update_id = 1").fetchone()[0] == 0
            )
            # terminal attempts are pruned with their parent: no orphan rows
            assert (
                conn.execute("SELECT COUNT(*) FROM model_attempts WHERE update_id = 1").fetchone()[
                    0
                ]
                == 0
            )
            assert _orphan_attempt_count(conn) == 0
        finally:
            conn.close()
        # Nonterminal request must survive past 48h
        assert (await store.reserve_request("bot1", 3, 101)).allowed is True
        clock.advance(49 * 3600.0)
        assert (await store.reserve_request("bot1", 4, 101)).allowed is True
        conn2 = sqlite3.connect(str(tmp_path / "t.db"))
        try:
            assert (
                conn2.execute("SELECT COUNT(*) FROM requests WHERE update_id = 3").fetchone()[0]
                == 1
            )
        finally:
            conn2.close()
    finally:
        await store.close()


async def test_nonterminal_survives_48h_restart_still_duplicate(
    tmp_path: Path,
) -> None:
    """Hermetic test: advance 49h, restart, same nonterminal ID remains duplicate, no new model auth."""
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        first = await store.reserve_request("bot1", 7, 101)
        assert first.allowed is True
        assert first.state == "RESERVED"
        # Advance 49h (beyond 48h retention)
        clock.advance(49 * 3600.0)
        # Restart with new store instance
        store2 = _make_store(tmp_path / "t.db", clock)
        await store2.initialize()
        try:
            # Same ID must remain a duplicate, never re-authorized
            dup = await store2.reserve_request("bot1", 7, 101)
            assert dup.allowed is False
            assert dup.duplicate is True
            # No new model authorization for the same update_id
            assert await store2.reserve_model_call("bot1", 7, 101) is False
            # A new store instance has no process-local execution capability.
            with pytest.raises(ValueError):
                await store2.mark_running("bot1", 7, 101, capability=first.capability)
            assert (
                await store2.reserve_model_call("bot1", 7, 101, capability=first.capability)
                is False
            )
            # Verify the request row still exists in DB
            conn = sqlite3.connect(str(tmp_path / "t.db"))
            try:
                row = conn.execute(
                    "SELECT state FROM requests WHERE bot_id = ? AND update_id = ?",
                    ("bot1", 7),
                ).fetchone()
                assert row is not None and row[0] == "RESERVED"
            finally:
                conn.close()
        finally:
            await store2.close()
    finally:
        await store.close()


async def test_user_mismatch_on_duplicate_denies_without_revealing_state(
    tmp_path: Path,
) -> None:
    """Same (bot_id,update_id) with different user_id must not reveal prior state."""
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        first = await store.reserve_request("bot1", 12, 101)
        assert first.allowed is True
        # Different user tries same bot_id+update_id
        other = await store.reserve_request("bot1", 12, 202)
        assert other.allowed is False
        # Must not be treated as duplicate (not the same identity)
        assert other.duplicate is False
        # Must NOT reveal the prior state
        assert other.state not in ("RESERVED", "RUNNING", "COMPLETED", "FAILED")
        # Mismatched user cannot advance or finish
        with pytest.raises((KeyError, ValueError)):
            await store.mark_running("bot1", 12, 202)
        assert await store.reserve_model_call("bot1", 12, 202) is False
        with pytest.raises((KeyError, ValueError)):
            await store.finish_request("bot1", 12, 202, success=True)
    finally:
        await store.close()


async def test_reject_memory_db_path(tmp_path: Path) -> None:
    """:memory: db_path must be rejected as nondurable."""
    from agency.telegram.budget_store import BetaBudgetStore

    with pytest.raises(ValueError):
        BetaBudgetStore(":memory:")
    # Error must not disclose the path
    with pytest.raises(ValueError) as ei:
        BetaBudgetStore(":memory:")
    assert ":memory:" not in str(ei.value)
    assert "persistent" in str(ei.value).lower() or "file" in str(ei.value).lower()


async def test_crash_recovery_never_executes_nonterminal(tmp_path: Path) -> None:
    """Crash recovery must return duplicate/unknown for nonterminal reservations and never re-execute."""
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        first = await store.reserve_request("bot1", 15, 101)
        assert first.allowed is True
        await store.mark_running("bot1", 15, 101, capability=first.capability)
        # Crash recovery must not re-execute; returns duplicate
        recovered = await store.crash_recovery("bot1", 15, 101)
        assert recovered.allowed is False
        assert recovered.duplicate is True
        assert recovered.state == "RUNNING"
        # Same update_id can never be re-authorized by reserve_request
        dup = await store.reserve_request("bot1", 15, 101)
        assert dup.allowed is False
        assert dup.duplicate is True
        # Crash recovery itself does not execute or change state;
        # model calls still work because request is legitimately RUNNING.
        # Key assertion: crash_recovery did NOT advance state to COMPLETED.
        assert await store.inspect_reservation("bot1", 15) == "RUNNING"
    finally:
        await store.close()


async def test_crash_recovery_unknown_for_missing_reservation(tmp_path: Path) -> None:
    """Crash recovery for unknown reservation returns unknown, never allowed."""
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        recovered = await store.crash_recovery("bot1", 999, 101)
        assert recovered.allowed is False
        assert recovered.duplicate is False
    finally:
        await store.close()


async def test_inspect_reservation_read_only(tmp_path: Path) -> None:
    """inspect_reservation is read-only: does not modify, prune, or change state."""
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        assert (await store.reserve_request("bot1", 20, 101)).allowed is True
        # Inspect without advancing
        state = await store.inspect_reservation("bot1", 20)
        assert state == "RESERVED"
        # Advance and inspect again: state unchanged (no auto-transition)
        clock.advance(3600.0)
        state2 = await store.inspect_reservation("bot1", 20)
        assert state2 == "RESERVED"
        # Count requests unchanged
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        try:
            assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1
        finally:
            conn.close()
    finally:
        await store.close()


async def test_inspect_reservation_missing_returns_none(tmp_path: Path) -> None:
    """inspect_reservation returns None for unknown update_id."""
    clock = FakeClock()
    store = _make_store(tmp_path / "t.db", clock)
    await store.initialize()
    try:
        assert await store.inspect_reservation("bot1", 999) is None
    finally:
        await store.close()


async def test_running_replay_on_new_store_cannot_reserve_model(tmp_path: Path) -> None:
    clock = FakeClock()
    db = tmp_path / "running.db"
    first_store = _make_store(db, clock)
    await first_store.initialize()
    reservation = await first_store.reserve_request("bot1", 888, 101)
    assert reservation.capability is not None
    await first_store.mark_running("bot1", 888, 101, capability=reservation.capability)
    await first_store.close()

    second_store = _make_store(db, clock)
    await second_store.initialize()
    try:
        replay = await second_store.reserve_request("bot1", 888, 101)
        assert replay.allowed is False
        assert replay.capability is None
        assert await second_store.reserve_model_call("bot1", 888, 101) is False
        assert (
            await second_store.reserve_model_call(
                "bot1", 888, 101, capability=reservation.capability
            )
            is False
        )
        with pytest.raises(ValueError):
            await second_store.finish_request(
                "bot1", 888, 101, success=True, capability=reservation.capability
            )
    finally:
        await second_store.close()


async def test_close_during_worker_never_returns_model_authorization(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _make_store(tmp_path / "close.db", clock)
    await store.initialize()
    reservation = await store.reserve_request("bot1", 901, 101)
    await store.mark_running("bot1", 901, 101, capability=reservation.capability)
    entered = threading.Event()
    release = threading.Event()
    original = store._reserve_model_call_sync

    def paused(*args: Any) -> bool:
        entered.set()
        if not release.wait(timeout=5):
            raise AssertionError("test worker was not released")
        return original(*args)

    store._reserve_model_call_sync = paused
    call = asyncio.create_task(
        store.reserve_model_call("bot1", 901, 101, capability=reservation.capability)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await store.close()
    finally:
        release.set()
    assert await call is False


async def test_close_during_request_worker_never_issues_capability(tmp_path: Path) -> None:
    from agency.telegram.budget_store import BudgetUnavailable

    clock = FakeClock()
    store = _make_store(tmp_path / "request-close.db", clock)
    await store.initialize()
    entered = threading.Event()
    release = threading.Event()
    original = store._reserve_request_sync

    def paused(*args: Any) -> Any:
        entered.set()
        if not release.wait(timeout=5):
            raise AssertionError("test request worker was not released")
        return original(*args)

    store._reserve_request_sync = paused
    call = asyncio.create_task(store.reserve_request("bot1", 902, 101))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await store.close()
    finally:
        release.set()
    with pytest.raises(BudgetUnavailable):
        await call
