"""Controlled retained boundaries, not deliberately hung real Git subprocesses."""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from agency.admission.models import PilotError
from agency.agents.factory_coding import NativeCodingUncertain
from tests.test_factory_coding_service import coding_case as coding_case  # noqa: PLC0414 -- pytest fixture export
from tests.test_factory_coding_service import make_service, payload, settled

pytestmark = pytest.mark.asyncio


async def test_native_uncertain_without_report_is_unknown_and_locally_blocks(
    coding_case, monkeypatch
):
    c = coding_case
    c.service = service = make_service(c)
    await service.start()

    async def unknown(*a, **kw):
        raise NativeCodingUncertain("controlled lost dispatch acknowledgement")

    monkeypatch.setattr(c.executor, "execute", unknown)
    view = await service.submit_coding(c.owner, payload(c))
    row = await settled(c, view)
    assert row.state == "UNCERTAIN" and row.error_code == "uncertainty"
    assert service._blocked
    assert (await service.get_coding_receipt(c.owner, row.task_id))["receipt"] is None
    assert c.calls == []
    with pytest.raises(PilotError) as error:
        await service.submit_coding(c.owner, payload(c, "next"))
    assert error.value.code == "uncertainty"


async def test_cancel_observes_exact_retained_uncertainty_ack_before_fallback(
    coding_case, monkeypatch
):
    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    entered, release, ack_entered, ack_release = (asyncio.Event() for _ in range(4))
    client = c.http_client
    original = service._store.mark_coding_uncertain
    marks = []

    async def held_http(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"choices": [{"message": {"content": c.response}}]})

    async def held_ack(*a, **kw):
        marks.append(kw)
        ack_entered.set()
        await ack_release.wait()
        return await original(*a, **kw)

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(held_http), **kw)
    )
    monkeypatch.setattr(service._store, "mark_coding_uncertain", held_ack)
    cancelling = None
    try:
        view = await service.submit_coding(c.owner, payload(c))
        await asyncio.wait_for(entered.wait(), 10)
        cancelling = asyncio.create_task(service.cancel(c.owner, view.task_id, "cancel"))
        await asyncio.wait_for(ack_entered.wait(), 3)
        await asyncio.sleep(0.03)
        assert not service._execution.done(), (
            "must not race generic root transition ahead of retained hook"
        )
        assert (await service._store.get(c.owner, view.task_id)).state == "RUNNING"
        ack_release.set()
        cancelled = await asyncio.wait_for(cancelling, 3)
        assert cancelled.state == "UNCERTAIN"
        assert len(marks) == 1
        assert not c.executor._drained(), "cancel response must not wait for held model drain"
        assert c.executor.uncertainty_acknowledged is True
        assert (await service.get_coding_receipt(c.owner, view.task_id))["receipt"] is None
    finally:
        ack_release.set()
        release.set()
        if cancelling is not None:
            await asyncio.gather(cancelling, return_exceptions=True)


@pytest.mark.parametrize("boundary", ["model", "host", "publish"])
async def test_stop_waits_actual_retained_work_before_owned_closure(
    coding_case, monkeypatch, boundary
):
    import agency.agents.factory_coding as native

    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    entered, release = asyncio.Event(), asyncio.Event()
    host_entered, host_release = threading.Event(), threading.Event()
    close_events, stop_results = [], []
    client = c.http_client
    original_stage = service._store.stage_coding_result
    original_native_stop = c.executor.stop
    original_host = native.AgencyCodingAgent.run_task

    async def held_http(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"choices": [{"message": {"content": c.response}}]})

    def held_host(*a, **kw):
        host_entered.set()
        assert host_release.wait(10), "owned fixture hold must be released"
        return original_host(*a, **kw)

    async def held_stage(*a, **kw):
        entered.set()
        await release.wait()
        return await original_stage(*a, **kw)

    async def observe_stop(**kw):
        result = await original_native_stop(**kw)
        stop_results.append(result)
        return result

    for obj, label in [
        (service._audit, "audit"),
        (service._budget, "budget"),
        (service._store, "store"),
    ]:
        close = obj.close

        async def observed(close=close, label=label):
            assert c.executor._drained(), (
                "resource closed before actual native/model/hook settlement"
            )
            close_events.append(label)
            await close()

        monkeypatch.setattr(obj, "close", observed)
    monkeypatch.setattr(c.executor, "stop", observe_stop)
    if boundary == "model":
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kw: client(transport=httpx.MockTransport(held_http), **kw),
        )
    elif boundary == "host":
        monkeypatch.setattr(native.AgencyCodingAgent, "run_task", held_host)
    else:
        monkeypatch.setattr(service._store, "stage_coding_result", held_stage)
    stopping = None
    try:
        view = await service.submit_coding(c.owner, payload(c))
        if boundary == "host":
            async with asyncio.timeout(10):
                while not host_entered.is_set():
                    await asyncio.sleep(0.005)
        else:
            await asyncio.wait_for(entered.wait(), 10)
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(0.12)
        assert not stopping.done()
        assert not service._closed and service._ready
        assert close_events == []
        assert False in stop_results, "negative native drain must keep owned resources open"
        assert service._stopping and service._blocked
        release.set()
        host_release.set()
        await asyncio.wait_for(stopping, 10)
        assert close_events == ["audit", "budget", "store"]
        assert stop_results[-1] is True and c.executor._drained()
        assert c.executor.native_report is None or boundary != "model"
        # Reopen without native: interrupted work is never resumed/published.
        c.service = reopened = make_service(c, coding=False)
        await reopened.start()
        row = await reopened._store.get(c.owner, view.task_id)
        assert row.state == "UNCERTAIN"
        assert await reopened._store.list_messages(c.owner, c.thread.id) == []
        replay = await reopened.submit_coding(c.owner, payload(c))
        assert replay.replayed and replay.state == "UNCERTAIN"
    finally:
        release.set()
        host_release.set()
        if stopping is not None:
            await asyncio.gather(stopping, return_exceptions=True)


async def test_partial_start_failure_drains_native_before_owned_cleanup(coding_case, monkeypatch):
    c = coding_case
    c.service = service = make_service(c)
    attempted, release = asyncio.Event(), asyncio.Event()
    original = c.executor.stop
    calls = []

    async def held_stop(**kw):
        calls.append("drain")
        attempted.set()
        if not release.is_set():
            return False
        return await original(**kw)

    async def fail_audit():
        raise RuntimeError("controlled startup audit failure")

    monkeypatch.setattr(c.executor, "stop", held_stop)
    monkeypatch.setattr(service._audit, "initialize", fail_audit)
    starting = asyncio.create_task(service.start())
    try:
        await asyncio.sleep(0.1)
        assert attempted.is_set(), "partial startup must also drain transferred executor"
        assert not starting.done() and not service._closed
        release.set()
        with pytest.raises(PilotError) as error:
            await asyncio.wait_for(starting, 3)
        assert error.value.code == "audit"
        assert service._closed and calls
    finally:
        release.set()
        await asyncio.gather(starting, return_exceptions=True)


@pytest.mark.parametrize("kind", ["denied", "unknown"])
async def test_typed_metered_errors_remain_distinct(coding_case, monkeypatch, kind):
    from agency.llm.metered import MeteredDenied, MeteredError, MeteredModel

    c = coding_case
    c.service = service = make_service(c)
    await service.start()

    async def failure(*a, **kw):
        raise (
            MeteredDenied("controlled before provider")
            if kind == "denied"
            else MeteredError("controlled unknown provider boundary")
        )

    monkeypatch.setattr(MeteredModel, "generate", failure)
    view = await service.submit_coding(c.owner, payload(c))
    row = await settled(c, view)
    assert row.state == ("FAILED" if kind == "denied" else "UNCERTAIN")
    assert row.error_code == ("quota" if kind == "denied" else "uncertainty")
    assert c.executor.native_report is None and c.calls == []
    assert (await service.get_coding_receipt(c.owner, view.task_id))["receipt"] is None


@pytest.mark.parametrize("coding_case", [2.0], indirect=True)
async def test_native_deadline_returns_uncertainty_but_retains_model_until_stop(
    coding_case, monkeypatch
):
    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"choices": [{"message": {"content": c.response}}]})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: c.http_client(transport=httpx.MockTransport(held), **kw)
    )
    stopping = None
    try:
        view = await service.submit_coding(c.owner, payload(c))
        await asyncio.wait_for(entered.wait(), 5)
        row = await settled(c, view)
        assert row.state == "UNCERTAIN" and not c.executor._drained()
        assert c.executor.uncertainty_acknowledged is True
        assert c.executor.native_report is None
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(0.1)
        assert not stopping.done() and not service._closed
        release.set()
        await asyncio.wait_for(stopping, 5)
    finally:
        release.set()
        if stopping is not None:
            await asyncio.gather(stopping, return_exceptions=True)


@pytest.mark.parametrize("lost_after_commit", [False, True])
async def test_uncertainty_ack_failure_never_retried_or_reported_acknowledged(
    coding_case, monkeypatch, lost_after_commit
):
    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    original = service._store.mark_coding_uncertain
    calls = []

    async def failed_ack(*a, **kw):
        calls.append(kw)
        if lost_after_commit:
            await original(*a, **kw)
        raise PilotError("store", uncertain=True)

    # Hold real native ownership by fencing at a hook, not inventing a report.
    async def failed_intent(*a, **kw):
        raise PilotError("store", uncertain=True)

    monkeypatch.setattr(service._store, "mark_coding_uncertain", failed_ack)
    monkeypatch.setattr(service._store, "record_coding_intent", failed_intent)
    view = await service.submit_coding(c.owner, payload(c))
    row = await settled(c, view)
    assert row.state == "UNCERTAIN" and service._blocked
    assert c.executor.uncertainty_acknowledged is False
    assert len(calls) == 1 and c.executor.native_report is None
    assert (await service.get_coding_receipt(c.owner, view.task_id))["receipt"] is None


async def test_stop_at_actual_coding_sql_commit_fence_rolls_back_transcript(
    coding_case, monkeypatch
):
    import sqlite3

    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    entered, release = threading.Event(), threading.Event()
    original_event = service._store._event

    def before_commit(conn, sequence, phase, code):
        original_event(conn, sequence, phase, code)
        if phase == "committed":
            # All coding SQL has executed in the real transaction, before the
            # existing publication gate and COMMIT. No fabricated store ACK.
            entered.set()
            assert release.wait(10), "owned SQL fixture hold must be released"

    monkeypatch.setattr(service._store, "_event", before_commit)
    stopping = None
    try:
        view = await service.submit_coding(c.owner, payload(c))
        async with asyncio.timeout(10):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(0.05)
        assert service._stopping and service._store._publication_closed
        assert not service._closed and not stopping.done()
        release.set()
        await asyncio.wait_for(stopping, 5)
        with sqlite3.connect(c.root / "owner.sqlite") as conn:
            assert conn.execute(
                "SELECT state FROM pilot_requests WHERE task_id=?", (view.task_id,)
            ).fetchone() == ("UNCERTAIN",)
            assert conn.execute("SELECT count(*) FROM thread_messages").fetchone() == (0,)
            side = conn.execute(
                "SELECT evidence_json,evidence_phase FROM pilot_coding_bindings"
            ).fetchone()
            assert side[0] is not None and side[1] == "uncertain"
        assert c.executor.native_report is not None and c.executor._drained()
    finally:
        release.set()
        if stopping is not None:
            await asyncio.gather(stopping, return_exceptions=True)
