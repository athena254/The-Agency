"""Controller-authored acceptance: corrupt receipts, isolation, cancellation and restart.

These are independent acceptance tests, not a claim that they originally drove
backend internals. All stores/providers/credentials are disposable test fixtures.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3

import httpx
import pytest
from pydantic import SecretStr

from agency.admission.auth import OwnerAuthenticator
from agency.admission.budget import OwnerBudgetAdapter
from agency.admission.models import OwnerTaskRequest
from agency.admission.service import OwnerPilotService
from agency.admission.store import OwnerPilotStore
from agency.butler.service import ButlerService
from agency.butler.threads import ThreadStore
from agency.kernel.audit import BetaAuditLog
from agency.prototype.app import create_app
from agency.telegram.budget_store import BetaBudgetStore
from tests.test_calculator_verification_api import wait_done
from tests.test_owner_pilot_api import api, concrete  # noqa: F401 -- fixture dependency


async def create_calculation(client, expression="2+3"):
    made = await client.post("/api/pilot/threads", json={"request_id": "thread", "title": "Security acceptance"})
    thread_id = made.json()["thread"]["id"]
    accepted = await client.post("/api/pilot/calculator-tasks", json={"request_id": "calculation", "thread_id": thread_id, "expression": expression})
    assert accepted.status_code == 202, accepted.text
    return thread_id, accepted.json()["task_id"]


@pytest.mark.parametrize("bad", [True, 2.0, None, ["2+3"], {"value": "2+3"}])
async def test_expression_requires_an_actual_string(api, bad):
    client, c, _ = api
    result = await client.post("/api/pilot/calculator-tasks", json={"request_id": "invalid", "thread_id": "unused", "expression": bad})
    assert result.status_code == 422, result.text
    assert c["requests"] == []


async def test_client_cannot_supply_expected_answer_or_passed_verdict(api):
    client, c, _ = api
    result = await client.post("/api/pilot/calculator-tasks", json={"request_id": "invalid", "thread_id": "unused", "expression": "2+3", "expected": "5", "status": "passed"})
    assert result.status_code == 422, result.text
    assert c["requests"] == []


@pytest.mark.parametrize("kind", ["result_digest", "response", "input_digest", "criteria", "verdict"])
async def test_corrupt_saved_verification_fails_closed(api, kind):
    client, c, _ = api
    c["replies"][1] = '{"final":"5"}'
    _, task_id = await create_calculation(client)
    assert (await wait_done(client, task_id))["state"] == "COMPLETED"
    assert (await client.get(f"/api/pilot/tasks/{task_id}/verification")).json()["receipt"]["status"] == "passed"
    with sqlite3.connect(c["store"]._db_path) as db:
        row = db.execute("SELECT result_json,result_digest,input_json,input_digest FROM pilot_requests WHERE task_id=?", (task_id,)).fetchone()
        result = json.loads(row[0])
        digest = row[1]
        input_digest = row[3]
        if kind == "result_digest":
            digest = "0" * 64
        elif kind == "input_digest":
            input_digest = "0" * 64
        else:
            if kind == "response":
                result["response"] = "6"
            elif kind == "criteria":
                result["verification"]["expression"] = "2+4"
                result["verification"]["criteria_sha256"] = hashlib.sha256(b"2+4").hexdigest()
            else:
                result["verification"]["status"] = "failed"
                result["verification"]["reason"] = "mismatch"
            digest = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        encoded = json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        db.execute("UPDATE pilot_requests SET result_json=?,result_digest=?,input_digest=? WHERE task_id=?", (encoded, digest, input_digest, task_id))
    response = await client.get(f"/api/pilot/tasks/{task_id}/verification")
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "store"
    assert "receipt" not in response.json()
    assert len(c["requests"]) == 2


async def test_pending_and_cancelled_tasks_never_publish_a_successful_receipt(api, monkeypatch):
    client, c, _ = api
    entered = asyncio.Event()

    async def held(request):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: c["client"](transport=httpx.MockTransport(held), **kw))
    _, task_id = await create_calculation(client)
    async with asyncio.timeout(5):
        await entered.wait()
        pending = await client.get(f"/api/pilot/tasks/{task_id}/verification")
        assert pending.status_code == 200, pending.text
        assert pending.json()["receipt"] is None
        assert pending.json()["reason"] == "not_completed"
        cancel = await client.post(f"/api/pilot/tasks/{task_id}/cancel", json={"request_id": "cancel"})
    assert cancel.status_code == 202, cancel.text
    assert cancel.json()["task"]["state"] == "UNCERTAIN"
    current = await client.get(f"/api/pilot/tasks/{task_id}/verification")
    assert current.json()["receipt"] is None and current.json()["reason"] == "not_completed"
    assert current.json()["execution_state"] == "UNCERTAIN"


@pytest.mark.parametrize("answer", ["5", "4", "five"])
async def test_saved_verdict_survives_service_reopen_without_another_attempt(api, answer):
    client, c, _ = api
    c["replies"][1] = json.dumps({"final": answer})
    thread_id, task_id = await create_calculation(client)
    before = await wait_done(client, task_id)
    saved = (await client.get(f"/api/pilot/tasks/{task_id}/verification")).json()
    row = await c["store"].get(c["owner"], task_id)
    admitted_message = json.loads(row.input_json)["message"]
    await c["service"].stop()
    reopened_store = OwnerPilotStore(str(c["tmp_path"] / "owner.db"))
    reopened_threads = ThreadStore(str(c["tmp_path"] / "owner.db"))
    reopened_budget = OwnerBudgetAdapter(BetaBudgetStore(str(c["tmp_path"] / "quota.db")), reopened_store)
    reopened_audit = BetaAuditLog(c["tmp_path"] / "audit.db")
    reopened_butler = ButlerService(orchestrator=c["orch"], thread_store=reopened_threads)
    service = OwnerPilotService(butler=reopened_butler, store=reopened_store, budget=reopened_budget,
        audit=reopened_audit, provider_config=c["config"], owner_id="owner")
    try:
        await service.start()
        app = create_app(reopened_butler, pilot=service, authenticator=OwnerAuthenticator(
            owner_id="owner", subject_id="subject", bearer_secret=SecretStr("SYNTHETIC_OWNER_TOKEN_32_CHARACTERS")))
        async with c["client"](transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as other:
            other.headers.update({"Authorization": "Bearer SYNTHETIC_OWNER_TOKEN_32_CHARACTERS"})
            result = await other.get(f"/api/pilot/tasks/{task_id}/verification")
            assert result.status_code == 200, result.text
            assert result.json() == saved
            # A different valid owner bearer cannot view the first owner's rows.
            app.state.owner_authenticator = OwnerAuthenticator(owner_id="other-owner", subject_id="other-subject", bearer_secret=SecretStr("SYNTHETIC_OWNER_TOKEN_32_CHARACTERS"))
            denied = await other.get(f"/api/pilot/tasks/{task_id}/verification")
            assert denied.status_code in {401, 403, 404}, denied.text
        replay = await service.submit(c["owner"], OwnerTaskRequest(request_id="calculation", thread_id=thread_id, message=admitted_message))
        assert replay.replayed is True
        assert replay.usage.model_dump() == before["usage"]
        assert len(c["requests"]) == 2
    finally:
        await service.stop()
        reopened_threads.close()
