"""Concrete SQLite quota backend under private neutral accounting keys."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest
from agency.admission.models import OwnerPrincipal, PilotError
from agency.admission.store import OwnerPilotStore
from agency.telegram.budget_store import BetaBudgetStore, BudgetLimits

OWNER = OwnerPrincipal("owner", "local", "local")


@pytest.fixture
async def resources(tmp_path):
    from agency.admission.budget import OwnerBudgetAdapter

    store = OwnerPilotStore(str(tmp_path / "pilot.db"))
    backend = BetaBudgetStore(str(tmp_path / "budget.db"))
    budget = OwnerBudgetAdapter(backend, store)
    try:
        await store.initialize()
        await backend.initialize()
        yield store, backend, budget
    finally:
        await budget.close()
        await backend.close()
        await store.close()


async def admit(store, key="turn", principal=OWNER):
    _, thread = await store.create_thread(principal, "thread-" + key, "Title")
    return await store.admit(
        principal, key, operation="turn", payload={"thread_id": thread.id, "message": "hello"}
    )


async def test_request_lease_uses_only_neutral_mapping_and_is_not_reissued(resources):
    from agency.admission.models import RequestLease

    store, backend, budget = resources
    row = await admit(store)
    lease = await budget.reserve_request(OWNER, row.request_id, row.task_id)
    assert type(lease) is RequestLease and lease.sequence == row.sequence
    assert lease.deadline_at == row.created_at + 120
    assert lease.quota_subject_id > 0 and lease.capability
    assert lease.capability not in repr(lease)
    assert budget.owns(lease)
    assert not budget.owns(replace(lease))
    assert not budget.owns(replace(lease, task_id="wrong"))
    with closing(sqlite3.connect(backend._db_path)) as db, db:
        assert db.execute("SELECT bot_id,update_id,user_id,state FROM requests").fetchall() == [
            ("owner-pilot:v1", row.sequence, lease.quota_subject_id, "RESERVED")
        ]
    with pytest.raises(PilotError):
        await budget.reserve_request(OWNER, row.request_id, row.task_id)
    with pytest.raises(PilotError):
        await budget.reserve_request(OWNER, "missing", row.task_id)
    await budget.mark_running(lease)
    assert (await store.get(OWNER, row.task_id)).state == "RUNNING"
    await budget.finish_request(lease, success=False)
    assert not budget.owns(lease)
    assert await backend.inspect_reservation("owner-pilot:v1", row.sequence) == "FAILED"


async def test_model_and_allowed_tool_attempts_are_durable_and_bounded(resources):
    store, backend, budget = resources
    row = await admit(store)
    lease = await budget.reserve_request(OWNER, row.request_id, row.task_id)
    assert not await budget.reserve_model_call(lease)
    await budget.mark_running(lease)
    assert sum(await asyncio.gather(*(budget.reserve_model_call(lease) for _ in range(12)))) == 8
    assert not await budget.reserve_tool_call(lease, "shell")
    assert (
        sum(
            await asyncio.gather(*(budget.reserve_tool_call(lease, "calculator") for _ in range(6)))
        )
        == 4
    )
    current = await store.get(OWNER, row.task_id)
    assert current.model_attempts == 8 and current.tool_attempts == 4
    with closing(sqlite3.connect(backend._db_path)) as db, db:
        assert db.execute("SELECT COUNT(*) FROM model_attempts").fetchone()[0] == 8
    await budget.finish_request(lease, success=True)
    assert not await budget.reserve_model_call(lease)
    assert not await budget.reserve_tool_call(lease, "utc_time")
    # Accounting completion is NOT transcript publication.
    assert (await store.get(OWNER, row.task_id)).state == "RUNNING"
    assert await store.list_messages(OWNER, row.thread_id) == []


async def test_backend_denial_never_refunds_conservative_pilot_attempt(tmp_path):
    from agency.admission.budget import OwnerBudgetAdapter

    store = OwnerPilotStore(str(tmp_path / "pilot.db"))
    backend = BetaBudgetStore(
        str(tmp_path / "budget.db"), limits=BudgetLimits(model_calls_per_request=2)
    )
    budget = OwnerBudgetAdapter(backend, store)
    try:
        await store.initialize()
        await backend.initialize()
        row = await admit(store)
        lease = await budget.reserve_request(OWNER, row.request_id, row.task_id)
        await budget.mark_running(lease)
        assert await budget.reserve_model_call(lease)
        assert await budget.reserve_model_call(lease)
        assert not await budget.reserve_model_call(lease)
        assert (await store.get(OWNER, row.task_id)).model_attempts == 3
        with closing(sqlite3.connect(backend._db_path)) as db, db:
            assert db.execute("SELECT COUNT(*) FROM model_attempts").fetchone()[0] == 2
    finally:
        await budget.close()
        await store.close()


async def test_grants_and_metered_binding_are_exact_internal_capabilities(resources):
    from agency.admission.models import OwnerExecutionGrant, OwnerMeteredBinding

    store, _, budget = resources
    row = await admit(store)
    lease = await budget.reserve_request(OWNER, row.request_id, row.task_id)
    grant = OwnerExecutionGrant(lease, budget)
    assert grant.allowed_tools == frozenset({"calculator", "utc_time", "web_search"})
    assert grant.max_model_calls == 8 and grant.max_tool_calls == 4
    binding = OwnerMeteredBinding(grant, "echo", "echo", "synthetic-config")
    assert binding.grant is grant
    assert lease.capability not in repr(grant) and lease.capability not in repr(binding)
    for changes in (
        {"max_model_calls": 9},
        {"max_tool_calls": True},
        {"allowed_tools": frozenset({"shell"})},
        {"allowed_tools": {"calculator"}},
        {"budget": object()},
        {"lease": replace(lease)},
    ):
        with pytest.raises(PilotError):
            replace(grant, **changes)
    for changes in (
        {"provider_config_id": ""},
        {"provider_config_id": "x" * 129},
        {"provider": "pollinations"},
        {"grant": object()},
    ):
        with pytest.raises(PilotError):
            replace(binding, **changes)
    budget.revoke(lease)
    with pytest.raises(PilotError):
        OwnerExecutionGrant(lease, budget)


async def test_cancel_terminal_and_recovery_immediately_invalidate_live_lease(resources):
    store, _, budget = resources
    row = await admit(store)
    lease = await budget.reserve_request(OWNER, row.request_id, row.task_id)
    await budget.mark_running(lease)
    await store.request_cancel(OWNER, row.task_id, "cancel")
    assert not budget.owns(lease)
    assert not await budget.reserve_model_call(lease)
    assert not await budget.reserve_tool_call(lease, "web_search")
    assert (await store.get(OWNER, row.task_id)).model_attempts == 0
    other = await admit(store, "other")
    other_lease = await budget.reserve_request(OWNER, other.request_id, other.task_id)
    await store.recover_interrupted()
    assert not budget.owns(other_lease)


async def test_cancelled_backend_await_drains_before_return_and_revokes(resources, monkeypatch):
    store, backend, budget = resources
    row = await admit(store)
    lease = await budget.reserve_request(OWNER, row.request_id, row.task_id)
    await budget.mark_running(lease)
    entered, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = backend.reserve_model_call

    async def gated(*args, **kwargs):
        entered.set()
        await release.wait()
        result = await original(*args, **kwargs)
        completed.set()
        return result

    monkeypatch.setattr(backend, "reserve_model_call", gated)
    task = asyncio.create_task(budget.reserve_model_call(lease))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed.is_set()
        assert not budget.owns(lease)
        assert (await store.get(OWNER, row.task_id)).model_attempts == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_close_during_backend_await_counts_but_never_authorizes(resources, monkeypatch):
    store, backend, budget = resources
    row = await admit(store)
    lease = await budget.reserve_request(OWNER, row.request_id, row.task_id)
    await budget.mark_running(lease)
    entered, release = asyncio.Event(), asyncio.Event()
    original = backend.reserve_model_call

    async def gated(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(backend, "reserve_model_call", gated)
    reserve = asyncio.create_task(budget.reserve_model_call(lease))
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        closing = asyncio.create_task(budget.close())
        await asyncio.sleep(0)
        assert not budget.owns(lease)
        release.set()
        assert await reserve is False
        await closing
    finally:
        release.set()
        await asyncio.gather(reserve, *([closing] if closing else []), return_exceptions=True)


async def test_duplicate_reservation_or_restart_never_mints_new_capability(resources, tmp_path):
    from agency.admission.budget import OwnerBudgetAdapter

    store, _, budget = resources
    row = await admit(store)
    replay = await store.admit(
        OWNER,
        row.request_id,
        operation="turn",
        payload={"thread_id": row.thread_id, "message": "hello"},
    )
    assert replay.replayed
    # A duplicate admission cannot sabotage the original submitter's one claim.
    attempts = await asyncio.gather(
        *(budget.reserve_request(OWNER, row.request_id, row.task_id) for _ in range(6)),
        return_exceptions=True,
    )
    leases = [result for result in attempts if not isinstance(result, BaseException)]
    assert len(leases) == 1
    assert budget.owns(leases[0])
    assert all(
        isinstance(result, PilotError) for result in attempts if isinstance(result, BaseException)
    )
    with pytest.raises(PilotError):
        await budget.reserve_request(OWNER, row.request_id, row.task_id)
    path = str(tmp_path / "restart.db")
    first = OwnerPilotStore(path)
    try:
        await first.initialize()
        old = await admit(first)
    finally:
        await first.close()
    second = OwnerPilotStore(path)
    backend = BetaBudgetStore(str(tmp_path / "restart-budget.db"))
    restarted = OwnerBudgetAdapter(backend, second)
    try:
        await second.initialize()
        await backend.initialize()
        with pytest.raises(PilotError):
            await restarted.reserve_request(OWNER, old.request_id, old.task_id)
        assert await backend.inspect_reservation("owner-pilot:v1", old.sequence) is None
    finally:
        await restarted.close()
        await second.close()


async def test_quota_backend_failure_preserves_attempt_and_revokes(resources):
    store, backend, budget = resources
    row = await admit(store)
    lease = await budget.reserve_request(OWNER, row.request_id, row.task_id)
    await budget.mark_running(lease)
    with closing(sqlite3.connect(backend._db_path)) as db, db:
        db.execute(
            "CREATE TRIGGER reject_attempt BEFORE INSERT ON model_attempts BEGIN SELECT RAISE(ABORT, 'synthetic private budget error'); END"
        )
    with pytest.raises(PilotError) as exc:
        await budget.reserve_model_call(lease)
    assert exc.value.uncertain and exc.value.status_code == 503
    assert "synthetic private budget error" not in str(exc.value)
    assert (await store.get(OWNER, row.task_id)).model_attempts == 1
    assert not budget.owns(lease)
    assert not await budget.reserve_model_call(lease)


async def test_request_ceiling_and_owner_mapping_survive_transport_change(tmp_path):
    from agency.admission.budget import OwnerBudgetAdapter

    store = OwnerPilotStore(str(tmp_path / "pilot.db"))
    backend = BetaBudgetStore(str(tmp_path / "budget.db"), limits=BudgetLimits(requests_per_hour=1))
    budget = OwnerBudgetAdapter(backend, store)
    try:
        await store.initialize()
        await backend.initialize()
        row = await admit(store)
        lease = await budget.reserve_request(OWNER, row.request_id, row.task_id)
        telegram = OwnerPrincipal("owner", "telegram", "42")
        other = await admit(store, "telegram", telegram)
        with pytest.raises(PilotError) as exc:
            await budget.reserve_request(telegram, other.request_id, other.task_id)
        assert exc.value.status_code == 429
        assert budget.owns(lease)
        with closing(sqlite3.connect(store._db_path)) as db, db:
            assert db.execute("SELECT COUNT(*) FROM pilot_subjects").fetchone()[0] == 1
    finally:
        await budget.close()
        await store.close()


async def test_tampered_expired_and_subclass_leases_deny_without_raising(resources, monkeypatch):
    import time

    from agency.admission.models import RequestLease

    store, _, budget = resources
    row = await admit(store)
    lease = await budget.reserve_request(OWNER, row.request_id, row.task_id)

    class Derived(RequestLease):
        pass

    derived = Derived(
        lease.principal,
        lease.request_id,
        lease.task_id,
        lease.sequence,
        lease.quota_subject_id,
        lease.capability,
        lease.deadline_at,
    )
    assert not budget.owns(derived)
    assert not await budget.reserve_model_call(derived)
    original_sequence = lease.sequence
    try:
        object.__setattr__(lease, "sequence", [])
        assert not budget.owns(lease)
    finally:
        object.__setattr__(lease, "sequence", original_sequence)
    object.__setattr__(lease, "task_id", "mismatch")
    assert not budget.owns(lease)
    object.__setattr__(lease, "task_id", row.task_id)
    assert budget.owns(lease)
    monkeypatch.setattr(time, "time", lambda: lease.deadline_at)
    assert not budget.owns(lease)
    assert not await budget.reserve_tool_call(lease, "calculator")


@pytest.mark.parametrize("method", ["mark_running", "finish_request"])
async def test_cancel_during_state_read_revokes_before_return(resources, monkeypatch, method):
    store, _, budget = resources
    row = await admit(store)
    lease = await budget.reserve_request(OWNER, row.request_id, row.task_id)
    if method == "finish_request":
        await budget.mark_running(lease)
    entered, release = asyncio.Event(), asyncio.Event()
    original = store.get

    async def gated(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(store, "get", gated)
    call = (
        budget.mark_running(lease)
        if method == "mark_running"
        else budget.finish_request(lease, success=False)
    )
    task = asyncio.create_task(call)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not budget.owns(lease)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_revocation_uses_original_lease_identity_even_when_malformed(resources):
    store, _, budget = resources
    row = await admit(store)
    lease = await budget.reserve_request(OWNER, row.request_id, row.task_id)
    sequence = lease.sequence
    try:
        object.__setattr__(lease, "sequence", [])
        budget.revoke(lease)
    finally:
        object.__setattr__(lease, "sequence", sequence)
    assert not budget.owns(lease)
