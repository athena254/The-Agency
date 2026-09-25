"""Beta polling/replay reliability (delivery slice).

At-least-once poller: offset advances only after successful ``handle_update``
(``ok``/``ignored``/``rejected``), persists in SQLite at the explicit
``TELEGRAM_POLL_DB_PATH`` in beta, survives restart, retries failed sends,
skips duplicates, surfaces 409 competing pollers, and filters the command
menu. No live Telegram calls: the adapter transport is fully mocked.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agency.telegram.config import TelegramConfig
from telegram_bot import TelegramBot

INVITED = [101, 202]


def _beta_config() -> TelegramConfig:
    return TelegramConfig(bot_token="test", beta_mode=True, allowed_user_ids=INVITED)


def _private_update(update_id: int, user_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "from": {"id": user_id},
            "chat": {"id": user_id, "type": "private"},
            "text": text,
        },
    }


def _make_bot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    beta: bool = True,
    poll_db: str | None = "auto",
) -> TelegramBot:
    """Construct a real TelegramBot isolated from repo data files."""
    monkeypatch.setenv("REMEX_PROFILE_DB_PATH", str(tmp_path / "profiles.db"))
    if beta:
        cfg = _beta_config()
        db_path = str(tmp_path / "poll.db") if poll_db == "auto" else poll_db
        assert db_path is not None
        monkeypatch.setenv("TELEGRAM_POLL_DB_PATH", db_path)
        bot = TelegramBot(token="test", config=cfg, poll_db_path=db_path)
    else:
        monkeypatch.delenv("TELEGRAM_POLL_DB_PATH", raising=False)
        monkeypatch.delenv("AGENCY_BETA_MODE", raising=False)
        bot = TelegramBot(token="test", config=TelegramConfig(bot_token="test"))
    bot._butler = AsyncMock()  # type: ignore[method-assign]
    bot._butler.stop = AsyncMock()
    bot._orchestrator = AsyncMock()  # type: ignore[method-assign]
    bot._orchestrator.stop = AsyncMock()
    return bot


def _wire_handler(bot: TelegramBot, butler: Any = "auto") -> Any:
    """Point the bot's real handler at a mock butler and mock transport."""
    b = AsyncMock() if butler == "auto" else butler
    if b is not None:
        b.handle_message.return_value = "ok"
    bot._handler._butler = b
    bot._handler._adapter.send_message = AsyncMock()  # type: ignore[method-assign]
    return b


def _conflict_error() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.telegram.org/getUpdates")
    response = httpx.Response(409, request=request)
    return httpx.HTTPStatusError(
        "Conflict: terminated by other getUpdates", request=request, response=response
    )


def _poll_state(db_path: str) -> int | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT next_offset FROM telegram_poll_state WHERE key='offset'"
        ).fetchone()
    return int(row[0]) if row else None


# --- beta demands an explicit poll DB path ---


def test_beta_demands_poll_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEX_PROFILE_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.delenv("TELEGRAM_POLL_DB_PATH", raising=False)
    with pytest.raises(ValueError, match="TELEGRAM_POLL_DB_PATH"):
        TelegramBot(token="test", config=_beta_config(), poll_db_path=None)
    with pytest.raises(ValueError, match="TELEGRAM_POLL_DB_PATH"):
        TelegramBot(token="test", config=_beta_config(), poll_db_path="  ")
    # Non-beta stays in-memory and never demands the path.
    bot = TelegramBot(token="test", config=TelegramConfig(bot_token="test"))
    assert bot._offset is None
    assert bot._poll_conn is None


# --- success advances, persists, and survives restart ---


@pytest.mark.asyncio
async def test_beta_success_advances_and_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    _wire_handler(bot)
    bot._handler._adapter.get_updates = AsyncMock(  # type: ignore[method-assign]
        return_value=[_private_update(10, 101, "hello"), _private_update(11, 202, "hi")]
    )
    bot._running = True
    assert await bot._poll_batch() == "ok"
    assert bot._offset == 12
    assert _poll_state(db_path) == 12

    restarted = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    assert restarted._offset == 12
    seen: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> list[dict[str, Any]]:
        seen.append(kwargs)
        return []

    restarted._handler._adapter.get_updates = _capture  # type: ignore[method-assign]
    restarted._running = True
    assert await restarted._poll_batch() == "ok"
    assert seen and seen[0].get("offset") == 12
    await bot.stop()
    await restarted.stop()


# --- P-D1: offset advances only after successful handling ---


@pytest.mark.asyncio
async def test_failed_send_leaves_offset_pending_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    _wire_handler(bot)
    send = bot._handler._adapter.send_message
    assert isinstance(send, AsyncMock)
    send.side_effect = [RuntimeError("send failed"), {"message_id": 1}]
    update = _private_update(20, 101, "hello")
    bot._handler._adapter.get_updates = AsyncMock(return_value=[update])  # type: ignore[method-assign]
    bot._running = True
    assert await bot._poll_batch() == "ok"
    assert bot._offset is None  # failed send must not advance (P-D1)
    assert _poll_state(db_path) is None
    assert await bot._poll_batch() == "ok"
    assert bot._offset == 21
    assert _poll_state(db_path) == 21
    assert send.await_count == 2  # at-least-once retry, no silent loss
    await bot.stop()


@pytest.mark.asyncio
async def test_error_status_does_not_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    bot._handler = AsyncMock()
    bot._handler.handle_update.side_effect = [
        {"status": "error", "reason": "profile unavailable"},
        {"status": "ok"},
    ]
    bot._handler._adapter.get_updates = AsyncMock(return_value=[_private_update(30, 101, "hello")])
    bot._handler.close = AsyncMock()
    bot._running = True
    assert await bot._poll_batch() == "ok"
    assert bot._offset is None
    assert _poll_state(db_path) is None
    assert await bot._poll_batch() == "ok"
    assert bot._offset == 31
    assert _poll_state(db_path) == 31
    await bot.stop()


@pytest.mark.asyncio
async def test_rejected_and_ignored_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    # Rejected (uninvited user) advances via the real beta handler.
    _wire_handler(bot)
    bot._handler._adapter.get_updates = AsyncMock(  # type: ignore[method-assign]
        return_value=[_private_update(40, 999, "hello")]
    )
    bot._running = True
    assert await bot._poll_batch() == "ok"
    assert bot._offset == 41
    assert _poll_state(db_path) == 41
    # Ignored advances too (explicit terminal outcome, no retry).
    bot._handler = AsyncMock()
    bot._handler.handle_update.return_value = {"status": "ignored", "reason": "no message"}
    bot._handler._adapter.get_updates = AsyncMock(return_value=[{"update_id": 41, "message": {}}])
    bot._handler.close = AsyncMock()
    assert await bot._poll_batch() == "ok"
    assert bot._offset == 42
    assert _poll_state(db_path) == 42
    await bot.stop()


# --- P-D4: replay stays bounded ---


@pytest.mark.asyncio
async def test_duplicate_replay_not_rehandled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    bot._handler = AsyncMock()
    bot._handler.handle_update.return_value = {"status": "ok"}
    batch = [_private_update(50, 101, "/name Atlas"), _private_update(50, 101, "/name Atlas")]
    bot._handler._adapter.get_updates = AsyncMock(return_value=batch)
    bot._handler.close = AsyncMock()
    bot._running = True
    assert await bot._poll_batch() == "ok"
    assert bot._handler.handle_update.await_count == 1
    assert bot._offset == 51
    # Redelivery of the same persisted offset window is skipped, not re-handled.
    bot._handler._adapter.get_updates = AsyncMock(return_value=batch)
    assert await bot._poll_batch() == "ok"
    assert bot._handler.handle_update.await_count == 1
    assert bot._offset == 51
    assert _poll_state(db_path) == 51
    await bot.stop()


# --- P-D3: competing poller surfaces instead of tight retry ---


class _LogCapture:
    """Capture structlog-style calls (structlog bypasses pytest caplog)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def info(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("info", args, kwargs))

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("warning", args, kwargs))

    def error(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("error", args, kwargs))

    def exception(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("exception", args, kwargs))

    def text(self) -> str:
        return " ".join(
            " ".join([str(a) for a in args] + [f"{k}={v}" for k, v in kwargs.items()])
            for _, args, kwargs in self.events
        )


@pytest.mark.asyncio
async def test_competing_poller_stops_with_safe_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import telegram_bot as bot_module

    bot = _make_bot(tmp_path, monkeypatch)
    bot._handler._adapter.get_updates = AsyncMock(side_effect=_conflict_error())  # type: ignore[method-assign]
    bot._handler._adapter.send_message = AsyncMock()  # type: ignore[method-assign]
    logs = _LogCapture()
    monkeypatch.setattr(bot_module, "logger", logs)
    bot._running = True
    await bot._poll()
    assert bot._running is False
    get_updates = bot._handler._adapter.get_updates
    assert isinstance(get_updates, AsyncMock)
    assert get_updates.await_count == 1  # no endless retry
    assert bot._offset is None
    logged = logs.text().lower()
    assert "409" in logged and "conflict" in logged
    assert "token" not in logged  # escalation carries no credentials
    await bot.stop()


# --- P-D5: menu filtering preserves non-beta ---


def test_menu_filtering_beta_hides_creation_nonbeta_keeps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    beta = _make_bot(tmp_path, monkeypatch)
    beta_names = [c["command"] for c in beta.commands_for_mode()]
    assert "propose_agent" not in beta_names
    assert "proposals" not in beta_names
    assert "status" in beta_names and "name" in beta_names
    # Class-level menu (non-beta default) is untouched.
    assert "propose_agent" in [c["command"] for c in TelegramBot.COMMANDS]
    assert "proposals" in [c["command"] for c in TelegramBot.COMMANDS]

    plain = _make_bot(tmp_path, monkeypatch, beta=False)
    plain_names = [c["command"] for c in plain.commands_for_mode()]
    assert "propose_agent" in plain_names and "proposals" in plain_names


@pytest.mark.asyncio
async def test_start_registers_filtered_menu_in_beta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _make_bot(tmp_path, monkeypatch)
    # Webhook path so start() registers the menu without entering the poll loop.
    bot._config.webhook_url = "https://example.test/hook"
    bot._handler._adapter.set_my_commands = AsyncMock(return_value=True)  # type: ignore[method-assign]
    bot._handler._adapter.set_webhook = AsyncMock(return_value=True)  # type: ignore[method-assign]
    await bot.start()
    set_commands = bot._handler._adapter.set_my_commands
    assert isinstance(set_commands, AsyncMock)
    registered = set_commands.await_args.args[0]
    names = [c["command"] for c in registered]
    assert "propose_agent" not in names and "proposals" not in names
    await bot.stop()


# --- malformed IDs fail safely; non-beta compatible; backoff bounded ---


@pytest.mark.asyncio
async def test_malformed_update_ids_fail_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    bot._handler = AsyncMock()
    bot._handler.handle_update.return_value = {"status": "ok"}
    bot._handler._adapter.get_updates = AsyncMock(
        return_value=[
            {"message": {}},
            {"update_id": "50"},
            {"update_id": True},
            {"update_id": 0},
            {"update_id": -3},
            {"update_id": None},
        ]
    )
    bot._handler.close = AsyncMock()
    bot._running = True
    assert await bot._poll_batch() == "ok"
    bot._handler.handle_update.assert_not_awaited()
    assert bot._offset is None
    assert _poll_state(db_path) is None
    await bot.stop()


@pytest.mark.asyncio
async def test_nonbeta_advances_only_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _make_bot(tmp_path, monkeypatch, beta=False)
    bot._handler = AsyncMock()
    bot._handler.handle_update.side_effect = [RuntimeError("boom"), {"status": "ok"}]
    bot._handler._adapter.get_updates = AsyncMock(return_value=[{"update_id": 60}])
    bot._handler.close = AsyncMock()
    bot._running = True
    assert await bot._poll_batch() == "ok"
    assert bot._offset is None
    assert await bot._poll_batch() == "ok"
    assert bot._offset == 61
    assert bot._poll_conn is None
    await bot.stop()


@pytest.mark.asyncio
async def test_transient_errors_backoff_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _make_bot(tmp_path, monkeypatch)
    bot._handler._adapter.get_updates = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            httpx.TimeoutException("timeout"),
            httpx.ConnectError("dns"),
            [_private_update(70, 101, "hello")],
        ]
    )
    bot._handler._adapter.send_message = AsyncMock()  # type: ignore[method-assign]
    _wire_handler(bot)
    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    bot._running = True
    assert await bot._poll_batch() == "transient"
    assert await bot._poll_batch() == "transient"
    assert await bot._poll_batch() == "ok"
    assert bot._offset == 71
    assert len(delays) == 2
    assert all(0 < d <= 30 for d in delays)
    assert delays[1] >= delays[0]  # bounded growth, no tight loop
    await bot.stop()


@pytest.mark.asyncio
async def test_stop_closes_db_safely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    bot._handler._adapter.get_updates = AsyncMock(return_value=[_private_update(80, 101, "hi")])  # type: ignore[method-assign]
    _wire_handler(bot)
    bot._running = True
    assert await bot._poll_batch() == "ok"
    await bot.stop()
    assert bot._poll_conn is None
    assert _poll_state(db_path) == 81
    await bot.stop()  # second close is safe


# --- durable offset fail-closed: persist first, then advance memory ---


@pytest.mark.asyncio
async def test_beta_persist_failure_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Durable offset is fail-closed: persist first, then advance memory.

    Injects sqlite OperationalError on the durable save after a successful
    handler result. In-memory offset/seen must not advance, the batch must
    stop (no later updates handled), backoff sleep must happen (no tight
    spin), and the next poll must reuse the previous durable offset.
    At-least-once only: a duplicate reply after crash is unavoidable.
    """
    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    bot._handler = AsyncMock()
    bot._handler.handle_update.return_value = {"status": "ok"}
    bot._handler.close = AsyncMock()
    bot._handler._adapter.get_updates = AsyncMock(return_value=[_private_update(10, 101, "hello")])
    bot._running = True
    assert await bot._poll_batch() == "ok"
    assert bot._offset == 11
    assert _poll_state(db_path) == 11

    bot._handler.handle_update.reset_mock()
    real_conn = bot._poll_conn
    failing = MagicMock()
    failing.execute.side_effect = sqlite3.OperationalError("injected persist failure")
    bot._poll_conn = failing  # type: ignore[assignment]
    bot._handler._adapter.get_updates = AsyncMock(
        return_value=[_private_update(11, 101, "retry"), _private_update(12, 101, "next")]
    )
    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    outcome = await bot._poll_batch()
    assert outcome == "transient"
    assert bot._offset == 11
    assert 11 not in bot._seen
    assert 12 not in bot._seen
    assert _poll_state(db_path) == 11
    # Failure stops the batch: second update is not handled.
    assert bot._handler.handle_update.await_count == 1
    # No tight spin: backoff sleep is required.
    assert len(delays) >= 1
    assert all(0 < d <= 30 for d in delays)
    get_updates = bot._handler._adapter.get_updates
    assert isinstance(get_updates, AsyncMock)
    assert get_updates.await_args is not None
    assert get_updates.await_args.kwargs.get("offset") == 11

    # Next poll reuses previous durable offset (does not ack/drop update 11).
    bot._poll_conn = real_conn
    seen: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> list[dict[str, Any]]:
        seen.append(kwargs)
        return []

    bot._handler._adapter.get_updates = _capture  # type: ignore[method-assign]
    assert await bot._poll_batch() == "ok"
    assert seen and seen[0].get("offset") == 11
    await bot.stop()


@pytest.mark.asyncio
async def test_beta_failed_persist_db_reopen_retains_old_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB reopen after a failed persist retains the old durable offset."""
    db_path = str(tmp_path / "poll.db")
    bot = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    bot._handler = AsyncMock()
    bot._handler.handle_update.return_value = {"status": "ok"}
    bot._handler.close = AsyncMock()
    bot._handler._adapter.get_updates = AsyncMock(return_value=[_private_update(30, 101, "hello")])
    bot._running = True
    assert await bot._poll_batch() == "ok"
    assert bot._offset == 31
    assert _poll_state(db_path) == 31

    real_conn = bot._poll_conn
    failing = MagicMock()
    failing.execute.side_effect = sqlite3.OperationalError("injected persist failure")
    bot._poll_conn = failing  # type: ignore[assignment]
    bot._handler._adapter.get_updates = AsyncMock(return_value=[_private_update(31, 101, "retry")])

    async def _fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    assert await bot._poll_batch() == "transient"
    assert bot._offset == 31
    assert _poll_state(db_path) == 31
    bot._poll_conn = real_conn

    restarted = _make_bot(tmp_path, monkeypatch, poll_db=db_path)
    assert restarted._offset == 31
    assert _poll_state(db_path) == 31
    await bot.stop()
    await restarted.stop()
