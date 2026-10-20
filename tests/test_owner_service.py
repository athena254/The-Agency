"""Concrete owner gateway tracer bullets; fake only HTTP and storage faults."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from agency.admission.budget import OwnerBudgetAdapter
from agency.admission.models import OwnerPrincipal, OwnerTaskRequest
from agency.admission.store import OwnerPilotStore
from agency.agents.assessment import output_digest
from agency.butler.service import ButlerService
from agency.butler.threads import ThreadStore
from agency.kernel.audit import BetaAuditLog
from agency.llm.adapter import LLMAdapter
from agency.llm.config import LLMConfig, ProviderKind
from agency.orchestrator import AgencyOrchestrator
from agency.telegram.budget_store import BetaBudgetStore


@pytest.fixture
async def concrete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    owner = OwnerPrincipal("owner", "local", "subject")
    store = OwnerPilotStore(str(tmp_path / "owner.db"))
    threads = ThreadStore(str(tmp_path / "owner.db"))
    backend = BetaBudgetStore(str(tmp_path / "quota.db"))
    budget = OwnerBudgetAdapter(backend, store)
    audit = BetaAuditLog(tmp_path / "audit.db")
    config = LLMConfig(
        provider=ProviderKind.OPENAI,
        model="synthetic-model",
        api_key=SecretStr("SYNTHETIC_KEY"),
        base_url="https://provider.invalid/v1",
        timeout=10.0,
    )
    orch = AgencyOrchestrator(
        llm=LLMAdapter(provider="echo", model="echo"), memory_db_path=":memory:"
    )
    butler = ButlerService(orchestrator=orch, thread_store=threads)
    replies = [
        '{"action":{"name":"calculator","args":{"expression":"2+3"}}}',
        '{"final":"five"}',
    ]
    requests = []
    client = httpx.AsyncClient

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": replies.pop(0)}}]})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handle), **kw)
    )
    service = None
    try:
        yield locals()
    finally:
        await audit.close()
        await budget.close()
        await store.close()
        threads.close()


def make_service(c):
    module = importlib.import_module("agency.admission.service")
    service = module.OwnerPilotService(
        butler=c["butler"],
        store=c["store"],
        budget=c["budget"],
        audit=c["audit"],
        provider_config=c["config"],
        owner_id="owner",
    )
    c["service"] = service
    return service


async def terminal(service, owner, task_id):
    async with asyncio.timeout(10):
        while True:
            view = await service.get_task(owner, task_id)
            if view.state not in {"ADMITTED", "RUNNING"}:
                return view
            await asyncio.sleep(0.005)


async def test_real_calculator_turn_is_bound_and_atomically_published(concrete):
    assert importlib.util.find_spec("agency.admission.service") is not None, "owner service missing"
    c = concrete
    service = make_service(c)
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        payload = OwnerTaskRequest(
            request_id="turn", thread_id=thread.id, message="  calculate 2+3\r\n"
        )
        admitted = await service.submit(c["owner"], payload)
        result = await terminal(service, c["owner"], admitted.task_id)
        assert result.state == "COMPLETED"
        assert result.response == "five"
        assert result.assessment.task_id == admitted.task_id
        assert result.assessment.output_sha256 == output_digest("five")
        assert result.assessment.verification.value == "not_performed"
        assert result.assessment.subtasks[0].checks[0].reason == "tool_path_unchecked"
        assert result.assessment.independently_verified is False
        assert result.usage.model_attempts == 2 and result.usage.tool_attempts == 1
        messages = await service.list_messages(c["owner"], thread.id)
        assert [(m.role, m.content) for m in messages] == [
            ("user", payload.message),
            ("assistant", "five"),
        ]
        assert len(c["requests"]) == 2
        assert "succeeded" in json.loads(c["requests"][1].content)["messages"][0]["content"]
        assert not c["butler"]._started and not c["orch"]._started
        assert c["orch"]._lattice is None and not c["orch"]._identity_registry.list_agents()
        assert c["orch"]._memory_store._conn is None
        assert not await c["audit"].has_pending_calls()
    finally:
        await service.stop()


async def test_stop_revokes_dispatch_before_waiting_for_the_admission_lock(concrete, monkeypatch):
    c = concrete
    service = make_service(c)
    entered, release = asyncio.Event(), asyncio.Event()
    client = c["client"]
    original = c["handle"]

    async def held(request):
        entered.set()
        await release.wait()
        return await original(request)

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(held), **kw)
    )
    stopping = None
    locked = False
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        task = await service.submit(
            c["owner"], OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="prompt")
        )
        await entered.wait()
        await service._gate.acquire()
        locked = True
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(0)
        release.set()
        async with asyncio.timeout(3):
            await asyncio.shield(service._execution)
        row = await c["store"].get(c["owner"], task.task_id)
        assert row.state == "UNCERTAIN" and row.tool_attempts == 0
        assert len(c["requests"]) == 1
    finally:
        release.set()
        if locked:
            service._gate.release()
        if stopping is not None:
            await stopping
        await service.stop()


async def test_shutdown_inspection_failure_still_closes_only_owned_resources(concrete, monkeypatch):
    from agency.admission.models import PilotError

    c = concrete
    service = make_service(c)
    await service.start()
    _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
    task = await service.submit(
        c["owner"], OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="prompt")
    )
    await asyncio.shield(service._execution)
    assert (await service.get_task(c["owner"], task.task_id)).state == "COMPLETED"

    async def fail():
        raise OSError("PRIVATE_INSPECTION_FAILURE")

    monkeypatch.setattr(c["audit"], "has_pending_calls", fail)
    with pytest.raises(PilotError) as unavailable:
        await service.stop()
    assert unavailable.value.code == "audit"
    assert not c["store"]._ready and c["backend"]._closed
    assert c["threads"].get_thread("owner", thread.id).id == thread.id
    await service.stop()


async def test_explicit_workspace_cannot_silently_select_another_workspace(concrete):
    from agency.admission.models import PilotError

    c = concrete
    module = importlib.import_module("agency.admission.service")
    service = module.OwnerPilotService(
        butler=c["butler"],
        store=c["store"],
        budget=c["budget"],
        audit=c["audit"],
        provider_config=c["config"],
        owner_id="owner",
        workspace_id="not-the-owner-workspace",
    )
    try:
        await service.start()
        with pytest.raises(PilotError) as denied:
            await service.create_thread(c["owner"], "thread", "Synthetic")
        assert denied.value.code == "forbidden"
        assert await c["store"].list_threads(c["owner"]) == [] and c["requests"] == []
    finally:
        await service.stop()


async def test_uncertainty_is_rechecked_after_admission_before_any_dispatch(concrete, monkeypatch):
    c = concrete
    service = make_service(c)
    original = c["store"].admit
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")

        async def interrupted(*a, **kw):
            row = await original(*a, **kw)
            old = await original(
                c["owner"],
                "interrupted",
                operation="turn",
                payload={"thread_id": thread.id, "message": "old"},
            )
            await c["store"].transition(
                old.sequence,
                expected="ADMITTED",
                state="UNCERTAIN",
                phase="recovery",
                error_code="uncertainty",
            )
            return row

        monkeypatch.setattr(c["store"], "admit", interrupted)
        task = await service.submit(
            c["owner"], OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="prompt")
        )
        await asyncio.shield(service._execution)
        assert (await service.get_task(c["owner"], task.task_id)).state != "COMPLETED"
        assert c["requests"] == []
        assert await service.list_messages(c["owner"], thread.id) == []
    finally:
        await service.stop()


async def test_final_only_turn_uses_only_quoted_committed_same_thread_context(concrete):
    from agency.admission.models import PilotError

    c = concrete
    service = make_service(c)
    c["replies"][:] = ['{"final":"first"}', '{"final":"second"}', '{"final":"third"}']
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        _, other = await service.create_thread(c["owner"], "other", "Other", thread.id)
        for request_id, target, message in [
            ("one", thread.id, "QUOTED_PRIVATE_CONTEXT"),
            ("two", thread.id, "followup"),
            ("three", other.id, "isolated"),
        ]:
            task = await service.submit(
                c["owner"],
                OwnerTaskRequest(request_id=request_id, thread_id=target, message=message),
            )
            assert (await terminal(service, c["owner"], task.task_id)).state == "COMPLETED"
            await asyncio.shield(service._execution)
        prompts = [json.loads(r.content)["messages"][0]["content"] for r in c["requests"]]
        assert len(prompts) == 3
        assert "Untrusted committed conversation (quoted data):" in prompts[1]
        assert "QUOTED_PRIVATE_CONTEXT" in prompts[1] and "\\n[user]" in prompts[1]
        assert "QUOTED_PRIVATE_CONTEXT" not in prompts[2]
        assert c["orch"]._memory_store._conn is None and c["orch"]._lattice is None
        stranger = OwnerPrincipal("stranger", "local", "foreign")
        with pytest.raises(PilotError) as denied:
            await service.list_threads(stranger)
        assert denied.value.code == "unauthorized"
        _, foreign = await c["store"].create_thread(stranger, "foreign", "Foreign")
        with pytest.raises(PilotError) as missing:
            await service.submit(
                c["owner"],
                OwnerTaskRequest(request_id="foreign", thread_id=foreign.id, message="prompt"),
            )
        assert missing.value.code == "not_found" and len(c["requests"]) == 3
    finally:
        await service.stop()


async def test_forbidden_after_tool_intent_is_still_durably_uncertain(concrete, monkeypatch):
    from agency.admission.models import PilotError

    c = concrete
    service = make_service(c)
    original = BetaAuditLog.claim_intent

    async def revoke_after_intent(self, entry):
        result = await original(self, entry)
        if entry.action == "tool.call":
            c["budget"].revoke(service._lease)
        return result

    monkeypatch.setattr(BetaAuditLog, "claim_intent", revoke_after_intent)
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        task = await service.submit(
            c["owner"], OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="prompt")
        )
        await asyncio.shield(service._execution)
        error = service._execution.result()
        assert isinstance(error, PilotError) and error.code == "forbidden"
        result = await service.get_task(c["owner"], task.task_id)
        assert result.state == "UNCERTAIN" and result.response is None
        assert await c["audit"].has_pending_calls() and len(c["requests"]) == 1
        assert await service.list_messages(c["owner"], thread.id) == []
    finally:
        await service.stop()


@pytest.mark.parametrize("fault", ["none", "quota", "commit", "committed_then_cancel"])
async def test_publication_order_and_terminal_commit_immutability(concrete, monkeypatch, fault):
    from agency.admission.models import PilotError

    c = concrete
    service = make_service(c)
    events = []
    stage, append, finish, commit = (
        c["store"].stage_result,
        c["audit"].append_beta,
        c["budget"].finish_request,
        c["store"].commit_turn,
    )

    async def staging(*a, **kw):
        await stage(*a, **kw)
        events.append("stage")

    async def auditing(entry):
        ack = await append(entry)
        if entry.action == "owner.gateway_result":
            assert events == ["stage"]
            events.append("audit")
        return ack

    async def finishing(lease, **kw):
        assert events == ["stage", "audit"]
        row = await c["store"].get(c["owner"], lease.task_id)
        assert row.state == "RUNNING" and row.phase == "staged"
        await finish(lease, **kw)
        events.append("finish")
        if fault == "quota":
            raise PilotError("store", uncertain=True)

    async def committing(sequence, **kw):
        assert events == ["stage", "audit", "finish"]
        assert not c["budget"].owns(service._lease)
        assert len(c["requests"]) == 2
        if fault == "commit":
            raise PilotError("store", uncertain=True)
        await commit(sequence, **kw)
        events.append("commit")
        if fault == "committed_then_cancel":
            raise asyncio.CancelledError

    monkeypatch.setattr(c["store"], "stage_result", staging)
    monkeypatch.setattr(c["audit"], "append_beta", auditing)
    monkeypatch.setattr(c["budget"], "finish_request", finishing)
    monkeypatch.setattr(c["store"], "commit_turn", committing)
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        payload = OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="prompt")
        task = await service.submit(c["owner"], payload)
        await asyncio.shield(service._execution)
        result = await service.get_task(c["owner"], task.task_id)
        successful = fault in {"none", "committed_then_cancel"}
        assert result.state == ("COMPLETED" if successful else "UNCERTAIN")
        assert len(await service.list_messages(c["owner"], thread.id)) == (2 if successful else 0)
        assert (await service.submit(c["owner"], payload)).replayed
        if successful:
            assert (
                await service.cancel(c["owner"], task.task_id, "late-cancel")
            ).state == "COMPLETED"
        assert len(c["requests"]) == 2
    finally:
        await service.stop()


async def test_pending_audit_after_gateway_ack_never_publishes_success(concrete, monkeypatch):
    from agency.kernel.audit import AuditEntry

    c = concrete
    service = make_service(c)
    original = c["audit"].append_beta

    async def interrupt(entry):
        ack = await original(entry)
        if entry.action == "owner.gateway_result":
            pending = AuditEntry(
                action="model.generate",
                result="pending",
                evidence={"phase": "intent", "call_id": "synthetic-interruption"},
            )
            assert await c["audit"].claim_intent(pending)
        return ack

    monkeypatch.setattr(c["audit"], "append_beta", interrupt)
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        task = await service.submit(
            c["owner"], OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="prompt")
        )
        result = await terminal(service, c["owner"], task.task_id)
        assert result.state == "UNCERTAIN" and result.response is None
        assert await service.list_messages(c["owner"], thread.id) == []
        assert await c["audit"].has_pending_calls()
    finally:
        await service.stop()


async def test_startup_fault_releases_owned_stores_without_legacy_start_or_close(
    concrete, monkeypatch
):
    from agency.admission.models import PilotError

    c = concrete
    service = make_service(c)

    async def fail():
        raise OSError("PRIVATE_STARTUP_FAILURE")

    monkeypatch.setattr(c["audit"], "initialize", fail)
    with pytest.raises(PilotError) as unavailable:
        await service.start()
    assert unavailable.value.code == "audit"
    assert not c["store"]._ready and c["backend"]._closed
    assert c["threads"]._conn is not None
    assert not c["butler"]._started and not c["orch"]._started
    replacement = OwnerPilotStore(c["store"]._db_path)
    try:
        await replacement.initialize()
    finally:
        await replacement.close()
        await service.stop()


async def test_audit_inspection_failure_blocks_execution_not_read_only_history(
    concrete, monkeypatch
):
    from agency.admission.models import PilotError

    c = concrete
    service = make_service(c)
    broken = False
    original = c["audit"].has_pending_calls
    client = c["client"]

    async def inspect():
        if broken:
            raise OSError("PRIVATE_AUDIT_FAILURE")
        return await original()

    async def failed(request):
        nonlocal broken
        broken = True
        raise httpx.ReadError("PRIVATE_PROVIDER_FAILURE")

    monkeypatch.setattr(c["audit"], "has_pending_calls", inspect)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(failed), **kw)
    )
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        task = await service.submit(
            c["owner"], OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="prompt")
        )
        await asyncio.shield(service._execution)
        result = await service.get_task(c["owner"], task.task_id)
        assert result.state == "UNCERTAIN" and result.response is None
        assert len(await service.list_tasks(c["owner"])) == 1
        with pytest.raises(PilotError) as unavailable:
            await service.capabilities(c["owner"])
        assert unavailable.value.code == "audit"
        broken = False
        assert (await service.capabilities(c["owner"])).uncertainty_blocked
    finally:
        broken = False
        await service.stop()


async def test_lowered_model_cap_finishes_known_failure_without_second_dispatch(concrete):
    from agency.telegram.budget_store import BudgetLimits

    c = concrete
    c["backend"]._limits = BudgetLimits(model_calls_per_request=1)
    service = make_service(c)
    try:
        await service.start()
        caps = await service.capabilities(c["owner"])
        assert caps.limits.model_calls_per_request == 1
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        task = await service.submit(
            c["owner"], OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="prompt")
        )
        result = await terminal(service, c["owner"], task.task_id)
        assert result.state == "FAILED" and result.error_code == "quota"
        assert result.usage.model_attempts == 1 and len(c["requests"]) == 1
        assert result.response is None and await service.list_messages(c["owner"], thread.id) == []
        row = await c["store"].get(c["owner"], task.task_id)
        assert await c["backend"].inspect_reservation("owner-pilot:v1", row.sequence) == "FAILED"
    finally:
        await service.stop()


async def test_admitted_deadline_cancels_the_actual_provider_without_extending_it(
    concrete, monkeypatch
):
    import sqlite3
    import time

    c = concrete
    service = make_service(c)
    original = c["store"].admit
    entered, cancelled = asyncio.Event(), asyncio.Event()
    client = c["client"]

    async def nearly_expired(*a, **kw):
        row = await original(*a, **kw)
        # Explicit persistence/clock fault: consume 119s of the actual durable deadline.
        conn = sqlite3.connect(c["store"]._db_path)
        try:
            with conn:
                conn.execute(
                    "UPDATE pilot_requests SET created_at=? WHERE sequence=?",
                    (time.time() - 119.0, row.sequence),
                )
        finally:
            conn.close()
        return await c["store"].get(c["owner"], row.task_id)

    async def held(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(c["store"], "admit", nearly_expired)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(held), **kw)
    )
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        task = await service.submit(
            c["owner"], OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="prompt")
        )
        async with asyncio.timeout(3):
            await entered.wait()
            await cancelled.wait()
            await asyncio.shield(service._execution)
        result = await service.get_task(c["owner"], task.task_id)
        assert result.state == "UNCERTAIN" and result.response is None
        assert await c["audit"].has_pending_calls()
    finally:
        await service.stop()


async def test_gateway_ack_failure_keeps_staged_output_private_and_blocks_dispatch(
    concrete, monkeypatch
):
    c = concrete
    service = make_service(c)
    original = c["audit"].append_beta
    observed = []

    async def fail_gateway(entry):
        if entry.action == "owner.gateway_result":
            rows = await c["store"].list_tasks(c["owner"])
            assert rows[0].phase == "staged" and rows[0].state == "RUNNING"
            assert (await service.get_task(c["owner"], rows[0].task_id)).response is None
            observed.append(entry)
            raise OSError("PRIVATE_STORAGE_FAILURE")
        return await original(entry)

    monkeypatch.setattr(c["audit"], "append_beta", fail_gateway)
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        task = await service.submit(
            c["owner"], OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="prompt")
        )
        result = await terminal(service, c["owner"], task.task_id)
        assert result.state == "UNCERTAIN" and result.response is None and result.assessment is None
        row = await c["store"].get(c["owner"], task.task_id)
        assert row.result_json is not None and "five" in row.result_json
        assert await service.list_messages(c["owner"], thread.id) == []
        assert len(observed) == 1 and len(c["requests"]) == 2
        assert not await c["audit"].has_pending_calls()
        assert (await service.capabilities(c["owner"])).uncertainty_blocked
        assert not c["budget"].owns(service._lease)
    finally:
        await service.stop()


async def test_shutdown_cancels_and_drains_even_when_stop_waiter_is_cancelled(
    concrete, monkeypatch
):
    from agency.admission.models import PilotError

    c = concrete
    service = make_service(c)
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    client = c["client"]

    async def held(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
            raise

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(held), **kw)
    )
    try:
        await service.start()
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        task = await service.submit(
            c["owner"], OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="prompt")
        )
        await entered.wait()
        stopping = asyncio.create_task(service.stop())
        async with asyncio.timeout(2):
            await cancelled.wait()
        stopping.cancel()
        await asyncio.sleep(0)
        assert not stopping.done() and c["store"]._ready
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        assert service._execution.done() and not c["store"]._ready
        assert c["threads"].get_thread("owner", thread.id).id == thread.id
        with pytest.raises(PilotError):
            await service.start()
        reopened = OwnerPilotStore(c["store"]._db_path)
        try:
            await reopened.initialize()
            assert (await reopened.get(c["owner"], task.task_id)).state == "UNCERTAIN"
        finally:
            await reopened.close()
    finally:
        release.set()
        await service.stop()


async def test_cancelled_admission_is_retained_and_immutable_until_scheduled(concrete, monkeypatch):
    c = concrete
    service = make_service(c)
    entered, release = asyncio.Event(), asyncio.Event()
    original = c["store"].admit

    async def held(*a, **kw):
        row = await original(*a, **kw)
        entered.set()
        await release.wait()
        return row

    monkeypatch.setattr(c["store"], "admit", held)
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        payload = OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="ORIGINAL")
        caller = asyncio.create_task(service.submit(c["owner"], payload))
        await entered.wait()
        payload.message = "MUTATED"
        object.__setattr__(c["config"], "model", "MUTATED_MODEL")
        caller.cancel()
        await asyncio.sleep(0)
        assert not caller.done(), "cancelled admission abandoned a persisted reservation"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
        row = await c["store"].get_request(c["owner"], "turn")
        assert row.task_id is not None
        result = await terminal(service, c["owner"], row.task_id)
        assert result.state == "COMPLETED"
        wire = json.loads(c["requests"][0].content)
        assert wire["model"] == "synthetic-model"
        assert (
            "Task: ORIGINAL" in wire["messages"][0]["content"]
            and "MUTATED" not in wire["messages"][0]["content"]
        )
        assert (await service.list_messages(c["owner"], thread.id))[0].content == "ORIGINAL"
    finally:
        release.set()
        await service.stop()


@pytest.mark.parametrize("cancel_waiter", [False, True])
async def test_cancel_drains_real_provider_child_and_preserves_pending_audit(
    concrete, monkeypatch, cancel_waiter
):
    from agency.admission.models import PilotError
    from agency.llm.metered import MeteredError

    c = concrete
    service = make_service(c)
    entered, cancelled, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
    client = c["client"]

    async def held(request):
        c["requests"].append(request)
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await drained.wait()
            raise

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(held), **kw)
    )
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        payload = OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="prompt")
        task = await service.submit(c["owner"], payload)
        await entered.wait()
        command = asyncio.create_task(service.cancel(c["owner"], task.task_id, "cancel"))
        async with asyncio.timeout(2):
            await cancelled.wait()
        assert not command.done(), "cancel reported completion before actual child drain"
        if cancel_waiter:
            command.cancel()
            await asyncio.sleep(0)
            assert not command.done(), "cancelled cancel waiter abandoned its child"
        drained.set()
        if cancel_waiter:
            with pytest.raises(asyncio.CancelledError):
                await command
            result = await service.get_task(c["owner"], task.task_id)
        else:
            result = await command
        assert result.state == "UNCERTAIN" and result.cancel_requested and result.response is None
        assert isinstance(service._execution.result(), MeteredError)
        assert await c["audit"].has_pending_calls() and len(c["requests"]) == 1
        assert (await service.cancel(c["owner"], task.task_id, "cancel")).replayed
        with pytest.raises(PilotError) as blocked:
            await service.submit(c["owner"], payload.model_copy(update={"request_id": "new"}))
        assert blocked.value.code == "uncertainty"
        assert await service.list_messages(c["owner"], thread.id) == []
    finally:
        drained.set()
        await service.stop()


async def test_recovery_blocks_new_execution_but_preserves_owner_history(concrete):
    from agency.admission.models import PilotError

    c = concrete
    await c["store"].initialize()
    _, thread = await c["store"].create_thread(c["owner"], "thread", "Synthetic")
    interrupted = await c["store"].admit(
        c["owner"],
        "old",
        operation="turn",
        payload={"thread_id": thread.id, "message": "interrupted"},
    )
    service = make_service(c)
    try:
        await service.start()
        caps = await service.capabilities(c["owner"])
        assert caps.enabled and caps.ready and caps.provider_ready and caps.uncertainty_blocked
        assert caps.tools == ["calculator", "utc_time", "web_search"]
        assert caps.limits.model_calls_per_request == 8 and caps.limits.tool_calls_per_request == 4
        assert {"shell", "files", "web_fetch", "memory", "governance"} <= set(caps.disabled)
        assert (await service.get_task(c["owner"], interrupted.task_id)).state == "UNCERTAIN"
        assert len(await service.list_tasks(c["owner"])) == 1
        assert (await service.list_threads(c["owner"]))[0].id == thread.id
        with pytest.raises(PilotError) as blocked:
            await service.submit(
                c["owner"], OwnerTaskRequest(request_id="new", thread_id=thread.id, message="new")
            )
        assert blocked.value.code == "uncertainty"
        assert c["requests"] == [] and len(await service.list_tasks(c["owner"])) == 1
    finally:
        await service.stop()


async def test_provider_failure_is_uncertain_private_and_never_retried(concrete, monkeypatch):
    c = concrete
    service = make_service(c)
    client = c["client"]
    requests = c["requests"]

    async def failed(request):
        requests.append(request)
        return httpx.Response(503, text="PRIVATE_PROVIDER_FAILURE")

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(failed), **kw)
    )
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        payload = OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="prompt")
        task = await service.submit(c["owner"], payload)
        result = await terminal(service, c["owner"], task.task_id)
        assert result.state == "UNCERTAIN" and result.uncertain
        assert result.response is None and result.assessment is None
        assert result.error_code == "uncertainty"
        assert len(requests) == 1 and await c["audit"].has_pending_calls()
        assert await service.list_messages(c["owner"], thread.id) == []
        replay = await service.submit(c["owner"], payload)
        assert replay.replayed and replay.state == "UNCERTAIN" and len(requests) == 1
    finally:
        await service.stop()


async def test_one_execution_replays_duplicates_but_refuses_a_second_turn(concrete, monkeypatch):
    from agency.admission.models import PilotError

    c = concrete
    service = make_service(c)
    entered, release = asyncio.Event(), asyncio.Event()
    original = c["handle"]

    async def held(request):
        entered.set()
        await release.wait()
        return await original(request)

    client = c["client"]
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(held), **kw)
    )
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        payload = OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="calculate")
        first, duplicate = await asyncio.gather(
            service.submit(c["owner"], payload), service.submit(c["owner"], payload)
        )
        assert first.task_id == duplicate.task_id and first.replayed != duplicate.replayed
        await entered.wait()
        with pytest.raises(PilotError) as busy:
            await service.submit(c["owner"], payload.model_copy(update={"request_id": "second"}))
        assert busy.value.code == "conflict"
        assert len(await c["store"].list_tasks(c["owner"])) == 1
        with pytest.raises(PilotError) as changed:
            await service.submit(c["owner"], payload.model_copy(update={"message": "changed"}))
        assert changed.value.code == "conflict"
        release.set()
        result = await terminal(service, c["owner"], first.task_id)
        assert result.state == "COMPLETED"
        replay = await service.submit(c["owner"], payload)
        assert replay.replayed and replay.task_id == first.task_id and replay.response == "five"
        assert len(c["requests"]) == 2
    finally:
        release.set()
        await service.stop()
