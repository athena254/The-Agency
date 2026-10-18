"""A3.1 gateway audit regressions: actual services, isolated stores, synthetic leaves."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from agency.butler.config import ButlerConfig
from agency.butler.service import ButlerService
from agency.kernel.audit import AuditLog
from agency.llm.adapter import LLMAdapter
from agency.orchestrator import AgencyOrchestrator


@pytest_asyncio.fixture
async def service(tmp_path: Path) -> AsyncIterator[ButlerService]:
    audit = AuditLog(tmp_path / "gateway-audit.db")
    model = LLMAdapter(provider="echo", model="echo")
    orchestrator = AgencyOrchestrator(llm=model, memory_db_path=str(tmp_path / "memory.db"))
    gateway = ButlerService(
        config=ButlerConfig(llm_provider="none"), orchestrator=orchestrator, audit_log=audit
    )
    await gateway.start()
    try:
        yield gateway
    finally:
        await gateway.stop()
        await audit.close()


async def test_required_audit_exception_is_fixed_and_not_retried(
    service: ButlerService, monkeypatch: pytest.MonkeyPatch
) -> None:
    append = AsyncMock(side_effect=OSError("SYNTHETIC_PRIVATE_STORAGE_BODY"))
    monkeypatch.setattr(service._audit, "append", append)
    with pytest.raises(RuntimeError, match="^butler audit unavailable$") as caught:
        await service._audit_append(agent=None, action="butler.request", result="received")
    assert type(caught.value).__name__ == "ButlerAuditError"
    assert caught.value.__suppress_context__ is True
    append.assert_awaited_once()


@pytest.mark.parametrize("ack", [None, False, True, object(), "foreign-entry-id"])
async def test_invalid_acknowledgement_is_not_accepted(
    service: ButlerService, monkeypatch: pytest.MonkeyPatch, ack: object
) -> None:
    append = AsyncMock(return_value=ack)
    monkeypatch.setattr(service._audit, "append", append)
    with pytest.raises(RuntimeError, match="^butler audit unavailable$"):
        await service._audit_append(agent=None, action="butler.request", result="received")
    append.assert_awaited_once()


@pytest.mark.parametrize("path", ["routed", "isolated", "route", "execute", "command"])
async def test_preflight_failure_prevents_work(
    service: ButlerService, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    routing = AsyncMock(return_value="general")
    monkeypatch.setattr(service, "_llm", routing)
    model = service.orchestrator._llm_adapter
    dispatch = MagicMock(wraps=model._echo_generate)
    monkeypatch.setattr(model, "_echo_generate", dispatch)
    recall = AsyncMock(return_value="")
    store = AsyncMock()
    command = MagicMock(wraps=service.commands.execute)
    monkeypatch.setattr(service, "_recall_history", recall)
    monkeypatch.setattr(service, "_store_turn", store)
    monkeypatch.setattr(service.commands, "execute", command)
    monkeypatch.setattr(service._audit, "append", AsyncMock(side_effect=OSError("fixture")))
    tasks_before = len(await service.orchestrator.list_tasks())
    with pytest.raises(RuntimeError, match="^butler audit unavailable$"):
        if path == "routed":
            await service.handle_message("hello fixture", "fixture-owner", {})
        elif path == "isolated":
            await service.handle_isolated_message("hello fixture", "fixture-owner")
        elif path == "route":
            await service.route("hello fixture", {})
        elif path == "execute":
            agent = next(a for a in service.router.list_agents() if a.domain == "general")
            await service.execute(agent, "hello fixture", {})
        else:
            await service.handle_message("/new fixture", "fixture-owner", {}, commands_enabled=True)
    routing.assert_not_awaited()
    dispatch.assert_not_called()
    recall.assert_not_awaited()
    store.assert_not_awaited()
    command.assert_not_called()
    assert len(await service.orchestrator.list_tasks()) == tasks_before


@pytest.mark.parametrize("threaded", [False, True])
async def test_final_gateway_ack_precedes_conversation_persistence(
    service: ButlerService, monkeypatch: pytest.MonkeyPatch, threaded: bool
) -> None:
    owner = "fixture-owner"
    context = {}
    thread = None
    if threaded:
        workspace = service.create_workspace(owner, "fixture")
        thread = service.create_thread(owner, workspace.id, "fixture")
        context["thread_id"] = thread.id
    dispatch = MagicMock(return_value='{"final":"SYNTHETIC_FIXTURE_OUTPUT"}')
    monkeypatch.setattr(service.orchestrator._llm_adapter, "_echo_generate", dispatch)
    monkeypatch.setattr(service, "_llm", AsyncMock(return_value="general"))
    store = AsyncMock(wraps=service._store_turn)
    monkeypatch.setattr(service, "_store_turn", store)
    append = service._audit.append

    async def fail_final(entry):
        if entry.action == "butler.handle_message":
            raise OSError("fixture final failure")
        return await append(entry)

    monkeypatch.setattr(service._audit, "append", fail_final)
    with pytest.raises(RuntimeError, match="^butler audit unavailable$"):
        await service.handle_message("hello fixture", owner, context)
    dispatch.assert_called_once()
    store.assert_not_awaited()
    if thread is not None:
        assert service.list_thread_messages(owner, thread.id) == []


@pytest.mark.parametrize("error_kind", ["audit", "denied", "unknown"])
@pytest.mark.parametrize("path", ["routing", "execution", "isolated_execution"])
async def test_blocking_errors_never_fall_back_or_become_a_reply(
    service: ButlerService, monkeypatch: pytest.MonkeyPatch, error_kind: str, path: str
) -> None:
    from agency.butler.service import ButlerAuditError
    from agency.llm.metered import MeteredDenied, MeteredError

    error = (
        ButlerAuditError()
        if error_kind == "audit"
        else MeteredDenied("SYNTHETIC_PRIVATE_DENIAL")
        if error_kind == "denied"
        else MeteredError("SYNTHETIC_PRIVATE_UNKNOWN")
    )
    failing = AsyncMock(side_effect=error)
    fallback = AsyncMock(wraps=service._router.route)
    store = AsyncMock()
    monkeypatch.setattr(service._router, "route", fallback)
    monkeypatch.setattr(service, "_store_turn", store)
    if path == "routing":
        monkeypatch.setattr(service, "_llm", failing)
    elif path == "execution":
        monkeypatch.setattr(service, "execute", failing)
    else:
        monkeypatch.setattr(service.orchestrator, "converse_isolated", failing)
    with pytest.raises(type(error)) as caught:
        if path == "isolated_execution":
            await service.handle_isolated_message("hello fixture", "fixture-owner")
        else:
            await service.handle_message("hello fixture", "fixture-owner", {})
    assert caught.value is error
    failing.assert_awaited_once()
    store.assert_not_awaited()
    if path == "routing":
        fallback.assert_not_awaited()


@pytest.mark.parametrize("path", ["routed", "isolated", "command", "direct_execute"])
async def test_gateway_audit_contains_no_private_bodies_or_sender(
    service: ButlerService, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, path: str
) -> None:
    markers = ("PRIVATE_INPUT_A31", "PRIVATE_OUTPUT_A31", "PRIVATE_SENDER_A31")
    monkeypatch.setattr(
        service.orchestrator._llm_adapter,
        "_echo_generate",
        MagicMock(return_value='{"final":"PRIVATE_OUTPUT_A31"}'),
    )
    monkeypatch.setattr(service, "_llm", AsyncMock(return_value="general"))
    if path == "routed":
        await service.handle_message(markers[0], markers[2], {})
    elif path == "isolated":
        await service.handle_isolated_message(markers[0], markers[2])
    elif path == "command":
        await service.handle_message("/new " + markers[0], markers[2], {}, commands_enabled=True)
    else:
        agent = next(a for a in service.router.list_agents() if a.domain == "general")
        await service.execute(agent, markers[0], {"sender": markers[2]})
    reader = AuditLog(tmp_path / "gateway-audit.db")
    await reader.initialize()
    try:
        rows = await reader.query()
        assert rows
        serialized = "\n".join(row.model_dump_json() for row in rows)
        for marker in markers:
            assert marker not in serialized
    finally:
        await reader.close()


@pytest.mark.parametrize(
    "path",
    [
        "routing",
        "execution",
        "isolated",
        "timeout",
        "isolated_timeout",
        "recall",
        "memory",
        "truncation",
    ],
)
async def test_gateway_error_logs_exclude_private_data_and_tracebacks(
    service: ButlerService, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    from structlog.testing import capture_logs

    marker = "SYNTHETIC_PRIVATE_LOG_A31"
    error = TimeoutError(marker) if "timeout" in path else OSError(marker)
    monkeypatch.setattr(
        service.orchestrator._llm_adapter,
        "_echo_generate",
        MagicMock(return_value='{"final":"fixture"}'),
    )
    if path == "routing":
        monkeypatch.setattr(service, "_llm", AsyncMock(side_effect=error))
    elif path in ("execution", "timeout"):
        monkeypatch.setattr(service, "execute", AsyncMock(side_effect=error))
    elif path in ("isolated", "isolated_timeout"):
        monkeypatch.setattr(service.orchestrator, "converse_isolated", AsyncMock(side_effect=error))
    elif path == "recall":
        monkeypatch.setattr(
            service._memory_retrieval, "semantic_search", AsyncMock(side_effect=error)
        )
    elif path == "memory":
        monkeypatch.setattr(service._memory, "store", AsyncMock(side_effect=error))
    else:
        monkeypatch.setattr(service.config, "max_message_length", 5)
    with capture_logs() as records:
        if path == "memory":
            agent = service.router.list_agents()[0]
            await service._store_turn(marker, agent, marker, marker)
        elif path.startswith("isolated"):
            await service.handle_isolated_message(marker, marker)
        else:
            await service.handle_message(marker, marker, {})
    repaired_events = [r for r in records if r["event"].startswith("butler.")]
    assert repaired_events
    for event in repaired_events:
        assert marker not in repr(event)
        assert not event.get("exc_info")
        assert "sender" not in event


@pytest.mark.parametrize(
    "phase",
    [
        "butler.route",
        "butler.execute_intent",
        "butler.execute",
        "butler.handle_message",
        "butler.route_isolated",
        "butler.handle_isolated_message",
        "butler.command",
    ],
)
@pytest.mark.parametrize("commit_then_fail", [False, True])
async def test_late_audit_failure_has_no_success_transcript_or_retry(
    service: ButlerService, monkeypatch: pytest.MonkeyPatch, phase: str, commit_then_fail: bool
) -> None:
    from agency.butler.service import ButlerAuditError

    dispatch = MagicMock(return_value='{"final":"fixture answer"}')
    monkeypatch.setattr(service.orchestrator._llm_adapter, "_echo_generate", dispatch)
    monkeypatch.setattr(service, "_llm", AsyncMock(return_value="general"))
    store = AsyncMock(wraps=service._store_turn)
    monkeypatch.setattr(service, "_store_turn", store)
    command = MagicMock(wraps=service.commands.execute)
    monkeypatch.setattr(service.commands, "execute", command)
    append = service._audit.append
    failed = []

    async def inject(entry):
        if entry.action == phase:
            failed.append(entry.entry_id)
            if commit_then_fail:
                await append(entry)
            raise OSError("SYNTHETIC_PRIVATE_LATE_FAILURE")
        return await append(entry)

    monkeypatch.setattr(service._audit, "append", inject)
    with pytest.raises(ButlerAuditError):
        if "isolated" in phase:
            await service.handle_isolated_message("fixture", "fixture-owner")
        elif phase == "butler.command":
            await service.handle_message("/new fixture", "fixture-owner", {}, commands_enabled=True)
        else:
            await service.handle_message("fixture", "fixture-owner", {})
    assert len(failed) == 1
    store.assert_not_awaited()
    expected_dispatches = int(
        phase in ("butler.execute", "butler.handle_message", "butler.handle_isolated_message")
    )
    assert dispatch.call_count == expected_dispatches
    if phase == "butler.command":
        command.assert_called_once()
        assert service.thread_store.get_active_thread("fixture-owner") is not None
    else:
        command.assert_not_called()
    rows = await service._audit.query()
    assert sum(r.entry_id == failed[0] for r in rows) == int(commit_then_fail)


@pytest.mark.parametrize(
    "phase",
    [
        "butler.request",
        "butler.route_intent",
        "butler.execute_intent",
        "butler.execute",
        "butler.handle_message",
        "butler.handle_isolated_message",
        "butler.command",
    ],
)
async def test_inflight_audit_cancellation_stays_cancellation(
    service: ButlerService, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    import asyncio

    entered = asyncio.Event()
    release = asyncio.Event()
    append = service._audit.append
    store = AsyncMock()
    monkeypatch.setattr(service, "_store_turn", store)
    monkeypatch.setattr(
        service.orchestrator._llm_adapter,
        "_echo_generate",
        MagicMock(return_value='{"final":"fixture"}'),
    )
    calls = []

    async def block(entry):
        receipt = await append(entry)
        if entry.action == phase:
            calls.append(entry.entry_id)
            entered.set()
            await release.wait()
        return receipt

    monkeypatch.setattr(service._audit, "append", block)
    if "isolated" in phase:
        work = service.handle_isolated_message("fixture", "fixture-owner")
    else:
        work = service.handle_message(
            "/new fixture" if phase == "butler.command" else "fixture",
            "fixture-owner",
            {},
            commands_enabled=phase == "butler.command",
        )
    task = asyncio.create_task(work)
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) == 1
        store.assert_not_awaited()
        rows = await service._audit.query()
        assert sum(r.entry_id == calls[0] for r in rows) == 1
    finally:
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_unknown_command_token_is_not_persisted_in_gateway_audit(
    service: ButlerService,
) -> None:
    reply = await service.handle_message(
        "/private_command_a31 private_args_a31", "private_owner_a31", {}, commands_enabled=True
    )
    assert "Unknown command" in reply
    rows = await service._audit.query()
    assert rows
    serialized = "\n".join(row.model_dump_json() for row in rows)
    for marker in ("private_command_a31", "private_args_a31", "private_owner_a31"):
        assert marker not in serialized
