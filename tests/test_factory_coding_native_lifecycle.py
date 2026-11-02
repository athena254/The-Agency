"""Retained work is not killed by cancellation; synthetic boundary controls only."""

from __future__ import annotations

import asyncio
import threading

import pytest

from agency.agents.coding import AgencyCodingAgent
from agency.agents.factory_coding import NativeCodingDenied, NativeCodingUncertain
from agency.agents.registry import AgentStatus
from tests import test_factory_coding_native as fixtures

native = fixtures.native
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def synthetic_preflight_boundary(monkeypatch):
    # Lifecycle controls model stalled native effects, not Git latency. Full real
    # preflight/base/registration checks are exercised by test_real_native_journey.
    from agency.agents.factory_coding import NativeCodingExecutor

    monkeypatch.setattr(NativeCodingExecutor, "_preflight", lambda *a, **kw: None)


async def run(n):
    return await n["executor"].execute(
        n["request"], grant=n["grant"], model=n["model"], hooks=n["hooks"]
    )


@pytest.mark.parametrize("boundary", ["preflight", "mutation"])
async def test_cancel_retains_exact_native_work(native, monkeypatch, boundary):
    n = native
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    dispatches = []
    if boundary == "preflight":
        original = n["executor"]._preflight

        def block(*a, **kw):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(8), "harness release missing"
            return original(*a, **kw)

        monkeypatch.setattr(n["executor"], "_preflight", block)
    else:

        def block(*a, **kw):
            dispatches.append(1)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(8), "harness release missing"
            raise RuntimeError("synthetic native failure after retained settlement; no report")

        monkeypatch.setattr(AgencyCodingAgent, "run_task", block)
    task = asyncio.create_task(run(n))
    try:
        await asyncio.wait_for(entered.wait(), 4)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await n["executor"].stop(drain_timeout_s=0.01) is False
        record = n["runtimes"].get_required(n["agent"].id)
        assert record.status is AgentStatus.DRAINING
        assert record.current_task_id == n["request"].binding.task_id
        with pytest.raises(NativeCodingDenied):
            await run(n)
        assert n["hooks"].unknown == [(boundary, "cancelled")]
        assert n["hooks"].outcomes == []
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await n["executor"].stop(drain_timeout_s=4.0) is True
    assert record.status is AgentStatus.IDLE
    assert n["hooks"].outcomes == []
    assert dispatches == ([1] if boundary == "mutation" else [])
    with pytest.raises(NativeCodingDenied):
        await run(n)


@pytest.mark.parametrize("native", [5.0], indirect=True)
@pytest.mark.parametrize("signal", ["deadline", "stop"])
async def test_deadline_and_stop_fence_without_killing(native, monkeypatch, signal):
    n = native
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    calls = []

    def block(*a, **kw):
        calls.append(1)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(8)
        raise RuntimeError("synthetic unknown native result")

    monkeypatch.setattr(AgencyCodingAgent, "run_task", block)
    task = asyncio.create_task(run(n))
    try:
        await asyncio.wait_for(entered.wait(), 4)
        if signal == "stop":
            assert await n["executor"].stop(drain_timeout_s=0.01) is False
        with pytest.raises(NativeCodingUncertain):
            await asyncio.wait_for(asyncio.shield(task), 6)
        assert await n["executor"].stop(drain_timeout_s=0.01) is False
        assert n["runtimes"].get_required(n["agent"].id).status is AgentStatus.DRAINING
        assert n["hooks"].unknown == [
            ("mutation", "deadline" if signal == "deadline" else "stopping")
        ]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await n["executor"].stop(drain_timeout_s=4.0) is True
    assert n["hooks"].outcomes == []
    assert calls == [1]


async def test_owner_loop_model_completion_retained(native, monkeypatch):
    n = native
    entered, release = asyncio.Event(), asyncio.Event()
    original = n["audit"].append_beta

    async def held(entry):
        n["hooks"].affinity()
        if entry.result == "ok":
            entered.set()
            await release.wait()
        return await original(entry)

    monkeypatch.setattr(n["audit"], "append_beta", held)
    task = asyncio.create_task(run(n))
    try:
        await asyncio.wait_for(entered.wait(), 4)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await n["executor"].stop(drain_timeout_s=0.01) is False
        assert await n["audit"].has_pending_calls()
        assert n["runtimes"].get_required(n["agent"].id).status is AgentStatus.DRAINING
        assert n["hooks"].unknown == [("planning", "cancelled")]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await n["executor"].stop(drain_timeout_s=4.0) is True
    assert not await n["audit"].has_pending_calls()
    assert len(n["calls"]) == 1
    assert not any(e[0] == "intent" for e in n["hooks"].events)
    assert n["hooks"].outcomes == []


@pytest.mark.parametrize("hook", ["record_intent", "publish"])
async def test_cancel_retains_hook_ack_no_retry(native, monkeypatch, hook):
    n = native
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def held(*a):
        n["hooks"].affinity()
        calls.append(1)
        entered.set()
        await release.wait()
        # Simulate acknowledged ledger action without publishing late in this recorder.
        return None  # noqa: RET501, PLR1711 -- explicit declared hook acknowledgement

    monkeypatch.setattr(n["hooks"], hook, held)
    task = asyncio.create_task(run(n))
    try:
        await asyncio.wait_for(entered.wait(), 4)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await n["executor"].stop(drain_timeout_s=0.01) is False
        assert n["runtimes"].get_required(n["agent"].id).status is AgentStatus.DRAINING
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await n["executor"].stop(drain_timeout_s=4.0) is True
    assert calls == [1]
    assert n["hooks"].outcomes == []
    with pytest.raises(NativeCodingDenied):
        await run(n)


@pytest.mark.parametrize("lost", ["foreign_task", "replacement", "revoked"])
async def test_lost_claim_never_cleared_after_drain(native, monkeypatch, lost):
    n = native
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def block(*a, **kw):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(8)
        raise RuntimeError("no native report")

    monkeypatch.setattr(AgencyCodingAgent, "run_task", block)
    task = asyncio.create_task(run(n))
    try:
        await asyncio.wait_for(entered.wait(), 4)
        if lost == "foreign_task":
            n["runtimes"].update_agent_status(
                n["agent"].id, AgentStatus.BUSY, current_task_id="foreign"
            )
        elif lost == "replacement":
            n["runtimes"].deregister(n["agent"].id)
            replacement = n["runtimes"].register(n["agent"].model_copy(deep=True))
            replacement.status = AgentStatus.BUSY
            replacement.current_task_id = n["request"].binding.task_id
        else:
            n["factory"].revoke("fixture-coder", "1.0.0", evidence="fixture revoke")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await n["executor"].stop(drain_timeout_s=0.01) is False
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await n["executor"].stop(drain_timeout_s=4.0) is True
    if lost == "revoked":
        assert n["runtimes"].get_agent(n["agent"].id) is None
    else:
        record = n["runtimes"].get_required(n["agent"].id)
        assert record.status is AgentStatus.BUSY
        assert record.current_task_id == (
            "foreign" if lost == "foreign_task" else n["request"].binding.task_id
        )
    assert n["hooks"].outcomes == []


async def test_overlapping_invocation_loses_without_stealing(native, monkeypatch):
    n = native
    entered, release = asyncio.Event(), asyncio.Event()

    async def checkpoint(binding, phase):
        if phase == "planning":
            entered.set()
            await release.wait()

    monkeypatch.setattr(n["hooks"], "checkpoint", checkpoint)
    first = asyncio.create_task(run(n))
    try:
        await asyncio.wait_for(entered.wait(), 4)
        with pytest.raises(NativeCodingDenied):
            await run(n)
        assert (
            n["runtimes"].get_required(n["agent"].id).current_task_id
            == n["request"].binding.task_id
        )
    finally:
        release.set()
    assert (await first).receipt.objective_status == "passed"
    assert len(n["calls"]) == 1


@pytest.mark.parametrize("hook", ["record_intent", "publish", "uncertain"])
async def test_unknown_hook_ack_is_not_success(native, monkeypatch, hook):
    n = native
    calls = []

    async def invalid(*a):
        calls.append(1)
        return True  # All declared hooks require exactly None on acknowledgement.

    monkeypatch.setattr(n["hooks"], hook, invalid)
    if hook == "uncertain":
        n["response"]["raw"] = ""
        from agency.llm.metered import MeteredError

        expected = MeteredError
    else:
        expected = NativeCodingUncertain
    with pytest.raises(expected):
        await run(n)
    assert calls == [1]
    assert n["hooks"].outcomes == []
    with pytest.raises(NativeCodingDenied):
        await run(n)
    if hook == "uncertain":
        assert n["executor"].uncertainty_acknowledged is False


async def test_revocation_after_await_prevents_native_dispatch(native, monkeypatch):
    n = native

    async def intent(*a):
        n["factory"].revoke("fixture-coder", "1.0.0", evidence="fixture revocation at intent")

    monkeypatch.setattr(n["hooks"], "record_intent", intent)
    with pytest.raises(NativeCodingUncertain):
        await run(n)
    assert n["hooks"].outcomes == []
    assert n["hooks"].unknown == [("mutation", "identity_changed")]
    assert n["runtimes"].get_agent(n["agent"].id) is None


@pytest.mark.parametrize("hook", ["record_intent", "publish", "uncertain"])
async def test_lost_ack_is_never_retried(native, monkeypatch, hook):
    n = native
    calls = []

    async def lost(*a):
        calls.append(1)
        raise RuntimeError("synthetic lost acknowledgement after possible write")

    monkeypatch.setattr(n["hooks"], hook, lost)
    if hook == "uncertain":
        n["response"]["raw"] = ""
        from agency.llm.metered import MeteredError

        expected = MeteredError
    else:
        expected = NativeCodingUncertain
    with pytest.raises(expected):
        await run(n)
    assert calls == [1]
    assert await n["executor"].stop(drain_timeout_s=2.0) is True
    assert n["hooks"].outcomes == []
    assert n["executor"].uncertainty_acknowledged is (hook != "uncertain")
    with pytest.raises(NativeCodingDenied):
        await run(n)


async def test_pending_uncertainty_ack_is_owned(native, monkeypatch):
    n = native
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(*a):
        entered.set()
        await release.wait()

    monkeypatch.setattr(n["hooks"], "uncertain", hold)
    n["response"]["raw"] = ""
    from agency.llm.metered import MeteredError

    try:
        with pytest.raises(MeteredError):
            await run(n)
        await asyncio.wait_for(entered.wait(), 4)
        assert n["executor"].uncertainty_acknowledged is False
        assert await n["executor"].stop(drain_timeout_s=0.01) is False
        assert n["runtimes"].get_required(n["agent"].id).status is AgentStatus.DRAINING
    finally:
        release.set()
        assert await n["executor"].stop(drain_timeout_s=4.0) is True
    assert n["executor"].uncertainty_acknowledged is True
    assert n["runtimes"].get_required(n["agent"].id).status is AgentStatus.IDLE


async def test_captured_typed_error_precedes_later_identity_drift(native, monkeypatch):
    n = native
    from agency.llm.metered import MeteredError

    original = n["model"].generate
    n["response"]["raw"] = ""
    captured = []

    async def generate(*a, **kw):
        try:
            return await original(*a, **kw)
        except MeteredError as exc:
            captured.append(exc)
            n["kernels"].revoke(n["agent"].id)
            raise

    monkeypatch.setattr(n["model"], "generate", generate)
    with pytest.raises(MeteredError) as error:
        await run(n)
    assert error.value is captured[0]
    assert n["hooks"].outcomes == []


async def test_cancel_during_dispatch_revalidation_cannot_start_forge(native, monkeypatch):
    n = native
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    checks, calls = [], []

    def preflight(*a, **kw):
        checks.append(1)
        if len(checks) == 2:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(8)

    def forbidden(*a, **kw):
        calls.append(1)
        raise AssertionError("Forge started after fenced preflight")

    monkeypatch.setattr(n["executor"], "_preflight", preflight)
    monkeypatch.setattr(AgencyCodingAgent, "run_task", forbidden)
    task = asyncio.create_task(run(n))
    try:
        await asyncio.wait_for(entered.wait(), 4)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await n["executor"].stop(drain_timeout_s=0.01) is False
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await n["executor"].stop(drain_timeout_s=4.0) is True
    assert calls == []
    assert n["hooks"].outcomes == []


@pytest.mark.parametrize("new,verdict", [("x = 2", "passed"), ("x = 3", "failed")])
async def test_publish_ack_can_finalize_and_revoke_metering_lease(
    native, monkeypatch, new, verdict
):
    import json

    n = native
    n["response"]["raw"] = json.dumps([{"path": "m.py", "old": "x = 1\n", "new": new}])
    original = n["hooks"].publish

    async def finalize(outcome):
        assert n["budget"].owns(n["grant"].lease)
        await n["budget"].finish_request(
            n["grant"].lease, success=outcome.receipt.objective_status == "passed"
        )
        assert not n["budget"].owns(n["grant"].lease)
        await original(outcome)

    monkeypatch.setattr(n["hooks"], "publish", finalize)
    outcome = await run(n)
    assert outcome.receipt.objective_status == verdict
    assert n["hooks"].outcomes == [outcome]
    assert n["hooks"].unknown == []
    assert n["runtimes"].get_required(n["agent"].id).status is AgentStatus.IDLE
