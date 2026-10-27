"""Private Telegram thread-command transport (nonbeta RED/GREEN slice).

Uses a real :class:`ButlerService` plus a real :class:`TelegramHandler`
over synthetic temp-file stores — not mock keyword assertions — to prove:

* ``/new`` / ``/threads`` / ``/switch`` / ``/current`` work from private
  nonbeta Telegram with a stable per-user owner scope;
* plain follow-ups continue the persisted active thread;
* ``/new@RemexMemorybot`` suffix and ``/unknown`` stay deterministic
  with zero model calls and never reach the LLM;
* two users are isolated; group traffic never enables thread state;
* missing senders are rejected; history survives reopen/restart;
* beta never opens a thread path; the command menu stays nonbeta-scoped.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from agency.butler.context import ThreadContextEngine
from agency.butler.service import ButlerService
from agency.butler.threads import ThreadStore
from agency.orchestrator import AgencyOrchestrator
from agency.telegram.config import TelegramConfig
from agency.telegram.handler import TelegramHandler
from telegram_bot import TelegramBot


def _private_update(user_id: int, text: str) -> dict[str, Any]:
    return {
        "message": {
            "from": {"id": user_id},
            "chat": {"id": user_id, "type": "private"},
            "text": text,
        }
    }


def _group_update(user_id: int, chat_id: int, text: str) -> dict[str, Any]:
    return {
        "message": {
            "from": {"id": user_id},
            "chat": {"id": chat_id, "type": "group"},
            "text": text,
        }
    }


class _Transport:
    """Real service + handler pair with a route spy (zero-LLM proof)."""

    def __init__(self, tmp_path: Any, *, beta: bool = False) -> None:
        self.tmp_path = tmp_path
        self.beta = beta
        self.store = ThreadStore(str(tmp_path / "threads.db"))
        self.orch = AgencyOrchestrator(memory_db_path=str(tmp_path / "memory.db"))
        self.service = ButlerService(orchestrator=self.orch, thread_store=self.store)
        if beta:
            config = TelegramConfig(bot_token="test", beta_mode=True, allowed_user_ids=[101, 202])
            self.butler: Any = AsyncMock()
            self.butler.handle_message.return_value = "SHOULD-NOT-HAPPEN"
        else:
            config = TelegramConfig(bot_token="test")
            self.butler = self.service
        self.handler = TelegramHandler(config, butler=self.butler)
        self.handler._adapter.send_message = AsyncMock()  # type: ignore[method-assign]
        self.route_calls: list[str] = []

    async def start(self) -> _Transport:
        if not self.beta:
            await self.service.start()
            orig_route = self.service.route

            async def spy_route(message: str, context: dict[str, Any]) -> Any:
                self.route_calls.append(message)
                return await orig_route(message, context)

            self.service.route = spy_route  # type: ignore[method-assign]
        return self

    async def send(self, update: dict[str, Any]) -> dict[str, Any]:
        return await self.handler.handle_update(update)

    def last_reply(self) -> str:
        send = self.handler._adapter.send_message
        assert isinstance(send, AsyncMock) and send.await_count >= 1
        return str(send.await_args.args[1])

    def owner(self, user_id: int) -> str:
        return f"telegram:{user_id}"

    async def close(self) -> None:
        await self.handler.close()
        if not self.beta:
            await self.service.stop()
            self.store.close()


@pytest_asyncio.fixture
async def transport(tmp_path: Any) -> AsyncIterator[_Transport]:
    pair = await _Transport(tmp_path).start()
    try:
        yield pair
    finally:
        await pair.close()


# ------------------------------------------------------------------ #
# Thread commands over private transport
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_private_new_creates_thread_and_followup_continues_it(
    transport: _Transport,
) -> None:
    result = await transport.send(_private_update(101, "/new Alpha"))
    assert result["status"] == "ok"
    assert result["command"] == "/new"
    assert "Alpha" in transport.last_reply()
    active = transport.store.get_active_thread(transport.owner(101))
    assert active is not None and active.title == "Alpha"

    result = await transport.send(_private_update(101, "hello thread"))
    assert result["status"] == "ok"
    contents = [m.content for m in transport.store.list_messages(transport.owner(101), active.id)]
    assert "hello thread" in contents


@pytest.mark.asyncio
async def test_threads_switch_current_roundtrip(transport: _Transport) -> None:
    await transport.send(_private_update(101, "/new Alpha"))
    await transport.send(_private_update(101, "/new Beta"))
    listing = transport.last_reply()
    # The latest reply is for /new Beta; ask explicitly for the list.
    result = await transport.send(_private_update(101, "/threads"))
    assert result["status"] == "ok" and result["command"] == "/threads"
    listing = transport.last_reply()
    assert "Alpha" in listing and "Beta" in listing and "*active*" in listing

    result = await transport.send(_private_update(101, "/switch Alpha"))
    assert result["status"] == "ok" and result["command"] == "/switch"
    assert "Alpha" in transport.last_reply()

    result = await transport.send(_private_update(101, "/current"))
    assert result["status"] == "ok" and result["command"] == "/current"
    assert "Alpha" in transport.last_reply()


@pytest.mark.asyncio
async def test_bot_suffix_command_is_deterministic(transport: _Transport) -> None:
    result = await transport.send(_private_update(101, "/new@RemexMemorybot Suffixed"))
    assert result["status"] == "ok"
    assert "Suffixed" in transport.last_reply()
    active = transport.store.get_active_thread(transport.owner(101))
    assert active is not None and active.title == "Suffixed"


@pytest.mark.asyncio
async def test_unknown_command_deterministic_with_zero_model_calls(
    transport: _Transport,
) -> None:
    result = await transport.send(_private_update(101, "/unknown"))
    assert result["status"] == "ok"
    assert "Unknown command" in transport.last_reply()
    assert transport.route_calls == []


@pytest.mark.asyncio
async def test_thread_commands_use_zero_model_calls(transport: _Transport) -> None:
    await transport.send(_private_update(101, "/new Alpha"))
    await transport.send(_private_update(101, "/threads"))
    await transport.send(_private_update(101, "/current"))
    await transport.send(_private_update(101, "/switch Alpha"))
    await transport.send(_private_update(101, "/bogus"))
    await transport.send(_private_update(101, "/new@RemexMemorybot Suffixed"))
    assert transport.route_calls == []


# ------------------------------------------------------------------ #
# Isolation: two users, group/private separation, missing sender
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_two_users_are_isolated(transport: _Transport) -> None:
    await transport.send(_private_update(101, "/new Private"))
    result = await transport.send(_private_update(202, "/threads"))
    assert result["status"] == "ok"
    assert "Private" not in transport.last_reply()

    result = await transport.send(_private_update(202, "/switch Private"))
    assert "No thread titled" in transport.last_reply()

    await transport.send(_private_update(202, "/new Other"))
    active_101 = transport.store.get_active_thread(transport.owner(101))
    active_202 = transport.store.get_active_thread(transport.owner(202))
    assert active_101 is not None and active_202 is not None
    assert active_101.id != active_202.id
    assert active_101.title == "Private" and active_202.title == "Other"

    # Cross-owner reads stay denied at the store boundary.
    with pytest.raises(KeyError):
        transport.store.list_messages(transport.owner(202), active_101.id)


@pytest.mark.asyncio
async def test_group_thread_commands_never_enable_state(
    transport: _Transport,
) -> None:
    result = await transport.send(_group_update(101, 555, "/new GroupThread"))
    assert result["status"] == "rejected"
    assert "private" in transport.last_reply().lower()
    assert transport.store.get_active_thread("telegram:chat:555:user:101") is None
    assert transport.store.get_active_thread(transport.owner(101)) is None
    assert transport.store.list_workspaces(transport.owner(101)) == []
    assert transport.route_calls == []


@pytest.mark.asyncio
async def test_group_unknown_command_never_reaches_butler(
    transport: _Transport,
) -> None:
    result = await transport.send(_group_update(101, 555, "/unknown"))
    assert result["status"] == "ok"
    assert "Unknown command" in transport.last_reply()
    assert transport.route_calls == []


@pytest.mark.asyncio
async def test_missing_sender_rejected_with_zero_side_effects(
    transport: _Transport,
) -> None:
    result = await transport.send(
        {"message": {"chat": {"id": 123}, "from": {}, "text": "/new Alpha"}}
    )
    assert result["status"] == "rejected"
    assert transport.route_calls == []
    assert transport.store.list_workspaces(transport.owner(123)) == []


# ------------------------------------------------------------------ #
# Persistence across reopen/restart
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_reopen_restart_persistence(tmp_path: Any) -> None:
    first = await _Transport(tmp_path).start()
    try:
        await first.send(_private_update(101, "/new Persist"))
        await first.send(_private_update(101, "before restart"))
        active = first.store.get_active_thread(first.owner(101))
        assert active is not None
    finally:
        await first.close()

    second = await _Transport(tmp_path).start()
    try:
        result = await second.send(_private_update(101, "/current"))
        assert result["status"] == "ok"
        assert "Persist" in second.last_reply()
        await second.send(_private_update(101, "after restart"))
        contents = [m.content for m in second.store.list_messages(second.owner(101), active.id)]
        assert "before restart" in contents and "after restart" in contents
    finally:
        await second.close()


# ------------------------------------------------------------------ #
# Ownership of thread-scoped recall + beta/menu guards
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_context_record_turn_denies_foreign_owner(tmp_path: Any) -> None:
    from agency.memory.sms.store import MemoryStore

    store = ThreadStore(":memory:")
    memory = MemoryStore(str(tmp_path / "memory.db"))
    await memory.initialize()
    try:
        engine = ThreadContextEngine(store, memory)
        workspace = store.create_workspace("alice", "WS")
        thread = store.create_thread("alice", workspace.id, "T")
        with pytest.raises(KeyError):
            await engine.record_turn("bob", thread.id, "hijack", "hijack reply")
        # The legitimate owner path still works.
        await engine.record_turn("alice", thread.id, "hello", "hi")
    finally:
        await memory.close()
        store.close()


@pytest.mark.asyncio
async def test_beta_thread_path_stays_closed(tmp_path: Any) -> None:
    pair = await _Transport(tmp_path, beta=True).start()
    try:
        result = await pair.send(_private_update(101, "/new Alpha"))
        assert result["status"] == "rejected"
        pair.butler.handle_message.assert_not_awaited()
        pair.handler._adapter.send_message.assert_not_awaited()
    finally:
        await pair.close()


def test_thread_menu_scoped_to_nonbeta() -> None:
    plain = object.__new__(TelegramBot)
    plain._config = TelegramConfig(bot_token="test", beta_mode=False)
    names = {c["command"] for c in plain.commands_for_mode()}
    assert {"new", "threads", "switch", "current"} <= names

    beta = object.__new__(TelegramBot)
    beta._config = TelegramConfig(bot_token="test", beta_mode=True, allowed_user_ids=[101])
    beta_names = {c["command"] for c in beta.commands_for_mode()}
    assert not ({"new", "threads", "switch", "current"} & beta_names)
    assert "propose_agent" not in beta_names and "proposals" not in beta_names


@pytest.mark.asyncio
async def test_existing_system_commands_unchanged(transport: _Transport) -> None:
    result = await transport.send(_private_update(101, "/start"))
    assert result == {"status": "ok", "chat_id": 101, "command": "/start"}
    result = await transport.send(_private_update(101, "/status"))
    assert result["status"] == "ok" and result["command"] == "/status"
    # Thread commands did not disturb the deterministic system path.
    assert transport.route_calls == []
    await transport.send(_private_update(101, "/name Atlas"))
    assert "Atlas" in transport.last_reply()
