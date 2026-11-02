"""Adversarial durable calculator binding; synthetic HTTP, real service/store/tool."""

# ruff: noqa: F811 -- pytest fixture import is intentionally shadowed by injection
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest

from agency.admission.calculator_verification import canonical_message, make_receipt
from agency.admission.models import OwnerPrincipal, OwnerTaskRequest, PilotError
from tests.test_owner_service import concrete, make_service, terminal  # noqa: F401


def request(thread_id, expression="2+3", request_id="turn"):
    return OwnerTaskRequest(
        request_id=request_id, thread_id=thread_id, message=canonical_message(expression)
    )


@pytest.mark.parametrize("fault", ["body", "thread", "digest"])
async def test_corrupt_admission_never_reaches_model(concrete, monkeypatch, fault):
    c = concrete
    service = make_service(c)
    original = c["store"].admit

    async def corrupt(*a, **kw):
        row = await original(*a, **kw)
        payload = json.loads(row.input_json)
        if fault == "body":
            payload["message"] = canonical_message("2+4")
            return replace(row, input_json=json.dumps(payload))
        if fault == "thread":
            payload["thread_id"] = "other-thread"
            text = json.dumps(payload)
            return replace(
                row, input_json=text, input_digest=hashlib.sha256(text.encode()).hexdigest()
            )
        return replace(row, input_digest="0" * 64)

    monkeypatch.setattr(c["store"], "admit", corrupt)
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Checked")
        task = await service.submit(c["owner"], request(thread.id))
        await asyncio.shield(service._execution)
        view = await service.get_task(c["owner"], task.task_id)
        assert c["requests"] == [], "corrupt admitted criteria dispatched to a model"
        assert view.state != "COMPLETED"
        assert await service.list_messages(c["owner"], thread.id) == []
    finally:
        await service.stop()


async def running(c, message):
    await c["store"].initialize()
    _, thread = await c["store"].create_thread(c["owner"], "thread", "Checked")
    row = await c["store"].admit(
        c["owner"], "turn", operation="turn", payload={"thread_id": thread.id, "message": message}
    )
    await c["store"].transition(row.sequence, expected="ADMITTED", state="RUNNING", phase="running")
    return row


async def test_new_opt_in_cannot_stage_or_commit_without_receipt(concrete):
    c = concrete
    row = await running(c, canonical_message("2+3"))
    with pytest.raises(PilotError):
        await c["store"].stage_result(
            row.sequence, {"task_id": row.task_id, "response": "5", "assessment": None}
        )
    assert (await c["store"].get(c["owner"], row.task_id)).result_json is None


async def test_opt_in_grant_rejects_unrelated_tools(concrete):
    c = concrete
    c["replies"][:] = ['{"action":{"name":"utc_time","args":{}}}', '{"final":"5"}']
    service = make_service(c)
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Checked")
        task = await service.submit(c["owner"], request(thread.id))
        view = await terminal(service, c["owner"], task.task_id)
        assert view.usage.tool_attempts == 0, "calculator opt-in granted unrelated tool authority"
    finally:
        await service.stop()


async def test_verification_read_honors_explicit_workspace_scope(concrete):
    from agency.admission.service import OwnerPilotService

    c = concrete
    row = await running(c, canonical_message("2+3"))
    receipt = make_receipt(
        task_id=row.task_id,
        request_id=row.request_id,
        thread_id=row.thread_id,
        expression="2+3",
        response="5",
    ).model_dump(mode="json")
    await c["store"].stage_result(
        row.sequence,
        {"task_id": row.task_id, "response": "5", "assessment": None, "verification": receipt},
    )
    await c["store"].commit_turn(row.sequence, text="5", assessment=None, verification=receipt)
    restricted = OwnerPilotService(
        butler=c["butler"],
        store=c["store"],
        budget=c["budget"],
        audit=c["audit"],
        provider_config=c["config"],
        owner_id="owner",
        workspace_id="not-the-owner-workspace",
    )
    try:
        await restricted.start()
        with pytest.raises(PilotError) as denied:
            await restricted.get_verification(c["owner"], row.task_id)
        assert denied.value.code == "forbidden"
    finally:
        await restricted.stop()


@pytest.mark.parametrize(
    "fault", ["ordinary", "task", "request", "thread", "expression", "output", "verdict"]
)
async def test_store_rejects_unsolicited_or_forged_receipt(concrete, fault):
    c = concrete
    row = await running(c, "ordinary" if fault == "ordinary" else canonical_message("2+3"))
    receipt = make_receipt(
        task_id=row.task_id,
        request_id=row.request_id,
        thread_id=row.thread_id,
        expression="2+3",
        response="5",
    ).model_dump(mode="json")
    if fault in {"task", "request", "thread"}:
        receipt[fault + "_id"] = "different"
    elif fault == "expression":
        receipt = make_receipt(
            task_id=row.task_id,
            request_id=row.request_id,
            thread_id=row.thread_id,
            expression="2+4",
            response="5",
        ).model_dump(mode="json")
    elif fault == "output":
        receipt["output_sha256"] = hashlib.sha256(b" 5").hexdigest()
    elif fault == "verdict":
        receipt["status"], receipt["reason"] = "failed", "mismatch"
    with pytest.raises(PilotError):
        await c["store"].stage_result(
            row.sequence,
            {"task_id": row.task_id, "response": "5", "assessment": None, "verification": receipt},
        )
    assert (await c["store"].get(c["owner"], row.task_id)).result_json is None


@pytest.mark.parametrize(
    "fault", ["missing", "changed", "input_digest", "result_digest", "cancel", "shutdown"]
)
async def test_commit_rebinds_receipt_and_preserves_publication_fences(concrete, fault):
    c = concrete
    row = await running(c, canonical_message("2+3"))
    receipt = make_receipt(
        task_id=row.task_id,
        request_id=row.request_id,
        thread_id=row.thread_id,
        expression="2+3",
        response="5",
    ).model_dump(mode="json")
    await c["store"].stage_result(
        row.sequence,
        {"task_id": row.task_id, "response": "5", "assessment": None, "verification": receipt},
    )
    if fault in {"input_digest", "result_digest"}:
        with sqlite3.connect(c["store"]._db_path) as db:
            db.execute(
                f"UPDATE pilot_requests SET {fault}=? WHERE sequence=?", ("0" * 64, row.sequence)
            )
    elif fault == "changed":
        receipt["request_id"] = "different"
    elif fault == "cancel":
        await c["store"].request_cancel(c["owner"], row.task_id, "cancel")
    elif fault == "shutdown":
        c["store"].fence_publication()
    with pytest.raises(PilotError):
        await c["store"].commit_turn(
            row.sequence,
            text="5",
            assessment=None,
            verification=None if fault == "missing" else receipt,
        )
    assert (await c["store"].get(c["owner"], row.task_id)).state != "COMPLETED"
    assert await c["store"].list_messages(c["owner"], row.thread_id) == []


async def test_private_staged_pass_never_appears_as_published_receipt(concrete, monkeypatch):
    c = concrete
    c["replies"][1] = '{"final":"5"}'
    service = make_service(c)
    original = c["audit"].append_beta

    async def fail_publication(entry):
        if entry.action == "owner.gateway_result":
            row = (await c["store"].list_tasks(c["owner"]))[0]
            assert json.loads(row.result_json)["verification"]["status"] == "passed"
            snapshot = await service.get_verification(c["owner"], row.task_id)
            assert snapshot["reason"] == "not_completed" and snapshot["receipt"] is None
            raise OSError("synthetic gateway failure")
        return await original(entry)

    monkeypatch.setattr(c["audit"], "append_beta", fail_publication)
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Checked")
        task = await service.submit(c["owner"], request(thread.id))
        result = await terminal(service, c["owner"], task.task_id)
        assert result.state == "UNCERTAIN"
        snapshot = await service.get_verification(c["owner"], task.task_id)
        assert snapshot["receipt"] is None and snapshot["reason"] == "not_completed"
        assert await service.list_messages(c["owner"], thread.id) == []
    finally:
        await service.stop()


async def test_prior_thread_history_cannot_redefine_the_admitted_criteria(concrete):
    c = concrete
    c["replies"][:] = ['{"final":"6"}', '{"final":"5"}']
    service = make_service(c)
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Checked")
        for expression, expected, key in [("2+4", "6", "old"), ("2+3", "5", "new")]:
            task = await service.submit(c["owner"], request(thread.id, expression, key))
            view = await terminal(service, c["owner"], task.task_id)
            assert view.state == "COMPLETED"
            saved = await service.get_verification(c["owner"], task.task_id)
            assert saved["receipt"]["expression"] == expression
            assert saved["receipt"]["expected"] == expected
            assert saved["receipt"]["status"] == "passed"
        assert len(c["requests"]) == 2
    finally:
        await service.stop()


async def test_old_completed_opt_in_has_no_retroactive_receipt_and_reads_do_not_write(concrete):
    c = concrete
    c["replies"][1] = '{"final":"5"}'
    service = make_service(c)
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Checked")
        payload = request(thread.id)
        task = await service.submit(c["owner"], payload)
        assert (await terminal(service, c["owner"], task.task_id)).state == "COMPLETED"
        # Explicit old-row fixture: restore the historical three-key payload.
        with sqlite3.connect(c["store"]._db_path) as db:
            result = json.loads(
                db.execute(
                    "SELECT result_json FROM pilot_requests WHERE task_id=?", (task.task_id,)
                ).fetchone()[0]
            )
            del result["verification"]
            text = json.dumps(
                result, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
            db.execute(
                "UPDATE pilot_requests SET result_json=?,result_digest=? WHERE task_id=?",
                (text, hashlib.sha256(text.encode()).hexdigest(), task.task_id),
            )
        with sqlite3.connect(c["store"]._db_path) as watch:
            before = watch.execute("PRAGMA data_version").fetchone()[0]
            snapshot = await service.get_verification(c["owner"], task.task_id)
            assert snapshot["reason"] == "legacy_result" and snapshot["receipt"] is None
            assert (await service.get_verification(c["owner"], task.task_id)) == snapshot
            assert watch.execute("PRAGMA data_version").fetchone()[0] == before
        assert (await service.submit(c["owner"], payload)).replayed
        assert len(c["requests"]) == 2
        foreign = OwnerPrincipal("foreign", "local", "foreign")
        with pytest.raises(PilotError) as denied:
            await service.get_verification(foreign, task.task_id)
        assert denied.value.code == "unauthorized"
    finally:
        await service.stop()
