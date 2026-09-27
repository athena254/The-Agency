"""Offline polling provenance and durable deterministic request admission."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agency.telegram.budget_store import BetaBudgetStore, BudgetLimits
from agency.telegram.config import TelegramConfig
from agency.telegram.handler import TelegramHandler
from agency.telegram.server import create_app
from telegram_bot import TelegramBot


def update(uid: int, text: str, update_id: int = 12) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {"from": {"id": uid}, "chat": {"id": uid, "type": "private"}, "text": text},
    }


def bot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TelegramBot:
    monkeypatch.setenv("REMEX_PROFILE_DB_PATH", str(tmp_path / "profile.db"))
    monkeypatch.setenv("TELEGRAM_BETA_BUDGET_DB_PATH", str(tmp_path / "budget.db"))
    b = TelegramBot(
        token="fake",
        config=TelegramConfig(bot_token="fake", beta_mode=True, allowed_user_ids=[101, 202]),
        poll_db_path=str(tmp_path / "poll.db"),
    )
    b._handler._adapter.get_updates = AsyncMock(return_value=[])
    b._handler._adapter.send_message = AsyncMock()
    b._butler.handle_message = AsyncMock(return_value="UNSAFE")
    return b


@pytest.mark.asyncio
async def test_caller_dict_cannot_mint_model_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = bot(tmp_path, monkeypatch)
    payload = update(101, "hello")
    payload["transport"] = "polling"
    payload["admitted"] = True
    assert (await b._handler.handle_update(payload))["status"] == "rejected"
    with pytest.raises(ValueError, match="trusted polling"):
        await b._handler.process_message(payload["message"])
    b._butler.handle_message.assert_not_awaited()
    b._handler._adapter.send_message.assert_not_awaited()
    await b.stop()


@pytest.mark.asyncio
async def test_polling_name_reserved_once_and_replay_does_not_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = bot(tmp_path, monkeypatch)
    payload = update(101, "/name Atlas")
    b._handler._adapter.get_updates = AsyncMock(return_value=[payload])
    assert await b._poll_batch() == "ok"
    assert b._handler._profiles.get_name(101) == "Atlas"
    b._handler._profiles.set_name(101, "Changed")
    # A fresh bot instance simulates offset-write failure/redelivery after restart.
    again = bot(tmp_path, monkeypatch)
    again._handler._adapter.get_updates = AsyncMock(return_value=[payload])
    assert await again._handler.handle_update(payload) == {
        "status": "rejected",
        "reason": "trusted polling required",
    }
    assert await again._poll_batch() == "ok"
    assert again._handler._profiles.get_name(101) == "Changed"
    with sqlite3.connect(tmp_path / "budget.db") as conn:
        rows = conn.execute("SELECT user_id, update_id, state FROM requests").fetchall()
    assert rows == [(101, 12, "COMPLETED")]
    await b.stop()
    await again.stop()


@pytest.mark.asyncio
async def test_beta_model_paths_never_reach_butler_even_via_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = bot(tmp_path, monkeypatch)
    for number, text in enumerate(("hello", "/research planets"), start=20):
        b._handler._adapter.get_updates = AsyncMock(return_value=[update(101, text, number)])
        assert await b._poll_batch() == "ok"
    b._butler.handle_message.assert_not_awaited()
    assert b._handler._adapter.send_message.await_count == 2
    await b.stop()


@pytest.mark.asyncio
async def test_trusted_status_and_model_denials_consume_one_durable_request_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = bot(tmp_path, monkeypatch)
    b._handler._budget = BetaBudgetStore(
        str(tmp_path / "budget.db"), limits=BudgetLimits(requests_per_hour=2)
    )
    for number, text in ((40, "/status"), (41, "hello"), (42, "/status")):
        b._handler._adapter.get_updates = AsyncMock(return_value=[update(101, text, number)])
        assert await b._poll_batch() == "ok"
    with sqlite3.connect(tmp_path / "budget.db") as conn:
        rows = conn.execute(
            "SELECT update_id, state FROM requests WHERE user_id = 101 ORDER BY update_id"
        ).fetchall()
    assert rows == [(40, "COMPLETED"), (41, "FAILED")]
    b._butler.handle_message.assert_not_awaited()
    assert b._handler._adapter.send_message.await_count == 3
    assert "budget" in b._handler._adapter.send_message.await_args.args[1].lower()
    await b.stop()


@pytest.mark.asyncio
async def test_quota_denies_before_profile_write_and_db_error_denies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = bot(tmp_path, monkeypatch)
    b._handler._budget = BetaBudgetStore(
        str(tmp_path / "budget.db"), limits=BudgetLimits(requests_per_hour=1)
    )
    for number in (30, 31):
        b._handler._adapter.get_updates = AsyncMock(
            return_value=[update(101, f"/name Name{number}", number)]
        )
        assert await b._poll_batch() == "ok"
    assert b._handler._profiles.get_name(101) == "Name30"
    await b._handler._budget.close()
    b._handler._adapter.get_updates = AsyncMock(return_value=[update(202, "/name Bob", 32)])
    assert await b._poll_batch() == "ok"
    assert b._handler._profiles.get_name(202) is None
    await b.stop()


@pytest.mark.asyncio
async def test_chat_restriction_precedes_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEX_PROFILE_DB_PATH", str(tmp_path / "profile.db"))
    monkeypatch.setenv("TELEGRAM_BETA_BUDGET_DB_PATH", str(tmp_path / "budget.db"))
    b = TelegramBot(
        token="fake",
        config=TelegramConfig(
            bot_token="fake", beta_mode=True, allowed_user_ids=[101], allowed_chat_ids=[202]
        ),
        poll_db_path=str(tmp_path / "poll.db"),
    )
    b._handler._adapter.send_message = AsyncMock()
    await b._handler._budget.initialize()
    b._handler._adapter.get_updates = AsyncMock(return_value=[update(101, "/name Atlas")])
    assert await b._poll_batch() == "ok"
    with sqlite3.connect(tmp_path / "budget.db") as conn:
        assert conn.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
    assert b._handler._profiles.get_name(101) is None
    await b.stop()


@pytest.mark.asyncio
async def test_internal_marker_alone_cannot_write_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = bot(tmp_path, monkeypatch)
    result = await b._handler._handle_update(update(101, "/name Forged"), marker=b._polling_marker)
    assert result["status"] == "rejected"
    assert b._handler._profiles.get_name(101) is None
    await b.stop()


def test_beta_webhook_refuses_even_with_secret(tmp_path: Path) -> None:
    config = TelegramConfig(
        bot_token="fake", beta_mode=True, allowed_user_ids=[101], webhook_secret="secret"
    )
    with pytest.raises(ValueError, match="webhook"):
        create_app(config)
    with pytest.raises(ValueError, match="webhook"):
        create_app(config, TelegramHandler(config))
