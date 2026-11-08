"""Synthetic copied evidence, not a native journey or owner API acceptance."""

import hashlib
import json

import pytest

from agency.admission.models import PilotError
from agency.factory.coding_models import (
    CodingEdit,
    CodingReceipt,
    canonical_sha256,
    coding_branch,
    literal_patch,
)
from tests.test_factory_coding_store import OWNER, PROFILE, prepared, rows
from tests.test_factory_coding_verification import report as synthetic_report


def copied_evidence(tmp_path, binding, *, status="passed"):
    from agency.admission.coding_store import CodingEvidence

    text = "x = 2\n" if status == "passed" else "x = 3\n"
    edits = (CodingEdit(path="m.py", old="x = 1\n", new=text),)
    report = synthetic_report(tmp_path / "absent-candidate", binding, text.encode("utf-8"))
    patch = literal_patch(text)
    receipt = CodingReceipt(
        binding=binding,
        plan_sha256=canonical_sha256([e.model_dump(mode="json") for e in edits]),
        forge_report_id=report.report_id,
        forge_report_sha256=canonical_sha256(report.model_dump(mode="json")),
        artifact_sha256=hashlib.sha256(text.encode()).hexdigest(),
        patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
        objective_status=status,
        reason="matched" if status == "passed" else "criterion_mismatch",
    )
    return CodingEvidence(
        edits=edits, forge_report=report, artifact_text=text, patch=patch, receipt=receipt
    )


@pytest.mark.asyncio
async def test_successful_publication_original_task_and_idempotent_replay(tmp_path):
    store, payload, binding, record, evidence = await ready(tmp_path)
    try:
        await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        await store.commit_coding_turn(record.sequence, binding=binding)
        saved = await store.get_coding_record(OWNER, binding.task_id)
        assert saved.admission.state == "COMPLETED" and saved.evidence_phase == "published"
        assert saved.evidence == evidence
        messages = await store.list_messages(OWNER, payload.thread_id)
        assert len(messages) == 2
        assert messages[0].content == payload.task and messages[0].id == record.user_message_id
        assert messages[1].id == record.assistant_message_id
        assert messages[1].content == json.loads(saved.admission.result_json)["response"]
        before = rows(store)
        await store.commit_coding_turn(record.sequence, binding=binding)
        replay = await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
        assert replay.replayed and rows(store) == before
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_failed_objective_cannot_be_completed(tmp_path):
    store, _payload, binding, record, evidence = await ready(tmp_path, status="failed")
    try:
        await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        before = rows(store)
        with pytest.raises(PilotError) as error:
            await store.commit_coding_turn(record.sequence, binding=binding)
        assert error.value.code == "conflict" and rows(store) == before
        saved = await store.get_coding_record(OWNER, binding.task_id)
        assert saved.evidence is None and saved.admission.result_json is None
        assert saved.admission.state == "RUNNING"
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("already_staged", [False, True])
async def test_uncertainty_preserves_evidence_and_is_idempotent(tmp_path, already_staged):
    store, _payload, binding, record, evidence = await ready(tmp_path)
    try:
        await store.reserve_attempt(record.sequence, kind="model", limit=1)
        if already_staged:
            await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        before = rows(store)
        await store.mark_coding_uncertain(
            record.sequence, binding=binding, phase="publication", reason="hook_unknown"
        )
        saved = await store.get_coding_record(OWNER, binding.task_id)
        assert saved.admission.state == "UNCERTAIN" and saved.evidence_phase == "uncertain"
        assert (
            saved.edits == evidence.edits
            and saved.evidence is None
            and saved.admission.result_json is None
        )
        assert saved.admission.model_attempts == 1
        after = rows(store)
        assert before["thread_messages"] == after["thread_messages"]
        await store.mark_coding_uncertain(
            record.sequence, binding=binding, phase="publication", reason="hook_unknown"
        )
        assert rows(store) == after
        with pytest.raises(PilotError):
            await store.mark_coding_uncertain(
                record.sequence, binding=binding, phase="mutation", reason="native_unknown"
            )
        with pytest.raises(PilotError):
            await store.commit_coding_turn(record.sequence, binding=binding)
        assert rows(store) == after
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_lost_publication_ack_cannot_downgrade_completed(tmp_path, monkeypatch):
    store, _payload, binding, record, evidence = await ready(tmp_path)
    try:
        await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        original = store._transaction

        def lost_ack(work, *, publication=False):
            result = original(work, publication=publication)
            if publication:
                raise PilotError("store")
            return result

        monkeypatch.setattr(store, "_transaction", lost_ack)
        with pytest.raises(PilotError):
            await store.commit_coding_turn(record.sequence, binding=binding)
        monkeypatch.setattr(store, "_transaction", original)
        before = rows(store)
        with pytest.raises(PilotError) as error:
            await store.mark_coding_uncertain(
                record.sequence, binding=binding, phase="publication", reason="hook_unknown"
            )
        assert error.value.code == "conflict" and rows(store) == before
        assert (await store.get_coding_record(OWNER, binding.task_id)).evidence == evidence
        await store.commit_coding_turn(record.sequence, binding=binding)
        assert rows(store) == before
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["cancelled", "deadline", "shutdown", "late_shutdown"])
async def test_existing_publication_fences_rollback_whole_coding_commit(
    tmp_path, monkeypatch, guard
):
    import sqlite3
    import time

    store, payload, binding, record, evidence = await ready(tmp_path)
    try:
        await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        if guard == "cancelled":
            await store.request_cancel(OWNER, binding.task_id, "cancel")
        elif guard == "deadline":
            with sqlite3.connect(store._db_path) as conn:
                conn.execute(
                    "UPDATE pilot_requests SET created_at=? WHERE sequence=?",
                    (time.time() - 121, record.sequence),
                )
        elif guard == "shutdown":
            store.fence_publication()
        else:
            original = store._event

            def fence_after_sql(conn, sequence, phase, code):
                original(conn, sequence, phase, code)
                if phase == "committed":
                    store.fence_publication()

            monkeypatch.setattr(store, "_event", fence_after_sql)
        before = rows(store)
        with pytest.raises(PilotError) as error:
            await store.commit_coding_turn(record.sequence, binding=binding)
        assert error.value.code in ("conflict", "uncertainty", "disabled")
        assert rows(store) == before
        assert not (await store.list_messages(OWNER, payload.thread_id))
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_historical_reopen_is_copied_data_only_and_readonly(tmp_path, monkeypatch):
    import ast
    import builtins
    import os
    import subprocess
    from pathlib import Path

    import agency.factory.coding_verification as checker
    from agency.admission.store import OwnerPilotStore

    store, payload, binding, record, evidence = await ready(tmp_path)
    await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
    await store.commit_coding_turn(record.sequence, binding=binding)
    path = store._db_path
    await store.close()
    reopened = OwnerPilotStore(path)
    await reopened.initialize()
    try:
        before = rows(reopened)

        def denied(*args, **kwargs):
            raise AssertionError("historical IO or regeneration forbidden")

        with monkeypatch.context() as guard:
            for target, names in [
                (
                    checker,
                    [
                        "checker_source_sha256",
                        "criteria_document",
                        "verify_literal_artifact",
                        "_read_artifact",
                        "_receipt",
                        "_matches_literal",
                    ],
                ),
                (Path, ["open", "stat", "lstat", "resolve"]),
                (builtins, ["open"]),
                (os, ["stat", "lstat", "scandir"]),
                (subprocess, ["run", "Popen"]),
                (ast, ["parse"]),
            ]:
                for name in names:
                    guard.setattr(target, name, denied)
            guard.setattr(reopened, "_workspace", denied)
            guard.setattr(reopened, "ensure_workspace", denied)
            saved = await reopened.get_coding_record(OWNER, binding.task_id, binding.workspace_id)
            assert saved.evidence.model_dump(mode="json") == evidence.model_dump(mode="json")
            replay = await reopened.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
            assert replay.replayed
        assert rows(reopened) == before and not reopened._fresh_turns
    finally:
        await reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage_first", [False, True])
async def test_restart_recovery_preserves_private_copies_without_retry(tmp_path, stage_first):
    from agency.admission.store import OwnerPilotStore

    store, payload, binding, record, evidence = await ready(tmp_path)
    if stage_first:
        await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
    before = rows(store)["pilot_coding_bindings"]
    path = store._db_path
    await store.close()
    store = OwnerPilotStore(path)
    await store.initialize()
    try:
        recovered = await store.recover_interrupted()
        assert [r.task_id for r in recovered] == [binding.task_id]
        assert rows(store)["pilot_coding_bindings"] == before
        saved = await store.get_coding_record(OWNER, binding.task_id)
        assert (
            saved.admission.state == "UNCERTAIN"
            and saved.evidence is None
            and saved.admission.result_json is None
        )
        with pytest.raises(PilotError):
            await store._claim(OWNER, payload.request_id, binding.task_id)
        assert not store._fresh_turns
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_completed_read_rebinds_original_transcript(tmp_path):
    import sqlite3

    store, _payload, binding, record, evidence = await ready(tmp_path)
    try:
        await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        await store.commit_coding_turn(record.sequence, binding=binding)
        with sqlite3.connect(store._db_path) as conn:
            conn.execute(
                "UPDATE thread_messages SET content=? WHERE id=?",
                ("changed transcript", record.user_message_id),
            )
        before = rows(store)
        with pytest.raises(PilotError) as error:
            await store.get_coding_record(OWNER, binding.task_id)
        assert error.value.code == "store" and rows(store) == before
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "blob",
        "digest",
        "duplicate",
        "deep",
        "missing_receipt",
        "missing_false_default",
        "missing_native_default",
        "coerced_integer",
        "owner",
        "thread",
        "profile",
        "plan",
        "artifact",
        "patch",
        "result_blob",
        "result_digest",
    ],
)
async def test_corrupt_staged_evidence_fails_closed(tmp_path, kind):
    import sqlite3

    store, _payload, binding, record, evidence = await ready(tmp_path)
    try:
        await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        with sqlite3.connect(store._db_path) as conn:
            text, digest = conn.execute(
                "SELECT evidence_json,evidence_digest FROM pilot_coding_bindings WHERE sequence=?",
                (record.sequence,),
            ).fetchone()
            data = json.loads(text)
            if kind == "blob":
                text = text.encode()
            elif kind == "digest":
                digest = "0" * 64
            elif kind in ("result_blob", "result_digest"):
                column = "result_json" if kind == "result_blob" else "result_digest"
                value = b"blob" if kind == "result_blob" else "0" * 64
                conn.execute(
                    "UPDATE pilot_requests SET " + column + "=? WHERE sequence=?",
                    (value, record.sequence),
                )
            else:
                if kind == "missing_receipt":
                    del data["receipt"]
                elif kind == "missing_false_default":
                    del data["receipt"]["tests_run"]
                elif kind == "missing_native_default":
                    del data["forge_report"]["tests"]
                elif kind == "coerced_integer":
                    data["forge_report"]["edits_applied"] = True
                elif kind in ("owner", "thread", "profile"):
                    field = {
                        "owner": "owner_id",
                        "thread": "thread_id",
                        "profile": "profile_sha256",
                    }[kind]
                    data["receipt"]["binding"][field] = "e" * 64 if kind == "profile" else "foreign"
                elif kind == "plan":
                    data["edits"][0]["new"] = "x = 3\n"
                elif kind == "artifact":
                    data["artifact_text"] = "x = 3\n"
                elif kind == "patch":
                    data["patch"] += "changed"
                text = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                if kind == "deep":
                    text = "[" * 30 + "0" + "]" * 30
                elif kind == "duplicate":
                    text = '{"receipt":null,"receipt":null}'
                digest = hashlib.sha256(text.encode()).hexdigest()
            conn.execute(
                "UPDATE pilot_coding_bindings SET evidence_json=?,evidence_digest=? WHERE sequence=?",
                (text, digest, record.sequence),
            )
        before = rows(store)
        with pytest.raises(PilotError) as error:
            await store.get_coding_record(OWNER, binding.task_id)
        assert error.value.code == "store" and rows(store) == before
        with pytest.raises(PilotError) as error:
            await store.commit_coding_turn(record.sequence, binding=binding)
        assert error.value.code == "store" and rows(store) == before
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_stage_snapshots_caller_evidence_before_await(tmp_path):
    import asyncio

    store, _payload, binding, record, evidence = await ready(tmp_path)
    expected = evidence.model_dump(mode="json")
    await store._gate.acquire()
    task = asyncio.create_task(
        store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
    )
    await asyncio.sleep(0)
    object.__setattr__(evidence.forge_report, "note", "caller changed report")
    object.__setattr__(evidence, "patch", "caller changed patch")
    store._gate.release()
    try:
        await task
        await store.commit_coding_turn(record.sequence, binding=binding)
        assert (await store.get_coding_record(OWNER, binding.task_id)).evidence.model_dump(
            mode="json"
        ) == expected
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("seam", ["stage", "publication", "uncertain"])
async def test_sql_failure_rolls_back_root_and_sidecar(tmp_path, seam):
    import sqlite3

    store, _payload, binding, record, evidence = await ready(tmp_path)
    try:
        if seam != "stage":
            await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        with sqlite3.connect(store._db_path) as conn:
            conn.execute(
                "CREATE TRIGGER refuse_update BEFORE UPDATE ON pilot_coding_bindings BEGIN SELECT RAISE(ABORT,'injected'); END"
            )
        before = rows(store)
        with pytest.raises(PilotError) as error:
            if seam == "stage":
                await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
            elif seam == "publication":
                await store.commit_coding_turn(record.sequence, binding=binding)
            else:
                await store.mark_coding_uncertain(
                    record.sequence, binding=binding, phase="publication", reason="hook_unknown"
                )
        assert error.value.code == "store" and rows(store) == before
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_complete_large_evidence_uses_journal_ceiling_not_pure_hash_limit(tmp_path):
    from agency.admission.coding_store import CodingEvidence

    store, payload, binding = await prepared(tmp_path)
    try:
        record = await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
        await store.transition(
            record.sequence, expected="ADMITTED", state="RUNNING", phase="running"
        )
        text = "#" + "a" * 7000 + "\nx = 2\n"
        edits = (CodingEdit(path="m.py", old="x = 1\n", new=text),)
        report = synthetic_report(
            tmp_path / "absent-large", binding, text.encode(), note="n" * 60000
        )
        patch = literal_patch(text)
        receipt = CodingReceipt(
            binding=binding,
            plan_sha256=canonical_sha256([e.model_dump(mode="json") for e in edits]),
            forge_report_id=report.report_id,
            forge_report_sha256=canonical_sha256(report.model_dump(mode="json")),
            artifact_sha256=hashlib.sha256(text.encode()).hexdigest(),
            patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
            objective_status="passed",
            reason="matched",
        )
        evidence = CodingEvidence(
            edits=edits, forge_report=report, artifact_text=text, patch=patch, receipt=receipt
        )
        assert len(evidence.model_dump_json().encode()) > 65536
        await store.record_coding_intent(
            record.sequence,
            binding=binding,
            edits=edits,
            plan_sha256=receipt.plan_sha256,
            branch=coding_branch(binding),
        )
        await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        await store.commit_coding_turn(record.sequence, binding=binding)
        assert (await store.get_coding_record(OWNER, binding.task_id)).evidence == evidence
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["unable", "native_failed"])
async def test_rejected_evidence_remains_private_on_failed_transition(tmp_path, verdict):
    store, _payload, binding, record, evidence = await ready(tmp_path)
    try:
        if verdict == "unable":
            receipt = evidence.receipt.model_copy(
                update={
                    "objective_status": "unable_to_verify",
                    "reason": "artifact_unavailable",
                    "artifact_sha256": None,
                }
            )
            evidence = evidence.model_copy(update={"artifact_text": None, "receipt": receipt})
        else:
            report = evidence.forge_report.model_copy(update={"gate": "FAIL"})
            receipt = evidence.receipt.model_copy(
                update={
                    "objective_status": "failed",
                    "reason": "forge_failed",
                    "forge_report_sha256": canonical_sha256(report.model_dump(mode="json")),
                }
            )
            evidence = evidence.model_copy(update={"forge_report": report, "receipt": receipt})
        await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        before = rows(store)["pilot_coding_bindings"]
        with pytest.raises(PilotError) as error:
            await store.commit_coding_turn(record.sequence, binding=binding)
        assert error.value.code == "conflict"
        await store.transition(record.sequence, expected="RUNNING", state="FAILED", phase="failed")
        saved = await store.get_coding_record(OWNER, binding.task_id)
        assert saved.admission.state == "FAILED" and saved.evidence is None
        assert saved.admission.result_json is None and saved.admission.result_digest is None
        assert rows(store)["pilot_coding_bindings"] == before
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase,reason",
    [
        ("unknown", "hook_unknown"),
        ("mutation", "private exception text"),
        (False, "cancelled"),
        ("publication", b"hook_unknown"),
    ],
)
async def test_uncertainty_accepts_only_frozen_phase_reason_enums(tmp_path, phase, reason):
    store, _payload, binding, record, _evidence = await ready(tmp_path)
    try:
        before = rows(store)
        with pytest.raises(PilotError) as error:
            await store.mark_coding_uncertain(
                record.sequence, binding=binding, phase=phase, reason=reason
            )
        assert error.value.code == "invalid_request" and rows(store) == before
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task",
    [
        "雪" * 8000,
        "[agency:calculator.v1]\nexpression=1+1\nReturn only the base-10 integer result, without prose or markup.",
    ],
)
async def test_original_long_or_calculator_looking_task_never_selects_calculator(tmp_path, task):
    store, payload, binding = await prepared(tmp_path, task)
    try:
        record = await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
        await store.transition(
            record.sequence, expected="ADMITTED", state="RUNNING", phase="running"
        )
        evidence = copied_evidence(tmp_path, binding)
        await store.record_coding_intent(
            record.sequence,
            binding=binding,
            edits=evidence.edits,
            plan_sha256=evidence.receipt.plan_sha256,
            branch=coding_branch(binding),
        )
        await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        await store.commit_coding_turn(record.sequence, binding=binding)
        messages = await store.list_messages(OWNER, payload.thread_id)
        assert messages[0].content == task
        saved = await store.get_coding_record(OWNER, binding.task_id)
        assert set(json.loads(saved.admission.result_json)) == {"task_id", "response", "assessment"}
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    ["missing", "false_flag", "constructed_report", "changed_binding", "missing_intent"],
)
async def test_unsolicited_or_malformed_copied_evidence_never_stages(tmp_path, corruption):
    store, _payload, binding, record, evidence = await ready(tmp_path)
    try:
        if corruption == "missing":
            evidence = evidence.model_copy(update={"receipt": None})
        elif corruption == "false_flag":
            evidence = evidence.model_copy(
                update={"receipt": evidence.receipt.model_copy(update={"tests_run": True})}
            )
        elif corruption == "constructed_report":
            evidence = evidence.model_copy(
                update={
                    "forge_report": evidence.forge_report.model_copy(update={"edits_applied": True})
                }
            )
        elif corruption == "changed_binding":
            evidence = evidence.model_copy(
                update={
                    "receipt": evidence.receipt.model_copy(
                        update={"binding": binding.model_copy(update={"owner_id": "foreign"})}
                    )
                }
            )
        else:
            import sqlite3

            with sqlite3.connect(store._db_path) as conn:
                conn.execute(
                    "UPDATE pilot_coding_bindings SET plan_json=NULL,plan_sha256=NULL,branch=NULL,evidence_phase='admitted' WHERE sequence=?",
                    (record.sequence,),
                )
        before = rows(store)
        with pytest.raises(PilotError) as error:
            await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        assert error.value.code in ("conflict", "invalid_request") and rows(store) == before
    finally:
        await store.close()


async def ready(tmp_path, *, status="passed"):
    store, payload, binding = await prepared(tmp_path)
    record = await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
    await store.transition(record.sequence, expected="ADMITTED", state="RUNNING", phase="running")
    evidence = copied_evidence(tmp_path, binding, status=status)
    await store.record_coding_intent(
        record.sequence,
        binding=binding,
        edits=evidence.edits,
        plan_sha256=evidence.receipt.plan_sha256,
        branch=coding_branch(binding),
    )
    return store, payload, binding, record, evidence


@pytest.mark.asyncio
async def test_atomic_copied_evidence_stage_conceals_private_result(tmp_path):
    store, _payload, binding, record, evidence = await ready(tmp_path)
    try:
        await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        saved = await store.get_coding_record(OWNER, binding.task_id)
        assert saved.evidence_phase == "staged"
        assert (
            saved.evidence is None
            and saved.admission.result_json is None
            and saved.admission.result_digest is None
        )
        raw = await store.get(OWNER, binding.task_id)
        staged = json.loads(raw.result_json)
        assert set(staged) == {"task_id", "response", "assessment"} and staged["assessment"] is None
        assert (
            "tests not run" in staged["response"] and "release not authorized" in staged["response"]
        )
        assert evidence.patch in staged["response"]
        before = rows(store)
        await store.stage_coding_result(record.sequence, binding=binding, evidence=evidence)
        assert rows(store) == before
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("seam", ["stage", "commit", "ordinary_replay"])
async def test_generic_paths_cannot_bypass_coding_binding(tmp_path, seam):
    store, payload, binding = await prepared(tmp_path)
    try:
        record = await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
        await store.transition(
            record.sequence, expected="ADMITTED", state="RUNNING", phase="running"
        )
        result = {"task_id": binding.task_id, "response": "unverified patch", "assessment": None}
        if seam == "commit":
            # Seed a legacy-shaped staged result, as a buggy generic caller could.
            from agency.admission.store import _canonical

            text, digest = _canonical(result)
            import sqlite3

            with sqlite3.connect(store._db_path) as conn:
                conn.execute(
                    "UPDATE pilot_requests SET result_json=?,result_digest=? WHERE sequence=?",
                    (text, digest, record.sequence),
                )
        before = rows(store)
        with pytest.raises(PilotError) as error:
            if seam == "stage":
                await store.stage_result(record.sequence, result)
            elif seam == "commit":
                await store.commit_turn(record.sequence, text=result["response"], assessment=None)
            else:
                await store.admit(
                    OWNER,
                    payload.request_id,
                    operation="turn",
                    payload=json.loads(record.input_json),
                )
        assert error.value.code == "conflict"
        assert rows(store) == before
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_oversized_copied_report_refusal_preserves_business_rows(tmp_path):
    store, payload, binding, record, evidence = await ready(tmp_path)
    try:
        # Deliberately malformed copied report, not a newly valid receipt.
        report = evidence.forge_report.model_copy(update={"caveats": ("a" * 32768,) * 32})
        oversized = evidence.model_copy(update={"forge_report": report})
        before = rows(store)
        with pytest.raises(PilotError) as error:
            await store.stage_coding_result(record.sequence, binding=binding, evidence=oversized)
        assert error.value.code == "invalid_request"
        assert str(error.value) == str(PilotError("invalid_request"))
        assert rows(store) == before
        saved = await store.get_coding_record(OWNER, binding.task_id)
        assert saved.evidence_phase == "intent" and saved.evidence is None
        assert saved.admission.result_json is None and saved.admission.state == "RUNNING"
        assert await store.list_messages(OWNER, payload.thread_id) == []
    finally:
        await store.close()
