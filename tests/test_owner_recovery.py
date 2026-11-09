"""Offline recovery against synthetic stores only; never real acceptance."""

# ruff: noqa: F811 -- pytest fixture import is intentionally shadowed by injection

import importlib
import importlib.util
import inspect
import sqlite3
from contextlib import closing

import pytest
from pydantic import SecretStr

from agency.admission.models import OwnerTaskRequest, PilotError
from agency.kernel.audit import AuditEntry, BetaAuditLog
from tests.test_owner_service import (  # noqa: F401 -- pytest fixture
    concrete,
    make_service,
    terminal,
)

KEY = SecretStr("SYNTHETIC_OWNER_TOKEN_" + "x" * 43)


def dump(path):
    with closing(sqlite3.connect(path)) as db:
        return tuple(db.iterdump())


async def seeded(c):
    service = make_service(c)
    await service.start()
    _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
    row = await c["store"].admit(
        c["owner"],
        "unknown",
        operation="turn",
        payload={"thread_id": thread.id, "message": "historical unknown"},
    )
    await c["store"].transition(
        row.sequence, expected="ADMITTED", state="UNCERTAIN", phase="uncertain"
    )
    intent = AuditEntry(
        agent="metered-model",
        task="owner-pilot",
        action="model.generate",
        result="pending",
        evidence={"phase": "intent", "call_id": "a" * 32},
    )
    assert await c["audit"].claim_intent(intent)
    assert (await service.capabilities(c["owner"])).uncertainty_blocked
    with pytest.raises(PilotError, match="uncertain"):
        await service.submit(
            c["owner"], OwnerTaskRequest(request_id="blocked", thread_id=thread.id, message="new")
        )
    return service, thread


def recovery(c, **kwargs):
    spec = importlib.util.find_spec("agency.admission.owner_recovery")
    assert spec is not None, "Explicit offline owner acknowledgement is unavailable"
    return importlib.import_module("agency.admission.owner_recovery").OwnerRecovery(
        store=c["store"],
        budget=c["budget"],
        audit=c["audit"],
        principal=c["owner"],
        key=KEY,
        **(
            {"operator": True}
            if "operator"
            in inspect.signature(
                importlib.import_module("agency.admission.owner_recovery").OwnerRecovery
            ).parameters
            else {}
        ),
        **kwargs,
    )


async def test_offline_receipt_preserves_unknown_and_raw_guards(concrete):
    c = concrete
    service, _ = await seeded(c)
    try:
        coord = recovery(c)
        before = {name: dump(c[name]._db_path) for name in ["store", "audit"]}
        quota = dump(c["backend"]._db_path)
        assert await coord.blocked() is True
        await coord.prepare()
        snap = await coord.preview()
        assert len(snap["uncertain_turns"]) == len(snap["pending_intents"]) == 1
        receipt = await coord.accept(snap["digest"], "accept-1", accept_residual_uncertainty=True)
        assert receipt["evidence"]["phase"] == "owner_ack"
        assert receipt["action"] == "owner.acknowledge_uncertainty"
        assert await coord.blocked() is False
        assert await c["store"].has_uncertainty(c["owner"]) is True
        assert await c["audit"].has_pending_calls() is True
        assert (
            await coord.accept(snap["digest"], "accept-1", accept_residual_uncertainty=True)
            == receipt
        )
        assert c["requests"] == []
        # Only marker DDL/data may be added to pilot/quota; old rows remain byte-identical.
        assert all(
            line in dump(c["store"]._db_path)
            for line in before["store"]
            if line not in ("BEGIN TRANSACTION;", "COMMIT;")
        )
        assert all(
            line in dump(c["backend"]._db_path)
            for line in quota
            if line not in ("BEGIN TRANSACTION;", "COMMIT;")
        )
        assert (
            await BetaAuditLog.claim_intent(
                c["audit"],
                AuditEntry(
                    action="model.generate",
                    result="pending",
                    evidence={"phase": "intent", "call_id": "b" * 32},
                ),
            )
            is False
        )
    finally:
        await service.stop()


async def test_owner_model_tool_loop_and_original_replay(concrete):
    from agency.admission.service import OwnerPilotService

    c = concrete
    service, thread = await seeded(c)
    coord = recovery(c)
    await coord.prepare()
    snap = await coord.preview()
    await coord.accept(snap["digest"], "accept-loop", accept_residual_uncertainty=True)
    try:
        assert "recovery_key" in inspect.signature(OwnerPilotService).parameters, (
            "Service cannot opt in to signed owner recovery"
        )
        # Retain the same lifetime stores; no acceptance or privileged tool is exposed by service.
        service = OwnerPilotService(
            butler=c["butler"],
            store=c["store"],
            budget=c["budget"],
            audit=c["audit"],
            provider_config=c["config"],
            owner_id="owner",
            recovery_key=KEY,
        )
        await service.start()
        assert not (await service.capabilities(c["owner"])).uncertainty_blocked
        replay = await service.submit(
            c["owner"],
            OwnerTaskRequest(
                request_id="unknown", thread_id=thread.id, message="historical unknown"
            ),
        )
        assert replay.state == "UNCERTAIN" and replay.replayed and not c["requests"]
        task = await service.submit(
            c["owner"],
            OwnerTaskRequest(request_id="new-task", thread_id=thread.id, message="calculate 2+3"),
        )
        result = await terminal(service, c["owner"], task.task_id)
        assert result.state == "COMPLETED" and result.response == "five"
        assert result.usage.model_attempts == 2 and result.usage.tool_attempts == 1
        assert len(c["requests"]) == 2
        # Exhausted synthetic replies produce another unknown, not another acceptance.
        next_task = await service.submit(
            c["owner"],
            OwnerTaskRequest(
                request_id="new-unknown", thread_id=thread.id, message="fail synthetically"
            ),
        )
        assert (await terminal(service, c["owner"], next_task.task_id)).state == "UNCERTAIN"
        assert (await service.capabilities(c["owner"])).uncertainty_blocked
        assert len(c["requests"]) == 3
        assert await c["audit"].has_pending_calls()
    finally:
        await service.stop()


@pytest.mark.parametrize(
    "case",
    [
        "wrong_subject",
        "wrong_workspace",
        "wrong_key",
        "blob",
        "null",
        "duplicate_json",
        "duplicate_call",
        "orphan_outcome",
        "overflow",
        "partial_marker",
        "signature",
        "changed_row",
        "active",
        "staged",
        "shutdown",
    ],
)
async def test_recovery_adversarial_fail_closed(concrete, monkeypatch, case):
    import agency.admission.owner_recovery as module
    from agency.admission.models import OwnerPrincipal

    c = concrete
    service, _ = await seeded(c)
    coord = recovery(c)
    try:
        await coord.prepare()
        snap = await coord.preview()
        await coord.accept(snap["digest"], "ack", accept_residual_uncertainty=True)
        if case == "wrong_subject":
            coord = module.OwnerRecovery(
                store=c["store"],
                budget=c["budget"],
                audit=c["audit"],
                principal=OwnerPrincipal("owner", "local", "other"),
                key=KEY,
            )
        elif case == "wrong_workspace":
            coord = recovery(c, workspace_id="foreign")
        elif case == "wrong_key":
            coord = module.OwnerRecovery(
                store=c["store"],
                budget=c["budget"],
                audit=c["audit"],
                principal=c["owner"],
                key=SecretStr("wrong-key"),
            )
        elif case == "overflow":
            monkeypatch.setattr(module, "MAX_ROWS", 1)
        elif case == "shutdown":
            c["store"].fence_publication()
        else:
            path = (
                c["store"]._db_path
                if case in ("changed_row", "active", "staged")
                else c["audit"]._db_path
            )
            with closing(sqlite3.connect(path)) as db, db:
                if case == "blob":
                    db.execute(
                        "UPDATE audit_entries SET evidence=? WHERE result='pending'", (b"{}",)
                    )
                elif case == "null":
                    db.execute("UPDATE audit_entries SET evidence=NULL WHERE result='pending'")
                elif case == "duplicate_json":
                    db.execute(
                        "UPDATE audit_entries SET evidence=? WHERE result='pending'",
                        ('{"phase":"intent","phase":"outcome","call_id":"' + "a" * 32 + '"}',),
                    )
                elif case == "duplicate_call":
                    db.execute(
                        "INSERT INTO audit_entries SELECT 'duplicate', timestamp,agent,task,target,authorization,capability,action,result,evidence,model,model_version,tool_version,environment FROM audit_entries WHERE result='pending'"
                    )
                elif case == "orphan_outcome":
                    db.execute(
                        "INSERT INTO audit_entries SELECT 'orphan', timestamp,agent,task,target,authorization,capability,action,'ok',?,model,model_version,tool_version,environment FROM audit_entries WHERE result='pending'",
                        ('{"phase":"outcome","call_id":"' + "c" * 32 + '"}',),
                    )
                elif case == "partial_marker":
                    db.execute("DROP TABLE owner_recovery_identity")
                elif case == "signature":
                    db.execute(
                        "UPDATE audit_entries SET target='forged' WHERE action='owner.acknowledge_uncertainty'"
                    )
                elif case == "changed_row":
                    db.execute(
                        "UPDATE pilot_requests SET phase='changed' WHERE request_id='unknown'"
                    )
                elif case == "active":
                    db.execute(
                        "UPDATE pilot_requests SET state='ADMITTED' WHERE request_id='unknown'"
                    )
                elif case == "staged":
                    db.execute(
                        "UPDATE pilot_requests SET result_json='{}' WHERE request_id='unknown'"
                    )
        before = {name: dump(c[name]._db_path) for name in ("store", "audit", "backend")}
        with pytest.raises(PilotError):
            await coord.accept(snap["digest"], "must-not-accept", accept_residual_uncertainty=True)
        assert before == {name: dump(c[name]._db_path) for name in before}
        assert not c["requests"]
    finally:
        await service.stop()


async def test_runtime_coordinator_cannot_issue_acceptance(concrete):
    c = concrete
    service, _ = await seeded(c)
    try:
        coord = recovery(c)
        await coord.prepare()
        snap = await coord.preview()
        runtime = type(coord)(
            store=c["store"], budget=c["budget"], audit=c["audit"], principal=c["owner"], key=KEY
        )
        with pytest.raises(PilotError):
            await runtime.accept(snap["digest"], "runtime", accept_residual_uncertainty=True)
        with pytest.raises(PilotError):
            await runtime.prepare()
    finally:
        await service.stop()
