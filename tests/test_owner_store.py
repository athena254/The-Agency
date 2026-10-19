"""Real scratch-only SQLite owner admission and publication tests."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from dataclasses import fields

import pytest

from agency.admission.models import OwnerPrincipal, PilotError

OWNER = OwnerPrincipal("owner", "local", "local")
FOREIGN = OwnerPrincipal("foreign", "local", "foreign")


@pytest.fixture
async def store(tmp_path):
    from agency.admission.store import OwnerPilotStore

    instance = OwnerPilotStore(str(tmp_path / "pilot.db"))
    try:
        await instance.initialize()
        yield instance
    finally:
        await instance.close()


async def test_lifetime_lock_and_atomic_thread_replay(tmp_path):
    from agency.admission.store import OwnerPilotStore

    path = str(tmp_path / "pilot.db")
    store, second = OwnerPilotStore(path), OwnerPilotStore(path)
    try:
        await store.initialize()
        with pytest.raises(PilotError) as exc:
            await second.initialize()
        assert exc.value.code == "store"
        workspace = await store.ensure_workspace(OWNER)
        assert await store.ensure_workspace(OWNER) == workspace
        rows = await asyncio.gather(
            *(store.create_thread(OWNER, "create-1", "Exact title ") for _ in range(8))
        )
        assert len({thread.id for _, thread in rows}) == 1
        assert sum(not receipt.replayed for receipt, _ in rows) == 1
        receipt, thread = rows[0]
        assert receipt.resource_id == thread.id and thread.title == "Exact title "
        assert "+00:00" in thread.created_at
        assert len(await store.list_threads(OWNER)) == 1
        assert await store.list_messages(OWNER, thread.id) == []
        with pytest.raises(PilotError) as exc:
            await store.create_thread(OWNER, "create-1", "Changed")
        assert exc.value.status_code == 409
        with pytest.raises(PilotError) as exc:
            await store.list_messages(FOREIGN, thread.id)
        assert exc.value.status_code == 404
        with pytest.raises(PilotError):
            await store.create_thread(FOREIGN, "branch", "Title", thread.id)
        with closing(sqlite3.connect(path)) as db, db:
            assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
        await store.close()
        await second.initialize()
        assert (await second.list_threads(OWNER))[0].id == thread.id
        assert await second.ensure_workspace(OWNER) == workspace
    finally:
        await store.close()
        await second.close()


async def test_admission_is_durable_owner_bound_and_exposes_frozen_columns(store):
    from agency.admission.models import AdmissionRecord

    _, thread = await store.create_thread(OWNER, "new", "Title")
    payload = {"thread_id": thread.id, "message": " \ufeffhello\r\n "}
    rows = await asyncio.gather(
        *(store.admit(OWNER, "turn-1", operation="turn", payload=payload) for _ in range(10))
    )
    assert len({r.task_id for r in rows}) == 1
    assert sum(not r.replayed for r in rows) == 1
    row = rows[0]
    assert {f.name for f in fields(AdmissionRecord)} == {
        "sequence",
        "owner_id",
        "request_id",
        "operation",
        "input_digest",
        "input_json",
        "task_id",
        "thread_id",
        "resource_id",
        "state",
        "phase",
        "cancel_requested",
        "model_attempts",
        "tool_attempts",
        "result_json",
        "result_digest",
        "error_code",
        "provider_config_id",
        "user_message_id",
        "assistant_message_id",
        "created_at",
        "updated_at",
        "replayed",
    }
    assert row.state == "ADMITTED" and row.operation == "turn"
    values = {
        field.name: getattr(row, field.name)
        for field in fields(AdmissionRecord)
        if field.name != "replayed"
    }
    assert AdmissionRecord(**values).replayed is False
    assert row.task_id and row.user_message_id and row.assistant_message_id
    assert len({row.task_id, row.user_message_id, row.assistant_message_id}) == 3
    assert row.input_digest and row.input_json
    assert (await store.get(OWNER, row.task_id)).sequence == row.sequence
    assert (await store.get_request(OWNER, "turn-1")).task_id == row.task_id
    assert len(await store.list_tasks(OWNER)) == 1
    assert await store.list_tasks(FOREIGN) == []
    for denied in (
        lambda: store.get(FOREIGN, row.task_id),
        lambda: store.admit(FOREIGN, "foreign", operation="turn", payload=payload),
    ):
        with pytest.raises(PilotError) as exc:
            await denied()
        assert exc.value.status_code == 404
    for changed in (payload | {"message": "changed"}, payload | {"owner_id": "evil"}):
        with pytest.raises(PilotError):
            await store.admit(OWNER, "turn-1", operation="turn", payload=changed)
    with pytest.raises(PilotError) as exc:
        await store.admit(OWNER, "new", operation="turn", payload=payload)
    assert exc.value.status_code == 409
    for limit in (True, 0, -1, 101):
        with pytest.raises(PilotError):
            await store.list_tasks(OWNER, limit=limit)


def assessment_for(task_id, text):
    from agency.agents.assessment import build_assessment, unchecked_subtask

    return build_assessment(
        task_id,
        execution_status="completed",
        output=text,
        subtasks=(
            unchecked_subtask("tool_loop", path="tool", execution_status="completed", output=text),
        ),
    )


async def new_turn(store, key="turn", message="user\r\n"):
    _, thread = await store.create_thread(OWNER, "thread-" + key, "Title")
    return await store.admit(
        OWNER, key, operation="turn", payload={"thread_id": thread.id, "message": message}
    )


async def test_staged_task_and_exact_bytes_commit_as_one_atomic_turn(store):
    row = await new_turn(store)
    output = "\ufeffanswer\r\n☃"
    assessment = assessment_for(row.task_id, output)
    result = {
        "task_id": row.task_id,
        "response": output,
        "assessment": assessment.model_dump(mode="json"),
    }
    with pytest.raises(PilotError):
        await store.transition(
            row.sequence, expected="ADMITTED", state="COMPLETED", phase="committed"
        )
    await store.transition(row.sequence, expected="ADMITTED", state="RUNNING", phase="running")
    await store.stage_result(row.sequence, result)
    staged = await store.get(OWNER, row.task_id)
    assert staged.state == "RUNNING" and staged.result_digest
    assert await store.list_messages(OWNER, row.thread_id) == []
    with pytest.raises(PilotError):
        await store.commit_turn(row.sequence, text=output + "changed", assessment=assessment)
    wrong = assessment_for("wrong-task", output)
    with pytest.raises(PilotError):
        await store.commit_turn(row.sequence, text=output, assessment=wrong)
    await store.commit_turn(row.sequence, text=output, assessment=assessment)
    final = await store.get(OWNER, row.task_id)
    assert final.state == "COMPLETED" and json.loads(final.result_json) == result
    messages = await store.list_messages(OWNER, row.thread_id)
    assert [(m.id, m.role, m.content) for m in messages] == [
        (row.user_message_id, "user", "user\r\n"),
        (row.assistant_message_id, "assistant", output),
    ]
    for denied in (
        lambda: store.commit_turn(row.sequence, text=output, assessment=assessment),
        lambda: store.stage_result(row.sequence, result),
        lambda: store.transition(
            row.sequence, expected="COMPLETED", state="FAILED", phase="failed"
        ),
    ):
        with pytest.raises(PilotError):
            await denied()
    assert len(await store.list_messages(OWNER, row.thread_id)) == 2


async def test_publication_rolls_back_result_and_both_messages_on_sql_failure(store):
    row = await new_turn(store)
    await store.transition(row.sequence, expected="ADMITTED", state="RUNNING", phase="running")
    result = {"task_id": row.task_id, "response": "answer", "assessment": None}
    await store.stage_result(row.sequence, result)
    with closing(sqlite3.connect(store._db_path)) as db, db:
        db.execute(
            "CREATE TRIGGER reject_assistant BEFORE INSERT ON thread_messages WHEN NEW.role='assistant' BEGIN SELECT RAISE(ABORT, 'synthetic private failure'); END"
        )
    with pytest.raises(PilotError) as exc:
        await store.commit_turn(row.sequence, text="answer", assessment=None)
    assert "synthetic private failure" not in str(exc.value)
    assert (await store.get(OWNER, row.task_id)).state == "RUNNING"
    assert await store.list_messages(OWNER, row.thread_id) == []


async def test_cancel_is_durable_idempotent_and_blocks_publication(store):
    admitted = await new_turn(store, "not-started")
    cancelled = await store.request_cancel(OWNER, admitted.task_id, "cancel-1")
    assert cancelled.state == "CANCELLED" and cancelled.cancel_requested and not cancelled.replayed
    replay = await store.request_cancel(OWNER, admitted.task_id, "cancel-1")
    assert replay.replayed and replay.task_id == admitted.task_id
    command = await store.get_request(OWNER, "cancel-1")
    assert (
        command.operation == "cancel"
        and command.task_id is None
        and command.resource_id == admitted.task_id
    )
    running = await new_turn(store, "running")
    await store.transition(running.sequence, expected="ADMITTED", state="RUNNING", phase="running")
    await store.stage_result(
        running.sequence,
        {"task_id": running.task_id, "response": "private staged", "assessment": None},
    )
    with pytest.raises(PilotError) as exc:
        await store.request_cancel(FOREIGN, running.task_id, "foreign")
    assert exc.value.status_code == 404
    with pytest.raises(PilotError) as exc:
        await store.request_cancel(OWNER, running.task_id, "cancel-1")
    assert exc.value.status_code == 409
    flagged = await store.request_cancel(OWNER, running.task_id, "cancel-2")
    assert flagged.state == "RUNNING" and flagged.cancel_requested
    with pytest.raises(PilotError):
        await store.commit_turn(running.sequence, text="private staged", assessment=None)
    assert not await store.reserve_attempt(running.sequence, kind="model", limit=8)
    recovered = await store.recover_interrupted()
    assert [r.task_id for r in recovered] == [running.task_id]
    assert recovered[0].state == "UNCERTAIN" and recovered[0].result_json
    assert await store.list_messages(OWNER, running.thread_id) == []
    assert await store.recover_interrupted() == []


async def test_attempt_caps_are_atomic_and_restart_never_resumes(tmp_path):
    from agency.admission.store import OwnerPilotStore

    path = str(tmp_path / "restart.db")
    first = OwnerPilotStore(path)
    try:
        await first.initialize()
        row = await new_turn(first)
        assert not await first.reserve_attempt(row.sequence, kind="model", limit=8)
        await first.transition(row.sequence, expected="ADMITTED", state="RUNNING", phase="running")
        assert (
            sum(
                await asyncio.gather(
                    *(first.reserve_attempt(row.sequence, kind="model", limit=8) for _ in range(12))
                )
            )
            == 8
        )
        assert (
            sum(
                await asyncio.gather(
                    *(first.reserve_attempt(row.sequence, kind="tool", limit=4) for _ in range(7))
                )
            )
            == 4
        )
        for kind, limit in (("model", 9), ("tool", 5), ("model", True), ("invalid", 1)):
            with pytest.raises(PilotError):
                await first.reserve_attempt(row.sequence, kind=kind, limit=limit)
    finally:
        await first.close()
    second = OwnerPilotStore(path)
    try:
        await second.initialize()
        recovered = await second.recover_interrupted()
        assert len(recovered) == 1 and recovered[0].state == "UNCERTAIN"
        assert recovered[0].model_attempts == 8 and recovered[0].tool_attempts == 4
        replay = await second.admit(
            OWNER,
            "turn",
            operation="turn",
            payload={"thread_id": row.thread_id, "message": "user\r\n"},
        )
        assert replay.replayed and replay.task_id == row.task_id and replay.state == "UNCERTAIN"
        with pytest.raises(PilotError):
            await second.transition(
                row.sequence, expected="UNCERTAIN", state="RUNNING", phase="running"
            )
    finally:
        await second.close()


async def test_thread_owner_checked_on_every_task_read(store):
    row = await new_turn(store)
    with closing(sqlite3.connect(store._db_path)) as db, db:
        db.execute("UPDATE threads SET owner='foreign' WHERE id=?", (row.thread_id,))
    for read in (
        lambda: store.get(OWNER, row.task_id),
        lambda: store.get_request(OWNER, row.request_id),
    ):
        with pytest.raises(PilotError) as exc:
            await read()
        assert exc.value.status_code == 404
    assert await store.list_tasks(OWNER) == []


@pytest.mark.parametrize("pragma", ["journal_mode", "synchronous"])
async def test_initialization_verifies_real_sqlite_pragmas_and_releases_lock(
    tmp_path, monkeypatch, pragma
):
    from agency.admission.store import OwnerPilotStore

    original = sqlite3.connect
    connections = []

    class RefusingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            normalized = sql.replace(" ", "").upper()
            if pragma == "journal_mode" and normalized == "PRAGMAJOURNAL_MODE=WAL":
                sql = "PRAGMA journal_mode=DELETE"
            elif pragma == "synchronous" and normalized == "PRAGMASYNCHRONOUS=FULL":
                sql = "PRAGMA synchronous=OFF"
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs, factory=RefusingConnection)
        connections.append(conn)
        return conn

    path = str(tmp_path / "pragmas.db")
    store = OwnerPilotStore(path)
    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        with pytest.raises(PilotError) as exc:
            await store.initialize()
        assert exc.value.code == "store"
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
    finally:
        await store.close()
    monkeypatch.setattr(sqlite3, "connect", original)
    replacement = OwnerPilotStore(path)
    try:
        await replacement.initialize()
    finally:
        await replacement.close()


async def test_cancelled_sqlite_worker_is_drained_before_lock_release(store, monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = store._workspace

    def gated(conn, principal):
        entered.set()
        assert release.wait(10)
        value = original(conn, principal)
        finished.set()
        return value

    monkeypatch.setattr(store, "_workspace", gated)
    operation = asyncio.create_task(store.ensure_workspace(OWNER))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        operation.cancel()
        closing = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        await closing
        assert finished.is_set()
        with pytest.raises(PilotError):
            await store.ensure_workspace(OWNER)
    finally:
        release.set()
        await asyncio.gather(operation, *([closing] if closing else []), return_exceptions=True)


async def test_killed_process_releases_lifetime_lock_and_recovers_private_staging(tmp_path):
    from agency.admission.store import OwnerPilotStore

    path, marker = tmp_path / "killed.db", tmp_path / "ready.json"
    code = """
import asyncio,json,os,sys
from pathlib import Path
from agency.admission.store import OwnerPilotStore
from agency.admission.models import OwnerPrincipal
async def main():
    store=OwnerPilotStore(sys.argv[1])
    await store.initialize()
    owner=OwnerPrincipal("owner","local","local")
    _,thread=await store.create_thread(owner,"thread","Title")
    row=await store.admit(owner,"turn",operation="turn",payload={"thread_id":thread.id,"message":"user"})
    await store.transition(row.sequence,expected="ADMITTED",state="RUNNING",phase="running")
    await store.stage_result(row.sequence,{"task_id":row.task_id,"response":"private staged","assessment":None})
    Path(sys.argv[2]).write_text(json.dumps({"pid":os.getpid(),"task_id":row.task_id,"thread_id":thread.id}))
    await asyncio.to_thread(sys.stdin.read)
    await store.close()
asyncio.run(main())
"""
    child = await asyncio.to_thread(
        subprocess.Popen,
        [sys.executable, "-c", code, str(path), str(marker)],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    contender = OwnerPilotStore(str(path))
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert marker.exists(), "child did not reach durable staging"
        with pytest.raises(PilotError):
            await contender.initialize()
        info = json.loads(marker.read_text())
        with closing(sqlite3.connect(path)) as db, db:
            assert (
                db.execute(
                    "SELECT state FROM pilot_requests WHERE task_id=?", (info["task_id"],)
                ).fetchone()[0]
                == "RUNNING"
            )
        # Kill the actual worker, not the Windows venv launcher parent.
        os.kill(info["pid"], signal.SIGTERM)
        await asyncio.to_thread(child.wait, 10)
        await contender.initialize()
        recovered = await contender.recover_interrupted()
        assert len(recovered) == 1 and recovered[0].state == "UNCERTAIN"
        assert recovered[0].result_json and recovered[0].task_id == info["task_id"]
        assert await contender.list_messages(OWNER, info["thread_id"]) == []
    finally:
        if child.poll() is None:
            if sys.platform == "win32":
                await asyncio.to_thread(
                    subprocess.run,
                    ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                )
            else:
                child.kill()
        await asyncio.to_thread(child.communicate, timeout=10)
        assert child.poll() is not None
        await contender.close()


@pytest.mark.parametrize(
    "text",
    ["x" * 65536, "\n" * 65535 + "x", "😀" * 16384],
    ids=["ascii", "escaped-newlines", "unicode"],
)
async def test_full_output_byte_limit_includes_json_escaping_without_truncation(store, text):
    row = await new_turn(store)
    await store.transition(row.sequence, expected="ADMITTED", state="RUNNING", phase="running")
    await store.stage_result(
        row.sequence, {"task_id": row.task_id, "response": text, "assessment": None}
    )
    await store.commit_turn(row.sequence, text=text, assessment=None)
    assert (await store.list_messages(OWNER, row.thread_id))[1].content == text


async def test_oversized_output_and_private_authority_fields_are_denied(store):
    row = await new_turn(store)
    await store.transition(row.sequence, expected="ADMITTED", state="RUNNING", phase="running")
    for result in (
        {"task_id": row.task_id, "response": "😀" * 16385, "assessment": None},
        {
            "task_id": row.task_id,
            "response": "answer",
            "assessment": None,
            "capability": "not-a-grant",
        },
        {"task_id": "wrong-task", "response": "answer", "assessment": None},
    ):
        with pytest.raises(PilotError):
            await store.stage_result(row.sequence, result)
    assert (await store.get(OWNER, row.task_id)).result_json is None
    assert await store.list_messages(OWNER, row.thread_id) == []
