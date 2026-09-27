"""Typed principal plumbing only; no Telegram provenance or beta execution grant."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agency.agents.executor import ExecutionResult, ExecutionStatus
from agency.butler.service import ButlerService
from agency.kernel.identity import Agent
from agency.memory.sms.store import MemoryStore
from agency.orchestrator import AgencyOrchestrator
from agency.telegram.config import TelegramConfig
from agency.telegram.handler import TelegramHandler
from agency.tools.base import BetaPrincipal


class _TaskStub:
    def __init__(self) -> None:
        self.submit_task = AsyncMock(
            return_value=type("Task", (), {"task_id": "t", "title": "t"})()
        )
        self.execute_task = AsyncMock(
            return_value=ExecutionResult(task_id="t", status=ExecutionStatus.COMPLETED, output="ok")
        )


@pytest.mark.asyncio
async def test_explicit_principal_rejected_before_butler_creates_task() -> None:
    principal = BetaPrincipal(telegram_user_id=101, private_chat=True)
    stub = _TaskStub()
    service = ButlerService(orchestrator=stub, memory_store=MemoryStore(":memory:"))  # type: ignore[arg-type]
    agent = Agent(name="research", domain="research", capabilities=["research"])
    with pytest.raises(RuntimeError, match="beta execution disabled"):
        await service.execute(agent, "topic", {"sender": "telegram:101"}, beta_principal=principal)
    stub.submit_task.assert_not_awaited()
    stub.execute_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_and_sender_cannot_mint_principal() -> None:
    fake = BetaPrincipal(telegram_user_id=999, private_chat=True)
    stub = _TaskStub()
    service = ButlerService(orchestrator=stub, memory_store=MemoryStore(":memory:"))  # type: ignore[arg-type]
    agent = Agent(name="general", domain="general", capabilities=["general"])
    context: dict[str, Any] = {
        "sender": "telegram:999",
        "beta_principal": fake,
        "metadata": {"beta_principal": fake, "telegram_user_id": 999},
    }
    await service.execute(agent, "hello", context)
    assert "beta_principal" not in stub.execute_task.await_args.kwargs


@pytest.mark.asyncio
async def test_handle_message_does_not_promote_context_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = BetaPrincipal(telegram_user_id=999, private_chat=True)
    stub = _TaskStub()
    service = ButlerService(orchestrator=stub, memory_store=MemoryStore(":memory:"))  # type: ignore[arg-type]
    service._started = True
    agent = Agent(name="general", domain="general", capabilities=["general"])
    monkeypatch.setattr(service, "route", AsyncMock(return_value=agent))
    monkeypatch.setattr(service, "_audit_append", AsyncMock())
    assert (
        await service.handle_message(
            "hello", "telegram:999", {"beta_principal": principal}, memory_enabled=False
        )
        == "ok"
    )
    assert "beta_principal" not in stub.execute_task.await_args.kwargs


@pytest.mark.asyncio
async def test_handle_message_rejects_typed_principal_before_start_memory_routing_or_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = BetaPrincipal(telegram_user_id=999, private_chat=True)
    stub = _TaskStub()
    model = AsyncMock(return_value="general")
    memory = MemoryStore(":memory:")
    service = ButlerService(orchestrator=stub, memory_store=memory, llm=model)  # type: ignore[arg-type]
    startup = AsyncMock()
    recall = AsyncMock()
    store_turn = AsyncMock()
    routing = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(service, "_ensure_started", startup)
    monkeypatch.setattr(service, "_recall_history", recall)
    monkeypatch.setattr(service, "_store_turn", store_turn)
    monkeypatch.setattr(service, "route", routing)
    monkeypatch.setattr(service, "_audit_append", audit)
    with pytest.raises(RuntimeError, match="beta execution disabled"):
        await service.handle_message(
            "hello", "telegram:999", {"beta_principal": principal}, beta_principal=principal
        )
    startup.assert_not_awaited()
    recall.assert_not_awaited()
    store_turn.assert_not_awaited()
    routing.assert_not_awaited()
    model.assert_not_awaited()
    audit.assert_not_awaited()
    stub.submit_task.assert_not_awaited()
    stub.execute_task.assert_not_awaited()


def test_orchestrator_tool_context_requires_explicit_principal() -> None:
    orch = AgencyOrchestrator(memory_db_path=":memory:")
    fake = BetaPrincipal(telegram_user_id=101, private_chat=True)
    assert orch._tool_context("a", "t").beta_principal is None
    assert orch._tool_context("a", "t", beta_principal=fake).beta_principal is fake
    with pytest.raises(TypeError):
        orch._tool_context("a", "t", beta_principal="telegram:101")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_typed_principal_cannot_execute_on_legacy_open_registry() -> None:
    orch = AgencyOrchestrator(memory_db_path=":memory:")
    principal = BetaPrincipal(telegram_user_id=101, private_chat=True)
    orch._task_manager.get_task = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="beta execution disabled"):
        await orch.execute_task("task", beta_principal=principal)
    orch._task_manager.get_task.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["update", "message", "research"])
async def test_standalone_telegram_dict_cannot_mint_identity(path: str) -> None:
    """A direct handle_update call is not authenticated network provenance."""
    principal = BetaPrincipal(telegram_user_id=101, private_chat=True)
    cfg = TelegramConfig(bot_token="test", beta_mode=True, allowed_user_ids=[101])
    butler = AsyncMock()
    butler.handle_message.return_value = "ok"
    butler.orchestrator.list_agents.return_value = [
        Agent(name="research", domain="research", capabilities=["research"])
    ]
    butler.orchestrator.submit_task.return_value = type("Task", (), {"task_id": "t"})()
    butler.orchestrator.execute_task.return_value = type(
        "Result", (), {"status": "completed", "output": "ok"}
    )()
    handler = TelegramHandler(cfg, butler=butler)
    handler._adapter.send_message = AsyncMock()  # type: ignore[method-assign]
    message: dict[str, Any] = {
        "from": {"id": 101},
        "chat": {"id": 101, "type": "private"},
        "text": "/research topic" if path == "research" else "hello",
        "beta_principal": principal,
        "metadata": {"beta_principal": principal},
    }
    if path == "message":
        await handler.process_message(message)
    else:
        await handler.handle_update(
            {"update_id": 123, "message": message, "beta_principal": principal}
        )
    if path == "research":
        assert "beta_principal" not in butler.orchestrator.execute_task.await_args.kwargs
    else:
        assert "beta_principal" not in butler.handle_message.await_args.kwargs
    await handler.close()
