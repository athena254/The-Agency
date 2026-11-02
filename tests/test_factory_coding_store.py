"""Durable storage-only fixtures; no native execution or approval attestation."""

import asyncio
import json
import sqlite3

import pytest

from agency.admission.models import OwnerPrincipal, PilotError
from agency.admission.store import OwnerPilotStore
from agency.factory.coding_models import (
    CodingBinding,
    CodingEdit,
    FactoryCodingRequest,
    canonical_sha256,
    coding_branch,
)


@pytest.mark.asyncio
async def test_intent_is_durable_bound_and_idempotent(tmp_path):
    store, payload, binding = await prepared(tmp_path)
    try:
        record = await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
        await store.transition(
            record.sequence, expected="ADMITTED", state="RUNNING", phase="running"
        )
        edits = (CodingEdit(path="m.py", old="x = 1\n", new="x = 2\n"),)
        digest = canonical_sha256([e.model_dump(mode="json") for e in edits])
        branch = coding_branch(binding)
        await store.record_coding_intent(
            record.sequence, binding=binding, edits=edits, plan_sha256=digest, branch=branch
        )
        saved = await store.get_coding_record(OWNER, binding.task_id)
        assert saved.edits == edits and saved.plan_sha256 == digest and saved.branch == branch
        assert saved.evidence_phase == "intent"
        before = rows(store)
        await store.record_coding_intent(
            record.sequence, binding=binding, edits=edits, plan_sha256=digest, branch=branch
        )
        assert rows(store) == before
        changed = (CodingEdit(path="m.py", old="1", new="3"),)
        with pytest.raises(PilotError):
            await store.record_coding_intent(
                record.sequence,
                binding=binding,
                edits=changed,
                plan_sha256=canonical_sha256([e.model_dump(mode="json") for e in changed]),
                branch=branch,
            )
        assert rows(store) == before
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["rollback", "lost_ack", "cancelled_ack"])
async def test_admission_atomicity_and_fresh_claim(tmp_path, monkeypatch, boundary):
    import threading

    store, payload, binding = await prepared(tmp_path)
    before = rows(store)
    try:
        if boundary == "rollback":
            with sqlite3.connect(store._db_path) as conn:
                conn.execute(
                    "CREATE TRIGGER refuse_coding BEFORE INSERT ON pilot_coding_bindings BEGIN SELECT RAISE(ABORT,'injected'); END"
                )
            with pytest.raises(PilotError) as error:
                await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
            assert error.value.code == "store" and rows(store) == before
        else:
            original = store._transaction
            committed, release = threading.Event(), threading.Event()

            def ambiguous(work, *, publication=False):
                result = original(work, publication=publication)
                committed.set()
                if boundary == "cancelled_ack":
                    assert release.wait(10)
                else:
                    raise PilotError("store")
                return result

            monkeypatch.setattr(store, "_transaction", ambiguous)
            task = asyncio.create_task(
                store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
            )
            if boundary == "cancelled_ack":
                assert await asyncio.to_thread(committed.wait, 10)
                task.cancel()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(PilotError):
                    await task
            monkeypatch.setattr(store, "_transaction", original)
            replay = await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
            assert replay.replayed
            with pytest.raises(PilotError) as error:
                await store._claim(OWNER, payload.request_id, binding.task_id)
            assert error.value.code == "conflict"
        assert not store._fresh_turns
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_ordinary_marker_is_not_promotable(tmp_path):
    store, payload, binding = await prepared(tmp_path)
    try:
        message = "factory-coding.v1:" + canonical_sha256(payload.model_dump(mode="json"))
        ordinary = await store.admit(
            OWNER,
            payload.request_id,
            operation="turn",
            payload={"thread_id": payload.thread_id, "message": message},
        )
        assert await store.get_coding_record(OWNER, ordinary.task_id) is None
        before = rows(store)
        with pytest.raises(PilotError) as error:
            await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
        assert error.value.code == "conflict" and rows(store) == before
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_admission_snapshots_before_await(tmp_path):
    store, payload, binding = await prepared(tmp_path)
    profile = json.loads(json.dumps(PROFILE))
    await store._gate.acquire()
    task = asyncio.create_task(
        store.admit_coding(OWNER, payload, binding, profile_snapshot=profile)
    )
    await asyncio.sleep(0)
    object.__setattr__(payload, "task", "changed by caller")
    object.__setattr__(binding, "task_id", "changed-task")
    profile["nonsecret"]["limit"] = 99
    store._gate.release()
    try:
        record = await task
        saved = await store.get_coding_record(OWNER, "server-task")
        assert record.task_id == "server-task"
        assert saved.request.task == "set x to 2" and saved.profile_snapshot == PROFILE
        saved.profile_snapshot["nonsecret"]["limit"] = -1
        assert (await store.get_coding_record(OWNER, "server-task")).profile_snapshot == PROFILE
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "column",
    [
        "request_json",
        "request_digest",
        "binding_json",
        "binding_digest",
        "profile_json",
        "profile_digest",
    ],
)
@pytest.mark.parametrize("corruption", ["blob", "wrong_digest", "deep", "missing", "duplicate"])
async def test_corrupt_admitted_snapshots_fail_closed(tmp_path, column, corruption):
    store, payload, binding = await prepared(tmp_path)
    try:
        record = await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
        with sqlite3.connect(store._db_path) as conn:
            raw = conn.execute(
                "SELECT " + column + " FROM pilot_coding_bindings WHERE sequence=?",
                (record.sequence,),
            ).fetchone()[0]
            value = (
                raw.encode()
                if corruption == "blob"
                else ("0" * 64 if corruption == "wrong_digest" else "[" * 20 + "0" + "]" * 20)
            )
            if corruption == "missing":
                value = "{}"
            elif corruption == "duplicate":
                value = '{"key":"one","key":"two"}'
            conn.execute(
                "UPDATE pilot_coding_bindings SET " + column + "=? WHERE sequence=?",
                (value, record.sequence),
            )
        before = rows(store)
        with pytest.raises(PilotError) as error:
            await store.get_coding_record(OWNER, binding.task_id)
        assert error.value.code == "store" and rows(store) == before
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["mapping", "constructed", "copied"])
async def test_intent_rejects_malformed_binding_as_pilot_error(tmp_path, bad):
    store, payload, binding = await prepared(tmp_path)
    try:
        record = await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
        edits = (CodingEdit(path="m.py", old="1", new="2"),)
        invalid = (
            binding.model_dump(mode="json")
            if bad == "mapping"
            else binding.model_copy(update={"task_id": False})
        )
        if bad == "constructed":
            invalid = CodingBinding.model_construct(
                **(binding.model_dump(mode="python") | {"profile_sha256": b"invalid"})
            )
        before = rows(store)
        with pytest.raises(PilotError) as error:
            await store.record_coding_intent(
                record.sequence,
                binding=invalid,
                edits=edits,
                plan_sha256=canonical_sha256([e.model_dump(mode="json") for e in edits]),
                branch=coding_branch(binding),
            )
        assert error.value.code == "invalid_request" and rows(store) == before
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_scoped_historical_read_does_not_provision(tmp_path, monkeypatch):
    store, payload, binding = await prepared(tmp_path)
    try:
        await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)

        def denied(*args, **kwargs):
            raise AssertionError("no provisioning")

        monkeypatch.setattr(store, "_workspace", denied)
        before = rows(store)
        for principal, task_id, workspace, code in [
            (OwnerPrincipal("foreign", "local", "foreign"), binding.task_id, "", "not_found"),
            (OWNER, "missing", "", "not_found"),
            (OWNER, binding.task_id, "foreign", "forbidden"),
        ]:
            with pytest.raises(PilotError) as error:
                await store.get_coding_record(principal, task_id, workspace)
            assert error.value.code == code
        assert (
            await store.get_coding_record(OWNER, binding.task_id, binding.workspace_id)
        ).binding == binding
        assert rows(store) == before
    finally:
        await store.close()


OWNER = OwnerPrincipal("owner", "local", "owner")
PROFILE = {"profile_id": "literal-assignment-v1", "nonsecret": {"limit": 1}}


async def prepared(tmp_path, task="set x to 2"):
    store = OwnerPilotStore(str(tmp_path / "pilot.db"))
    await store.initialize()
    _, thread = await store.create_thread(OWNER, "new-thread", "Coding fixture")
    workspace = await store.ensure_workspace(OWNER)
    payload = FactoryCodingRequest(
        request_id="coding-request",
        thread_id=thread.id,
        profile_id="literal-assignment-v1",
        task=task,
    )
    binding = CodingBinding(
        owner_id=OWNER.owner_id,
        workspace_id=workspace,
        request_id=payload.request_id,
        task_id="server-task",
        thread_id=thread.id,
        profile_id=payload.profile_id,
        profile_sha256=canonical_sha256(PROFILE),
        blueprint_id="approved-fixture",
        blueprint_version="1",
        runtime_agent_id="runtime",
        base_commit="b" * 40,
        checker_sha256="c" * 64,
        criteria_sha256="d" * 64,
    )
    return store, payload, binding


def rows(store):
    with sqlite3.connect(store._db_path) as conn:
        return {
            name: conn.execute("SELECT * FROM " + name + " ORDER BY rowid").fetchall()
            for name in [
                "pilot_requests",
                "pilot_coding_bindings",
                "pilot_events",
                "thread_messages",
                "threads",
                "pilot_subjects",
            ]
        }


@pytest.mark.asyncio
async def test_atomic_typed_admission_full_task_and_readonly_replay(tmp_path):
    store, payload, binding = await prepared(tmp_path, "雪" * 8000)
    try:
        record = await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
        assert record.task_id == binding.task_id
        assert json.loads(record.input_json) == {
            "thread_id": payload.thread_id,
            "message": "factory-coding.v1:" + canonical_sha256(payload.model_dump(mode="json")),
        }
        saved = await store.get_coding_record(OWNER, binding.task_id)
        assert saved.request == payload and saved.binding == binding
        assert saved.profile_snapshot == PROFILE and saved.evidence is None
        assert saved.evidence_phase == "admitted"
        before = rows(store)
        again = await store.admit_coding(OWNER, payload, binding, profile_snapshot=PROFILE)
        assert again.replayed and rows(store) == before
        changed = payload.model_copy(update={"task": "different"})
        with pytest.raises(PilotError, match="conflicts"):
            await store.admit_coding(OWNER, changed, binding, profile_snapshot=PROFILE)
        assert rows(store) == before
    finally:
        await store.close()


def test_journal_refuses_repeated_string_budget_before_serialization(monkeypatch):
    from agency.admission import coding_store

    maximum = 524288
    chunk = "a" * 32768
    # Shared references still occupy bytes at every occurrence in the journal.
    caveats = [chunk] * 32
    value = {"forge_report": {"caveats": caveats}}
    assert len(chunk.encode("utf-8")) < maximum
    assert len(caveats) < 8192 and len(chunk.encode("utf-8")) * len(caveats) > maximum
    serializations = []
    original = coding_store.json.dumps

    def observed(*args, **kwargs):
        serializations.append(True)
        return original(*args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(coding_store.json, "dumps", observed)
        with pytest.raises(ValueError, match="invalid coding journal"):
            coding_store.encode(value, maximum=maximum)
    assert serializations == [], "oversized journal reached full json.dumps"


@pytest.mark.parametrize("placement", ["values", "keys"])
def test_journal_counts_aggregate_utf8_occurrences_before_serialization(monkeypatch, placement):
    from agency.admission import coding_store

    maximum = 524288
    chunk = "雪" * 8192
    assert len(chunk) * 32 < maximum < len(chunk.encode("utf-8")) * 32
    value = (
        {"caveats": [chunk] * 32}
        if placement == "values"
        else [{chunk: None} for _ in range(32)]
    )
    serializations = []
    original = coding_store.json.dumps

    def observed(*args, **kwargs):
        serializations.append(True)
        return original(*args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(coding_store.json, "dumps", observed)
        with pytest.raises(ValueError, match="invalid coding journal"):
            coding_store.encode(value, maximum=maximum)
    assert serializations == []


@pytest.mark.parametrize("maximum", [65536, 524288])
def test_journal_preserves_exact_utf8_ceiling(maximum):
    import hashlib

    from agency.admission import coding_store

    value = {"雪": "a" * (maximum - 10)}
    text, digest = coding_store.encode(value, maximum=maximum)
    assert len(text.encode("utf-8")) == maximum
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert coding_store.decode(text, digest, maximum=maximum) == value
    with pytest.raises(ValueError):
        coding_store.encode({"雪": value["雪"] + "b"}, maximum=maximum)


def test_journal_retains_exact_ceiling_for_json_escapes(monkeypatch):
    from agency.admission import coding_store

    value = {"key": "\n" * 64}
    assert len(b"key") + len(value["key"].encode("utf-8")) < 128
    serializations = []
    original = coding_store.json.dumps

    def observed(*args, **kwargs):
        serializations.append(True)
        return original(*args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(coding_store.json, "dumps", observed)
        with pytest.raises(ValueError, match="invalid coding journal"):
            coding_store.encode(value, maximum=128)
    assert serializations == [True]
