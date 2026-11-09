"""Storage faults, exact-snapshot and cancellation: only disposable databases."""

# ruff: noqa: F811 -- pytest fixture import is intentionally shadowed by injection

import asyncio
import json
import shutil
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest

from agency.admission.models import OwnerExecutionGrant, PilotError
from agency.kernel.audit import AuditEntry
from tests.test_owner_recovery import dump, recovery, seeded
from tests.test_owner_service import concrete  # noqa: F401 -- pytest fixture


@pytest.mark.parametrize(
    "case",
    [
        "null_pending",
        "orphan_uncertainty",
        "bad_call",
        "null_request",
        "wrong_request_type",
        "unknown_version",
    ],
)
async def test_malformed_storage_cannot_be_previewed(concrete, case):
    c = concrete
    service, _ = await seeded(c)
    coord = recovery(c)
    try:
        await coord.prepare()
        path = (
            c["store"]._db_path
            if case in ("null_request", "wrong_request_type")
            else c["audit"]._db_path
        )
        with closing(sqlite3.connect(path)) as db, db:
            if case == "null_pending":
                db.execute("UPDATE audit_entries SET evidence=NULL WHERE result='pending'")
            elif case == "orphan_uncertainty":
                db.execute(
                    "UPDATE audit_entries SET result='unknown', evidence=?",
                    (json.dumps({"phase": "uncertainty", "call_id": "d" * 32}),),
                )
            elif case == "bad_call":
                db.execute(
                    "UPDATE audit_entries SET evidence=?",
                    (json.dumps({"phase": "intent", "call_id": "not-valid"}),),
                )
            elif case == "null_request":
                db.execute("UPDATE pilot_requests SET task_id=NULL WHERE state='UNCERTAIN'")
            elif case == "wrong_request_type":
                db.execute(
                    "UPDATE pilot_requests SET created_at='not-a-time' WHERE state='UNCERTAIN'"
                )
            else:
                db.execute(
                    "UPDATE owner_recovery_identity SET payload=?", (json.dumps({"version": 2}),)
                )
        before = dump(path)
        with pytest.raises(PilotError):
            await coord.preview()
        assert dump(path) == before
    finally:
        await service.stop()


async def test_same_id_concurrent_replay_and_reopen(concrete):
    from agency.admission.budget import OwnerBudgetAdapter
    from agency.admission.store import OwnerPilotStore
    from agency.kernel.audit import BetaAuditLog
    from agency.telegram.budget_store import BetaBudgetStore

    c = concrete
    service, _ = await seeded(c)
    coord = recovery(c)
    await coord.prepare()
    snap = await coord.preview()
    receipts = await asyncio.gather(
        *(
            coord.accept(snap["digest"], "same-id", accept_residual_uncertainty=True)
            for _ in range(3)
        )
    )
    assert all(r == receipts[0] for r in receipts)
    before = [dump(c[name]._db_path) for name in ("store", "backend", "audit")]
    await service.stop()
    store = OwnerPilotStore(c["store"]._db_path)
    backend = BetaBudgetStore(c["backend"]._db_path)
    budget = OwnerBudgetAdapter(backend, store)
    audit = BetaAuditLog(c["audit"]._db_path)
    c.update(store=store, backend=backend, budget=budget, audit=audit)
    try:
        await store.initialize()
        await budget.initialize()
        await audit.initialize()
        reopened = recovery(c)
        assert await reopened.prepare() == {"prepared": True}
        assert (
            await reopened.accept(snap["digest"], "same-id", accept_residual_uncertainty=True)
            == receipts[0]
        )
        assert not await reopened.blocked()
        assert before == [dump(c[name]._db_path) for name in ("store", "backend", "audit")]
    finally:
        await audit.close()
        await budget.close()
        await store.close()


@pytest.mark.parametrize("change", ["added", "missing", "changed", "path", "swapped_file"])
async def test_exact_current_snapshot_and_file_binding(concrete, change):
    c = concrete
    service, thread = await seeded(c)
    coord = recovery(c)
    try:
        await coord.prepare()
        snap = await coord.preview()
        await coord.accept(snap["digest"], "old-ack", accept_residual_uncertainty=True)
        if change == "added":
            row = await c["store"].admit(
                c["owner"],
                "extra",
                operation="turn",
                payload={"thread_id": thread.id, "message": "extra"},
            )
            await c["store"].transition(
                row.sequence, expected="ADMITTED", state="UNCERTAIN", phase="uncertain"
            )
        elif change == "path":
            c["store"]._db_path += ".wrong"
        elif change == "swapped_file":
            c["threads"].close()
            original = Path(c["store"]._db_path)
            # Flush WAL before making a byte-preserving replacement with a new file identity.
            with closing(sqlite3.connect(original)) as db:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            replacement = original.with_suffix(".copy")
            shutil.copyfile(original, replacement)
            original.unlink()
            replacement.rename(original)
        else:
            with closing(sqlite3.connect(c["store"]._db_path)) as db, db:
                if change == "missing":
                    db.execute("DELETE FROM pilot_requests WHERE state='UNCERTAIN'")
                else:
                    db.execute("UPDATE pilot_requests SET phase='changed' WHERE state='UNCERTAIN'")
        try:
            blocked = await coord.blocked()
        except PilotError:
            blocked = True
        assert blocked
        with pytest.raises(PilotError):
            await coord.accept(snap["digest"], "old-ack", accept_residual_uncertainty=True)
        assert not c["requests"]
    finally:
        await service.stop()


async def test_claim_rechecks_durable_turn_inside_transaction(concrete):
    c = concrete
    service, thread = await seeded(c)
    coord = recovery(c)
    try:
        await coord.prepare()
        snap = await coord.preview()
        await coord.accept(snap["digest"], "ack", accept_residual_uncertainty=True)
        row = await c["store"].admit(
            c["owner"],
            "fresh",
            operation="turn",
            payload={"thread_id": thread.id, "message": "new"},
        )
        lease = await c["budget"].reserve_request(c["owner"], "fresh", row.task_id)
        await c["budget"].mark_running(lease)
        grant = OwnerExecutionGrant(lease, c["budget"])
        bound = coord.for_grant(grant)
        with closing(sqlite3.connect(c["store"]._db_path)) as db, db:
            db.execute(
                "UPDATE pilot_requests SET cancel_requested=1 WHERE sequence=?", (row.sequence,)
            )
        before = dump(c["audit"]._db_path)
        with pytest.raises(PilotError):
            await bound.claim_intent(
                grant,
                c["audit"],
                AuditEntry(
                    agent="metered-model",
                    task="owner-pilot",
                    action="model.generate",
                    result="pending",
                    evidence={"phase": "intent", "call_id": "f" * 32},
                ),
            )
        assert dump(c["audit"]._db_path) == before
    finally:
        await service.stop()


@pytest.mark.parametrize("fault", ["commit", "cancel", "response_loss"])
async def test_commit_cancellation_response_loss(concrete, monkeypatch, fault):
    import agency.admission.owner_recovery as module

    c = concrete
    service, _ = await seeded(c)
    coord = recovery(c)
    try:
        await coord.prepare()
        snap = await coord.preview()
        before = dump(c["audit"]._db_path)
        entered, release = threading.Event(), threading.Event()
        connect = sqlite3.connect

        class Connection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql == "COMMIT":
                    if fault == "commit":
                        raise sqlite3.OperationalError("synthetic commit failure")
                    if fault == "cancel":
                        entered.set()
                        assert release.wait(5)
                result = super().execute(sql, *args, **kwargs)
                if sql == "COMMIT" and fault == "response_loss":
                    raise sqlite3.OperationalError("synthetic response loss")
                return result

        monkeypatch.setattr(
            module.sqlite3, "connect", lambda *a, **kw: connect(*a, **kw, factory=Connection)
        )
        if fault == "cancel":
            task = asyncio.create_task(
                coord.accept(snap["digest"], "fault", accept_residual_uncertainty=True)
            )
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0.03)
            assert not task.done(), "Cancellation abandoned SQLite commit"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(PilotError):
                await coord.accept(snap["digest"], "fault", accept_residual_uncertainty=True)
        monkeypatch.setattr(module.sqlite3, "connect", connect)
        if fault == "commit":
            assert dump(c["audit"]._db_path) == before
        replay = await coord.accept(snap["digest"], "fault", accept_residual_uncertainty=True)
        with closing(sqlite3.connect(c["audit"]._db_path)) as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM audit_entries WHERE action='owner.acknowledge_uncertainty'"
                ).fetchone()[0]
                == 1
            )
        assert replay["receipt_id"] and not c["requests"]
    finally:
        await service.stop()


async def test_accept_requires_exact_postcommit_readback(concrete, monkeypatch):
    import agency.admission.owner_recovery as module

    c = concrete
    service, _ = await seeded(c)
    coord = recovery(c)
    try:
        await coord.prepare()
        snap = await coord.preview()
        connect = sqlite3.connect

        class ChangedAfterCommit(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                result = super().execute(sql, *args, **kwargs)
                if sql == "COMMIT":
                    super().execute(
                        "UPDATE audit_entries SET target='changed-after-commit' WHERE action='owner.acknowledge_uncertainty'"
                    )
                return result

        monkeypatch.setattr(
            module.sqlite3,
            "connect",
            lambda *a, **kw: connect(*a, **kw, factory=ChangedAfterCommit),
        )
        with pytest.raises(PilotError):
            await coord.accept(snap["digest"], "readback", accept_residual_uncertainty=True)
        monkeypatch.setattr(module.sqlite3, "connect", connect)
        with pytest.raises(PilotError):
            await coord.blocked()
    finally:
        await service.stop()
