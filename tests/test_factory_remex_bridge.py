"""Explicit coding commands over the owner API; synthetic transport only."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from agency.factory.coding_models import CodingBinding, CodingReceipt

spec = importlib.util.spec_from_file_location(
    "factory_remex_bridge", Path(__file__).parents[1] / "ops/remex_owner_trial/remex_bridge.py"
)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
pytestmark = pytest.mark.skip(reason="pending coding pilot wiring; preserved from resi/coding-rollout")


def update(uid=100, text="/code Set x to integer 2", owner=5981285472):
    return {"update_id": uid, "message": {"from": {"id": owner},
            "chat": {"id": owner, "type": "private"}, "text": text}}


def receipt_envelope():
    binding = CodingBinding(
        owner_id="fixture-owner", workspace_id="fixture-workspace", request_id="tg-remex-code-100",
        task_id="task-1", thread_id="thread-1", profile_id="literal-assignment-v1",
        profile_sha256="0" * 64, blueprint_id="fixture-coder", blueprint_version="1.0.0",
        runtime_agent_id="fixture-agent", base_commit="0" * 40,
        checker_sha256="0" * 64, criteria_sha256="0" * 64,
    )
    receipt = CodingReceipt(
        binding=binding, plan_sha256="0" * 64, forge_report_id="fixture-report",
        forge_report_sha256="0" * 64, artifact_sha256="0" * 64, patch_sha256="0" * 64,
        objective_status="passed", reason="matched",
    )
    return {"task_id": "task-1", "request_id": binding.request_id, "thread_id": "thread-1",
            "execution_state": "COMPLETED", "receipt": receipt.model_dump(mode="json"),
            "reason": "available"}


@pytest.fixture
def case(tmp_path):
    b = bridge.Bridge.__new__(bridge.Bridge)
    b.db = sqlite3.connect(tmp_path / "bridge.db")
    b.db.executescript("CREATE TABLE kv(k TEXT PRIMARY KEY,v TEXT NOT NULL);"
                      "CREATE TABLE turns(id INTEGER PRIMARY KEY,payload TEXT NOT NULL);")
    c = SimpleNamespace(b=b, calls=[], messages=[], envelope=receipt_envelope())

    class Sink:
        async def send_message(self, chat, text, **kwargs):
            c.messages.append((chat, text))

    async def api(method, path, payload=None):
        c.calls.append((method, path, payload))
        if path == "/api/pilot/threads":
            return {"thread": {"id": "thread-1", "title": "Fixture"}}
        if path.endswith("/coding-receipt"):
            return c.envelope
        if path == "/api/pilot/coding-tasks":
            # Exact request must be durable BEFORE first admission.
            saved = json.loads(b.db.execute("SELECT payload FROM turns WHERE id=100").fetchone()[0])
            assert saved == payload
        return {"task_id": "task-1", "state": "COMPLETED", "response": "Untrusted model text"}

    b.adapter = Sink()
    b.api = api
    yield c
    b.db.close()


@pytest.mark.asyncio
async def test_explicit_code_uses_native_endpoint_and_bound_saved_receipt(case):
    c = case
    await c.b.handle(update())
    expected = {"request_id": "tg-remex-code-100", "thread_id": "thread-1",
                "profile_id": "literal-assignment-v1", "task": "Set x to integer 2"}
    assert ("POST", "/api/pilot/coding-tasks", expected) in c.calls
    assert ("GET", "/api/pilot/tasks/task-1/coding-receipt", None) in c.calls
    assert c.b.get("task:100") == "task-1"
    text = c.messages[-1][1]
    assert "Objective check: passed" in text
    assert "Tests were not run" in text and "release is not authorized" in text
    assert "Untrusted model text" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["task_id", "request_id", "thread_id", "execution_state", "reason",
                                  "binding.task_id", "binding.request_id", "binding.thread_id"])
async def test_foreign_or_inconsistent_receipt_is_not_reported_as_passed(case, field):
    if field.startswith("binding."):
        case.envelope["receipt"]["binding"][field.split(".")[1]] = "foreign"
    else:
        case.envelope[field] = "foreign"
    assert await case.b.process_update(update()) is False
    assert case.b.get("task:100") == "task-1"
    assert case.b.get("offset") is None
    assert "temporarily unavailable" in case.messages[-1][1]
    assert all("Objective check: passed" not in text for _, text in case.messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["", "   ", "x" * 8001, "bad\x00text", "bad\ud800text"])
async def test_invalid_code_task_has_no_admission_or_thread_side_effects(case, body):
    await case.b.handle(update(text="/code " + body))
    assert case.calls == []
    assert case.b.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
    assert "valid task" in case.messages[-1][1]


@pytest.mark.asyncio
async def test_code_preserves_exact_task_text_after_command_separator(case):
    body = "  Set x to integer 2\n  "
    await case.b.handle(update(text="/code\n" + body))
    posted = next(payload for method, path, payload in case.calls if path == "/api/pilot/coding-tasks")
    assert posted["task"] == body


@pytest.mark.asyncio
async def test_disabled_coding_submission_names_the_exact_saved_request(case):
    original = case.b.api

    async def disabled(method, path, payload=None):
        if path == "/api/pilot/coding-tasks":
            raise RuntimeError("pilot:disabled")
        return await original(method, path, payload)

    case.b.api = disabled
    assert await case.b.process_update(update()) is True
    text = case.messages[-1][1]
    assert "Saved request: tg-remex-code-100" in text
    assert "No success is confirmed" in text
    saved = json.loads(case.b.db.execute("SELECT payload FROM turns WHERE id=100").fetchone()[0])
    assert saved["request_id"] == "tg-remex-code-100"


@pytest.mark.asyncio
async def test_coding_replay_uses_saved_kind_body_thread_and_get_only(case):
    await case.b.handle(update())
    before = case.b.db.execute("SELECT payload FROM turns WHERE id=100").fetchone()[0]
    case.b.put("thread", "other-thread")
    case.calls.clear()
    await case.b.handle(update(text="An ordinary edited delivery must not replace saved coding work"))
    assert case.calls == [("GET", "/api/pilot/tasks/task-1", None),
                          ("GET", "/api/pilot/tasks/task-1/coding-receipt", None)]
    assert case.b.db.execute("SELECT payload FROM turns WHERE id=100").fetchone()[0] == before


@pytest.mark.asyncio
async def test_ambiguous_admission_reuses_identical_saved_coding_request(case):
    original = case.b.api

    async def timeout(method, path, payload=None):
        if path == "/api/pilot/coding-tasks":
            raise TimeoutError("synthetic lost acknowledgement")
        return await original(method, path, payload)

    case.b.api = timeout
    with pytest.raises(TimeoutError):
        await case.b.process_update(update())
    saved = case.b.db.execute("SELECT payload FROM turns WHERE id=100").fetchone()[0]
    assert case.b.get("task:100") is None and case.b.get("offset") is None
    case.b.put("thread", "other-thread")
    case.b.api = original
    assert await case.b.process_update(update(text="/code changed text")) is True
    posted = next(value for method, path, value in case.calls if path == "/api/pilot/coding-tasks")
    assert posted == json.loads(saved)


@pytest.mark.asyncio
async def test_missing_receipt_retains_known_task_and_warns_once(case):
    original = case.b.api

    async def unavailable(method, path, payload=None):
        if path.endswith("/coding-receipt"):
            raise RuntimeError("pilot:store")
        return await original(method, path, payload)

    case.b.api = unavailable
    assert await case.b.process_update(update()) is False
    assert await case.b.process_update(update()) is False
    assert len(case.messages) == 1 and "Task accepted" in case.messages[0][1]
    assert case.b.get("offset") is None
    case.calls.clear()
    case.b.api = original
    assert await case.b.process_update(update()) is True
    assert all(method == "GET" for method, _, _ in case.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("objective_status", ["passed"]),
    ("objective_status", "constructor"), ("tests_run", 0), ("release_authorized", 0),
    ("release_authorized", True), ("binding", None)])
async def test_malformed_receipt_never_claims_passed(case, field, value):
    case.envelope["receipt"][field] = value
    assert await case.b.process_update(update()) is False
    assert all("Objective check: passed" not in text for _, text in case.messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["FAILED", "UNCERTAIN", "CANCELLED", "DENIED"])
async def test_noncompleted_coding_does_not_read_or_invent_a_receipt(case, state):
    original = case.b.api

    async def failed(method, path, payload=None):
        result = await original(method, path, payload)
        if path == "/api/pilot/coding-tasks":
            result["state"] = state
        return result

    case.b.api = failed
    await case.b.handle(update())
    assert not any(path.endswith("/coding-receipt") for _, path, _ in case.calls)
    assert state in case.messages[-1][1] and "not confirmed successful" in case.messages[-1][1]


@pytest.mark.asyncio
async def test_ordinary_markers_do_not_select_coding(case):
    await case.b.handle(update(text='profile_id=literal-assignment-v1 /code Set x to integer 2'))
    assert any(path == "/api/pilot/tasks" for _, path, _ in case.calls)
    assert not any(path == "/api/pilot/coding-tasks" for _, path, _ in case.calls)


@pytest.mark.asyncio
async def test_foreign_code_sender_is_ignored(case):
    await case.b.handle(update(owner=1234))
    assert case.calls == [] and case.messages == []


@pytest.mark.asyncio
async def test_full_8000_character_coding_task_is_allowed(case):
    await case.b.handle(update(text="/code " + "x" * 8000))
    assert next(value for _, path, value in case.calls if path == "/api/pilot/coding-tasks")["task"] == "x" * 8000


@pytest.mark.asyncio
@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("text", ["/status", "/new Changed", "/code", "/help", ""])
async def test_saved_coding_turn_precedes_all_current_text_dispatch(case, known, text):
    payload = {"request_id": "tg-remex-code-100", "thread_id": "thread-1",
               "profile_id": "literal-assignment-v1", "task": "Set x to integer 2"}
    encoded = json.dumps(payload)
    case.b.db.execute("INSERT INTO turns VALUES (?,?)", (100, encoded))
    case.b.db.commit()
    case.b.put("thread", "other-thread")
    if known:
        case.b.put("task:100", "task-1")
    assert await case.b.process_update(update(text=text)) is True
    expected = [("GET", "/api/pilot/tasks/task-1", None)] if known else [
        ("POST", "/api/pilot/coding-tasks", payload)]
    assert case.calls == expected + [("GET", "/api/pilot/tasks/task-1/coding-receipt", None)]
    assert case.b.get("thread") == "other-thread"
    assert case.b.db.execute("SELECT payload FROM turns WHERE id=100").fetchone()[0] == encoded
    assert "Objective check: passed" in case.messages[-1][1]
