"""New calculator contract over the real owner service/tool loop; HTTP provider synthetic."""
from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from tests.test_owner_pilot_api import api, concrete  # noqa: F401 -- concrete dependency fixture


async def wait_done(client, task_id):
    async with asyncio.timeout(10):
        while True:
            response = await client.get(f"/api/pilot/tasks/{task_id}")
            assert response.status_code == 200, response.text
            view = response.json()
            if view["state"] not in {"ADMITTED", "RUNNING"}:
                return view
            await asyncio.sleep(0.005)


@pytest.mark.parametrize(
    ("answer", "status", "reason", "observed"),
    [("5", "passed", "matches", "5"), ("4", "failed", "mismatch", "4"),
     ("The answer is five.", "unable_to_verify", "answer_not_integer", None)],
)
async def test_calculator_verdict_is_saved_separately_and_replay_does_not_dispatch(api, answer, status, reason, observed):
    client, c, _ = api
    c["replies"][1] = json.dumps({"final": answer})
    created = await client.post("/api/pilot/threads", json={"request_id": "thread", "title": "Checked calculation"})
    thread = created.json()["thread"]["id"]
    payload = {"request_id": "calculation", "thread_id": thread, "expression": "2+3"}
    admitted = await client.post("/api/pilot/calculator-tasks", json=payload)
    assert admitted.status_code == 202, admitted.text
    task_id = admitted.json()["task_id"]
    result = await wait_done(client, task_id)
    assert result["state"] == "COMPLETED", result
    assert result["assessment"]["independently_verified"] is False
    assert result["usage"] == {"model_attempts": 2, "tool_attempts": 1}
    check = await client.get(f"/api/pilot/tasks/{task_id}/verification")
    assert check.status_code == 200, check.text
    snapshot = check.json()
    assert set(snapshot) == {"task_id", "request_id", "thread_id", "execution_state", "receipt", "reason"}
    assert snapshot["reason"] == "available"
    receipt = snapshot["receipt"]
    assert receipt == {
        "schema_version": "calculator.v1", "method": "bounded_integer_arithmetic/v1",
        "task_id": task_id, "request_id": "calculation", "thread_id": thread,
        "expression": "2+3", "criteria_sha256": hashlib.sha256(b"2+3").hexdigest(),
        "output_sha256": hashlib.sha256(answer.encode()).hexdigest(),
        "status": status, "reason": reason, "expected": "5", "observed": observed,
    }
    stored = await c["store"].get(c["owner"], task_id)
    assert json.loads(stored.result_json)["verification"] == receipt
    repeated = await client.post("/api/pilot/calculator-tasks", json=payload)
    assert repeated.status_code == 200 and repeated.json()["replayed"] is True
    assert repeated.json()["usage"] == result["usage"]
    assert (await client.get(f"/api/pilot/tasks/{task_id}/verification")).json() == snapshot
    assert len(c["requests"]) == 2
    conflict = await client.post("/api/pilot/calculator-tasks", json={**payload, "expression": "2+4"})
    assert conflict.status_code == 409
    assert len(c["requests"]) == 2


@pytest.mark.parametrize("expression", ["2/3", "1.5", "1e3", "2**3", "True", "__import__('os')", "01+2", "\u0661+2", "2\n+3", "("*13+"1"+")"*13, "9"*121, "1000000000000000+1"])
async def test_unsupported_calculation_is_rejected_without_model_dispatch(api, expression):
    client, c, _ = api
    created = await client.post("/api/pilot/threads", json={"request_id": "thread", "title": "Rejected"})
    response = await client.post("/api/pilot/calculator-tasks", json={"request_id": "invalid", "thread_id": created.json()["thread"]["id"], "expression": expression})
    assert response.status_code == 422, response.text
    assert c["requests"] == []
    assert (await client.get("/api/pilot/tasks")).json() == {"tasks": []}


async def test_ordinary_task_stays_unverified_and_retains_original_result_shape(api):
    client, c, _ = api
    created = await client.post("/api/pilot/threads", json={"request_id": "thread", "title": "Ordinary"})
    admitted = await client.post("/api/pilot/tasks", json={"request_id": "ordinary", "thread_id": created.json()["thread"]["id"], "message": "calculate 2+3"})
    result = await wait_done(client, admitted.json()["task_id"])
    check = await client.get(f"/api/pilot/tasks/{result['task_id']}/verification")
    assert check.status_code == 200, check.text
    assert check.json()["reason"] == "not_requested" and check.json()["receipt"] is None
    stored = await c["store"].get(c["owner"], result["task_id"])
    assert set(json.loads(stored.result_json)) == {"task_id", "response", "assessment"}


async def test_calculator_submission_and_receipt_need_owner_auth(api):
    client, c, _ = api
    client.headers.pop("Authorization")
    response = await client.post("/api/pilot/calculator-tasks", json={"request_id": "bad", "thread_id": "x", "expression": "2+3"})
    assert response.status_code == 401, response.text
    response = await client.get("/api/pilot/tasks/missing/verification")
    assert response.status_code == 401, response.text
    assert c["requests"] == []
