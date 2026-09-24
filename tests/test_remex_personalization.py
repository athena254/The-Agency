"""Telegram display-name customization without changing shared bot identity."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agency.llm.adapter import LLMAdapter
from agency.llm.config import LLMConfig, ProviderKind
from agency.orchestrator import AgencyOrchestrator
from agency.telegram.config import TelegramConfig
from agency.telegram.handler import TelegramHandler
from agency.telegram.profile_store import ProfileStore
from telegram_bot import TelegramBot


def _message(user_id: int, text: str, *, chat_type: str = "private") -> dict:
    return {
        "message": {
            "chat": {"id": user_id, "type": chat_type},
            "from": {"id": user_id},
            "text": text,
        }
    }


@pytest.mark.asyncio
async def test_private_name_command_changes_only_its_owner_and_survives_restart(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "profiles.db")
    store = ProfileStore(path)
    handler = TelegramHandler(TelegramConfig(bot_token="test"), profile_store=store)
    handler._adapter.send_message = AsyncMock()
    await handler.handle_update(_message(101, "/name Atlas"))
    assert "Atlas" in handler._adapter.send_message.await_args.args[1]
    await handler.handle_update(_message(202, "/whoami"))
    assert "Remex" in handler._adapter.send_message.await_args.args[1]
    await handler.close()

    restarted = TelegramHandler(TelegramConfig(bot_token="test"), profile_store=ProfileStore(path))
    restarted._adapter.send_message = AsyncMock()
    await restarted.handle_update(_message(101, "/whoami"))
    assert "Atlas" in restarted._adapter.send_message.await_args.args[1]
    await restarted.handle_update(_message(101, "/start"))
    assert "Atlas" in restarted._adapter.send_message.await_args.args[1]
    await restarted.handle_update(_message(101, "/name reset"))
    await restarted.handle_update(_message(101, "/whoami"))
    assert "Remex" in restarted._adapter.send_message.await_args.args[1]
    await restarted.close()


@pytest.mark.asyncio
async def test_name_rejects_group_invalid_and_prefix_without_writing(tmp_path: Path) -> None:
    store = ProfileStore(str(tmp_path / "profiles.db"))
    handler = TelegramHandler(TelegramConfig(bot_token="test"), profile_store=store)
    handler._adapter.send_message = AsyncMock()
    await handler.handle_update(_message(101, "/name Atlas", chat_type="group"))
    assert store.get_name(101) is None
    await handler.handle_update(_message(101, "/name *Admin*"))
    assert store.get_name(101) is None
    await handler.handle_update(_message(101, "/nameother Atlas"))
    assert store.get_name(101) is None
    await handler.close()


@pytest.mark.asyncio
async def test_private_chat_passes_personal_identity_to_butler(tmp_path: Path) -> None:
    store = ProfileStore(str(tmp_path / "profiles.db"))
    store.set_name(101, "Atlas")
    butler = AsyncMock()
    butler.handle_message.return_value = "A real reply"
    handler = TelegramHandler(TelegramConfig(bot_token="test"), butler=butler, profile_store=store)
    handler._adapter.send_message = AsyncMock()
    await handler.handle_update(_message(101, "hello"))
    assert butler.handle_message.await_args.args[2]["assistant_name"] == "Atlas"
    await handler.handle_update(_message(202, "hello"))
    assert butler.handle_message.await_args.args[2]["assistant_name"] == "Remex"
    assert handler._adapter.send_message.await_args.args[1] == "A real reply"
    await handler.handle_update(_message(101, "hello", chat_type="group"))
    assert butler.handle_message.await_args.args[2]["assistant_name"] == "Remex"
    await handler.close()


@pytest.mark.asyncio
async def test_failed_profile_write_is_not_claimed_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProfileStore(str(tmp_path / "profiles.db"))
    handler = TelegramHandler(TelegramConfig(bot_token="test"), profile_store=store)
    handler._adapter.send_message = AsyncMock()

    def fail(_user_id: int, _name: str) -> None:
        raise sqlite3.OperationalError("disk unavailable")

    monkeypatch.setattr(store, "set_name", fail)
    await handler.handle_update(_message(101, "/name Atlas"))
    assert "Could not save" in handler._adapter.send_message.await_args.args[1]
    assert store.get_name(101) is None
    await handler.close()


@pytest.mark.asyncio
async def test_failed_profile_read_does_not_falsely_claim_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProfileStore(str(tmp_path / "profiles.db"))
    handler = TelegramHandler(TelegramConfig(bot_token="test"), profile_store=store)
    handler._adapter.send_message = AsyncMock()

    def fail(_user_id: int) -> str | None:
        raise sqlite3.OperationalError("disk unavailable")

    monkeypatch.setattr(store, "get_name", fail)
    await handler.handle_update(_message(101, "/name Atlas"))
    assert "unavailable" in handler._adapter.send_message.await_args.args[1].lower()
    await handler.close()


@pytest.mark.asyncio
async def test_process_message_private_path_uses_owners_name(tmp_path: Path) -> None:
    store = ProfileStore(str(tmp_path / "profiles.db"))
    store.set_name(101, "Atlas")
    butler = AsyncMock()
    butler.handle_message.return_value = "ok"
    handler = TelegramHandler(TelegramConfig(bot_token="test"), butler=butler, profile_store=store)
    await handler.process_message(_message(101, "hello")["message"])
    assert butler.handle_message.await_args.args[2]["assistant_name"] == "Atlas"
    await handler.close()


@pytest.mark.asyncio
async def test_research_command_uses_owners_name_in_tool_prompt_context(tmp_path: Path) -> None:
    store = ProfileStore(str(tmp_path / "profiles.db"))
    store.set_name(101, "Atlas")
    orchestrator = AsyncMock()
    orchestrator.list_agents.return_value = [
        SimpleNamespace(id="research-agent", domain="research")
    ]
    orchestrator.submit_task.return_value = SimpleNamespace(task_id="test-task")
    orchestrator.execute_task.return_value = SimpleNamespace(output="source result")
    butler = SimpleNamespace(orchestrator=orchestrator)
    handler = TelegramHandler(TelegramConfig(bot_token="test"), butler=butler, profile_store=store)
    handler._adapter.send_message = AsyncMock()
    await handler.handle_update(_message(101, "/research planets"))
    assert orchestrator.execute_task.await_args.kwargs["context"]["assistant_name"] == "Atlas"
    await handler.close()


def test_names_are_isolated_persistent_and_reset(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "profiles.db"
    store = ProfileStore(str(path))
    assert store.get_name(101) is None
    store.set_name(101, "Atlas")
    store.set_name(202, "Friday")
    store.close()

    reopened = ProfileStore(str(path))
    assert reopened.get_name(101) == "Atlas"
    assert reopened.get_name(202) == "Friday"
    reopened.reset_name(101)
    assert reopened.get_name(101) is None
    assert reopened.get_name(202) == "Friday"
    reopened.close()


@pytest.mark.parametrize("value", ["", "reset", "*Admin*", "A\nB", " 1name", "A" * 33])
def test_store_rejects_invalid_names(tmp_path: Path, value: str) -> None:
    store = ProfileStore(str(tmp_path / "profiles.db"))
    with pytest.raises(ValueError):
        store.set_name(101, value)
    assert store.get_name(101) is None
    store.close()


def test_store_rejects_missing_or_boolean_ids(tmp_path: Path) -> None:
    store = ProfileStore(str(tmp_path / "profiles.db"))
    for bad_id in (0, -1, True):
        with pytest.raises(ValueError):
            store.set_name(bad_id, "Atlas")
    store.close()


def test_bot_registers_name_command_and_uses_file_backed_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEX_PROFILE_DB_PATH", str(tmp_path / "profiles.db"))
    bot = TelegramBot(token="test")
    assert "name" in [item["command"] for item in bot.COMMANDS]
    bot._handler._profiles.set_name(101, "Atlas")
    bot._handler._profiles.close()
    second = TelegramBot(token="test")
    assert second._handler._profiles.get_name(101) == "Atlas"
    second._handler._profiles.close()


def test_corrupt_persisted_name_is_not_rendered(tmp_path: Path) -> None:
    path = str(tmp_path / "profiles.db")
    store = ProfileStore(path)
    store.close()
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO telegram_profiles VALUES (?, ?)", (101, "*spoof*"))
    store = ProfileStore(path)
    assert store.get_name(101) is None
    store.close()


@pytest.mark.asyncio
async def test_classic_agent_prompt_uses_user_display_name_without_changing_butler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    llm = LLMAdapter(config=LLMConfig(provider=ProviderKind.ECHO, model="echo"))
    orch = AgencyOrchestrator(llm=llm, memory_db_path=":memory:")
    await orch.start()
    try:
        agent = await orch.register_agent("security-probe", "security", ["inspect"])
        task = await orch.submit_task("identity", "What is your name?", agent.id)
        result = await orch.execute_task(
            task.task_id, context={"sender": "telegram:101", "assistant_name": "Atlas"}
        )
        assert "Atlas" in str(result.output)
        assert "Butler module" in str(result.output)
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_tool_agent_prompt_uses_user_display_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    llm = LLMAdapter(config=LLMConfig(provider=ProviderKind.ECHO, model="echo"))
    orch = AgencyOrchestrator(llm=llm, memory_db_path=":memory:")
    await orch.start()
    try:
        agent = await orch.register_agent("general-probe", "general", ["respond"])
        task = await orch.submit_task("identity", "What is your name?", agent.id)
        assert orch._tool_driver is not None
        fake_run = AsyncMock(
            return_value=SimpleNamespace(
                status="completed", steps=[], llm_calls=0, final_answer="ok", evidence=[]
            )
        )
        monkeypatch.setattr(orch._tool_driver, "run", fake_run)
        await orch.execute_task(
            task.task_id, context={"sender": "telegram:101", "assistant_name": "Atlas"}
        )
        assert "You are Atlas" in fake_run.await_args.kwargs["system_prompt"]
    finally:
        await orch.stop()
