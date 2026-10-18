"""Hermetic lifecycle regressions for the Telegram webhook and handler."""

from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import AsyncMock, Mock

import pytest

import agency.telegram.server as server_module
from agency.telegram.config import TelegramConfig
from agency.telegram.handler import TelegramHandler
from agency.telegram.profile_store import ProfileStore
from agency.telegram.server import create_app


@pytest.mark.asyncio
@pytest.mark.parametrize("body_error", [None, ValueError("body failed"), asyncio.CancelledError()])
async def test_lifespan_closes_injected_handler_on_every_exit(
    body_error: BaseException | None,
) -> None:
    handler = AsyncMock(spec=TelegramHandler)
    app = create_app(TelegramConfig(bot_token="test", webhook_secret="test"), handler)

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            handler.close.assert_not_awaited()
            if body_error is not None:
                raise body_error

    if body_error is None:
        await exercise()
    else:
        with pytest.raises(type(body_error)) as caught:
            await exercise()
        assert caught.value is body_error
    handler.close.assert_awaited_once_with()
    assert app.router.on_shutdown == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body_error", [None, ValueError("body failed"), asyncio.CancelledError()])
async def test_lifespan_preserves_primary_error_when_cleanup_fails(
    body_error: BaseException | None,
) -> None:
    cleanup_error = RuntimeError("cleanup failed")
    handler = AsyncMock(spec=TelegramHandler)
    handler.close.side_effect = cleanup_error
    app = create_app(TelegramConfig(bot_token="test"), handler)
    expected = body_error if body_error is not None else cleanup_error

    with pytest.raises(type(expected)) as caught:
        async with app.router.lifespan_context(app):
            if body_error is not None:
                raise body_error

    assert caught.value is expected
    handler.close.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter_error", [None, RuntimeError("adapter failed"), asyncio.CancelledError()]
)
@pytest.mark.parametrize("profile_fails", [False, True])
async def test_handler_close_attempts_both_resources_and_preserves_primary(
    adapter_error: BaseException | None, profile_fails: bool
) -> None:
    profiles = Mock(spec=ProfileStore)
    profile_error = RuntimeError("profile failed")
    if profile_fails:
        profiles.close.side_effect = profile_error
    backend, budget = AsyncMock(), AsyncMock()
    handler = TelegramHandler(
        TelegramConfig(bot_token="test"), butler=backend, profile_store=profiles, budget=budget
    )
    adapter = AsyncMock()
    adapter.close.side_effect = adapter_error
    handler._adapter = adapter
    expected = (
        adapter_error if adapter_error is not None else profile_error if profile_fails else None
    )
    if expected is None:
        await handler.close()
    else:
        with pytest.raises(type(expected)) as caught:
            await handler.close()
        assert caught.value is expected
    adapter.close.assert_awaited_once_with()
    profiles.close.assert_called_once_with()
    backend.close.assert_not_called()
    budget.close.assert_not_called()


@pytest.mark.asyncio
async def test_handler_close_releases_default_real_profile_store() -> None:
    handler = TelegramHandler(TelegramConfig(bot_token="test"))
    await handler.close()
    with pytest.raises(sqlite3.ProgrammingError):
        handler._profiles.get_name(1)


@pytest.mark.parametrize(
    "construction_error", [RuntimeError("construct failed"), asyncio.CancelledError()]
)
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_app_rolls_back_owned_profile_when_handler_construction_fails(
    monkeypatch: pytest.MonkeyPatch, construction_error: BaseException, cleanup_fails: bool
) -> None:
    profiles = Mock(spec=ProfileStore)
    if cleanup_fails:
        profiles.close.side_effect = RuntimeError("profile cleanup failed")
    allocate = Mock(return_value=profiles)
    construct = Mock(side_effect=construction_error)
    monkeypatch.setattr(server_module, "ProfileStore", allocate)
    monkeypatch.setattr(server_module, "TelegramHandler", construct)
    with pytest.raises(type(construction_error)) as caught:
        create_app(TelegramConfig(bot_token="test"))
    assert caught.value is construction_error
    allocate.assert_called_once()
    construct.assert_called_once()
    profiles.close.assert_called_once_with()
