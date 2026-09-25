"""Polling hot-loop regression: failures back off without losing offset.

Covers the delivery slice contract: a failed handler (exception), a
non-terminal handler status, and a failed durable offset persist must all
1) sleep with a bounded backoff (no hot spin), 2) leave the in-memory
offset and replay window untouched, 3) keep the durable SQLite offset at
its last committed value, and 4) report ``transient`` so the caller keeps
polling. The next successful poll redelivers the same update at-least-once
and then advances. No live Telegram calls: adapter and butler are mocked.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_beta_telegram_polling import (
    _LogCapture,
    _make_bot,
    _poll_state,
    _private_update,
    _wire_handler,
)

_MAX_BACKOFF = 30.0


def _no_sleep_capture(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace asyncio.sleep with a recorder so tests stay fast and assertable."""
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)
        await real_sleep(0)  # Let other tasks (including stop) make progress.

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    return delays


# --- handler exception: back off, keep offset, redeliver on next poll ---


@pytest.mark.asyncio
async def test_handler_exception_backs_off_and_keeps_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import telegram_bot as bot_module

    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    bot._handler = AsyncMock()
    bot._handler.handle_update.side_effect = RuntimeError("PRIVATE-USER-TEXT")
    bot._handler._adapter.get_updates = AsyncMock(  # type: ignore[method-assign]
        return_value=[_private_update(10, 101, "hello")]
    )
    bot._handler.close = AsyncMock()
    bot._running = True
    logs = _LogCapture()
    monkeypatch.setattr(bot_module, "logger", logs)
    delays = _no_sleep_capture(monkeypatch)

    assert await bot._poll_batch() == "transient"
    assert "PRIVATE-USER-TEXT" not in repr(logs.events)
    # Offset untouched: in-memory and durable.
    assert bot._offset is None
    assert bot._seen == set()
    assert _poll_state(db_path) is None
    # Backed off with bounded delay, no hot spin.
    assert len(delays) == 1
    assert 0 < delays[0] <= _MAX_BACKOFF

    # Failure count reset on the next successful batch.
    bot._handler.handle_update.side_effect = None
    bot._handler.handle_update.return_value = {"status": "ok"}
    assert await bot._poll_batch() == "ok"
    assert bot._offset == 11
    assert _poll_state(db_path) == 11
    assert bot._consecutive_failures == 0
    # Same update was retried, not skipped.
    assert bot._handler.handle_update.await_count == 2
    await bot.stop()


@pytest.mark.asyncio
async def test_handler_exception_backoff_grows_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _make_bot(tmp_path, monkeypatch)
    bot._handler = AsyncMock()
    bot._handler.handle_update.side_effect = RuntimeError("send failed")
    bot._handler._adapter.get_updates = AsyncMock(  # type: ignore[method-assign]
        return_value=[_private_update(20, 101, "hello")]
    )
    bot._handler.close = AsyncMock()
    bot._running = True
    delays = _no_sleep_capture(monkeypatch)

    for _ in range(5):
        assert await bot._poll_batch() == "transient"
    assert len(delays) == 5
    # Exponential but capped: strictly non-decreasing, never above the cap.
    assert all(0 < d <= _MAX_BACKOFF for d in delays)
    assert delays == sorted(delays)
    assert delays[-1] == _MAX_BACKOFF  # 2**5 capped at 30s
    assert bot._offset is None
    await bot.stop()


# --- non-terminal status: back off, keep offset ---


@pytest.mark.asyncio
async def test_nonterminal_status_backs_off_and_keeps_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    bot._handler = AsyncMock()
    bot._handler.handle_update.side_effect = [
        {"status": "error", "reason": "profile unavailable"},
        {"status": "ok"},
    ]
    bot._handler._adapter.get_updates = AsyncMock(  # type: ignore[method-assign]
        return_value=[_private_update(30, 101, "hello")]
    )
    bot._handler.close = AsyncMock()
    bot._running = True
    delays = _no_sleep_capture(monkeypatch)

    assert await bot._poll_batch() == "transient"
    assert bot._offset is None
    assert _poll_state(db_path) is None
    assert len(delays) == 1
    assert 0 < delays[0] <= _MAX_BACKOFF

    assert await bot._poll_batch() == "ok"
    assert bot._offset == 31
    assert _poll_state(db_path) == 31
    assert bot._handler.handle_update.await_count == 2
    await bot.stop()


# --- DB persist failure: back off, offset stays at last durable value ---


@pytest.mark.asyncio
async def test_db_persist_failure_backs_off_without_losing_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    bot._handler = AsyncMock()
    bot._handler.handle_update.return_value = {"status": "ok"}
    bot._handler.close = AsyncMock()
    bot._handler._adapter.get_updates = AsyncMock(  # type: ignore[method-assign]
        return_value=[_private_update(10, 101, "hello")]
    )
    bot._running = True
    assert await bot._poll_batch() == "ok"
    assert bot._offset == 11
    assert _poll_state(db_path) == 11

    # Inject a durable persist failure after a successful handler result.
    bot._handler.handle_update.reset_mock()
    real_conn = bot._poll_conn
    failing = MagicMock()
    failing.execute.side_effect = sqlite3.OperationalError("injected persist failure")
    bot._poll_conn = failing  # type: ignore[assignment]
    bot._handler._adapter.get_updates = AsyncMock(  # type: ignore[method-assign]
        return_value=[_private_update(11, 101, "retry")]
    )
    delays = _no_sleep_capture(monkeypatch)

    assert await bot._poll_batch() == "transient"
    # No data loss: memory stays at the last durable offset, nothing acked.
    assert bot._offset == 11
    assert 11 not in bot._seen
    assert _poll_state(db_path) == 11
    assert bot._handler.handle_update.await_count == 1
    assert len(delays) == 1
    assert 0 < delays[0] <= _MAX_BACKOFF

    # Recovery: real conn restored, same update redelivered and advances.
    bot._poll_conn = real_conn
    assert await bot._poll_batch() == "ok"
    assert bot._offset == 12
    assert _poll_state(db_path) == 12
    await bot.stop()


# --- the loop itself never spins hot and never skips ahead ---


@pytest.mark.asyncio
async def test_poll_loop_persists_failure_without_tight_spin_or_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import telegram_bot as bot_module

    bot = _make_bot(tmp_path, monkeypatch)
    bot._handler = AsyncMock()
    bot._handler.handle_update.side_effect = RuntimeError("persistently failing")
    bot._handler._adapter.get_updates = AsyncMock(  # type: ignore[method-assign]
        return_value=[_private_update(40, 101, "hello")]
    )
    bot._handler.close = AsyncMock()
    logs = _LogCapture()
    monkeypatch.setattr(bot_module, "logger", logs)
    real_sleep = asyncio.sleep
    delays = _no_sleep_capture(monkeypatch)

    async def stop_soon() -> None:
        await real_sleep(0)
        for _ in range(50):
            await real_sleep(0)
            if len(delays) >= 3:
                break
        bot._running = False

    bot._running = True
    await asyncio.gather(bot._poll(), stop_soon())
    # Every failing iteration slept: zero consecutive unslept failures.
    assert len(delays) >= 3
    assert all(0 < d <= _MAX_BACKOFF for d in delays)
    # getUpdates never advanced past the failing update.
    get_updates = bot._handler._adapter.get_updates
    assert isinstance(get_updates, AsyncMock)
    offsets = [c.kwargs.get("offset") for c in get_updates.await_args_list]
    assert offsets and all(o is None for o in offsets)
    await bot.stop()


@pytest.mark.asyncio
async def test_recovery_after_failures_resumes_from_pending_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    bot._handler = AsyncMock()
    bot._handler.handle_update.side_effect = RuntimeError("down")
    bot._handler._adapter.get_updates = AsyncMock(  # type: ignore[method-assign]
        return_value=[_private_update(50, 101, "hello")]
    )
    bot._handler.close = AsyncMock()
    bot._running = True
    _no_sleep_capture(monkeypatch)
    assert await bot._poll_batch() == "transient"

    # New update arrives while 50 is still pending: it must not be handled
    # or acked ahead of the stuck one.
    bot._handler.handle_update.reset_mock()
    bot._handler._adapter.get_updates = AsyncMock(  # type: ignore[method-assign]
        return_value=[_private_update(50, 101, "hello"), _private_update(51, 101, "later")]
    )
    assert await bot._poll_batch() == "transient"
    assert bot._handler.handle_update.await_count == 1  # only update 50 retried
    assert bot._offset is None

    # Handler recovers: both updates process in order, batch advances.
    bot._handler.handle_update.side_effect = None
    bot._handler.handle_update.return_value = {"status": "ok"}
    assert await bot._poll_batch() == "ok"
    assert bot._handler.handle_update.await_count == 3
    assert bot._offset == 52
    assert _poll_state(db_path) == 52
    await bot.stop()


# --- exception path still reports transient (caller keeps polling) ---


@pytest.mark.asyncio
async def test_transient_outcome_is_reported_not_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _make_bot(tmp_path, monkeypatch)
    bot._handler = AsyncMock()
    bot._handler.handle_update.side_effect = RuntimeError("boom")
    bot._handler._adapter.get_updates = AsyncMock(  # type: ignore[method-assign]
        return_value=[_private_update(60, 101, "hello")]
    )
    bot._handler.close = AsyncMock()
    bot._running = True
    _no_sleep_capture(monkeypatch)
    # Contract: callers must see "transient" (not "ok") so failure is visible.
    assert await bot._poll_batch() != "ok"
    assert await bot._poll_batch() == "transient"
    await bot.stop()


# --- success path still advances and reports ok (no regression) ---


@pytest.mark.asyncio
async def test_success_path_unaffected_by_backoff_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    _wire_handler(bot)
    bot._handler._adapter.get_updates = AsyncMock(  # type: ignore[method-assign]
        return_value=[_private_update(70, 101, "hello"), _private_update(71, 202, "hi")]
    )
    bot._running = True
    delays = _no_sleep_capture(monkeypatch)

    assert await bot._poll_batch() == "ok"
    assert bot._offset == 72
    assert _poll_state(db_path) == 72
    assert delays == []  # no backoff on success
    assert bot._consecutive_failures == 0
    await bot.stop()
