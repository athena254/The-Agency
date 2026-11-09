"""Authenticated ASGI transport to concrete owner/native Forge; synthetic model only."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from agency.admission.auth import OwnerAuthenticator
from agency.prototype.app import create_app
from tests.test_factory_coding_service import coding_case as coding_case_fixture
from tests.test_factory_coding_service import make_service, payload

coding_case = coding_case_fixture
pytestmark = [pytest.mark.asyncio, pytest.mark.skip(reason="pending coding-receipt route wiring; preserved from resi/coding-rollout")]
TOKEN = "SYNTHETIC_OWNER_TOKEN_32_CHARACTERS"


def application(c, service):
    return create_app(service._butler, pilot=service, authenticator=OwnerAuthenticator(
        owner_id=c.owner.owner_id, subject_id=c.owner.subject_id, bearer_secret=SecretStr(TOKEN),
    ))


def client_for(c, app, **kwargs):
    return c.http_client(transport=httpx.ASGITransport(app=app, **kwargs),
                         base_url="http://127.0.0.1", headers={
        "Authorization": "Bearer " + TOKEN, "X-Prototype-CSRF": app.state.csrf_token,
        "Origin": "http://127.0.0.1",
    })


async def completed(client, task_id):
    async with asyncio.timeout(15):
        while True:
            response = await client.get(f"/api/pilot/tasks/{task_id}")
            assert response.status_code == 200, response.text
            if response.json()["state"] not in {"ADMITTED", "RUNNING"}:
                return response.json()
            await asyncio.sleep(0.005)


async def test_authenticated_post_native_forge_and_disabled_reopen_replay(coding_case, monkeypatch):
    import agency.admission.service as module
    import agency.agents.factory_coding as native

    c = coding_case
    invocations = []
    planner, forge = native.plan_edits, native.AgencyCodingAgent.run_task
    def planning(*a, **kw):
        invocations.append("planner")
        return planner(*a, **kw)
    def writing(*a, **kw):
        invocations.append("forge")
        return forge(*a, **kw)
    monkeypatch.setattr(native, "plan_edits", planning)
    monkeypatch.setattr(native.AgencyCodingAgent, "run_task", writing)
    c.service = make_service(c)
    app = application(c, c.service)
    body = payload(c).model_dump(mode="json")
    async with app.router.lifespan_context(app), client_for(c, app) as client:
        accepted = await client.post("/api/pilot/coding-tasks", json=body)
        assert accepted.status_code == 202, accepted.text
        task_id = accepted.json()["task_id"]
        view = await completed(client, task_id)
        assert view["state"] == "COMPLETED", view
        assert view["usage"] == {"model_attempts": 1, "tool_attempts": 0}
        saved = await c.service._store.get_coding_record(c.owner, task_id, c.workspace)
        assert saved.evidence_phase == "published"
        artifact = Path(saved.evidence.forge_report.worktree_path, "m.py").read_bytes()
        assert artifact == b"x = 2"
        assert (c.repo / "m.py").read_bytes() == b"x = 1\n"
        assert invocations == ["planner", "forge"]
        assert len(c.calls) == 1 and "tools" not in c.calls[0]
        messages = await c.service._store.list_messages(c.owner, c.thread.id)
        assert len(messages) == 2 and messages[0].content == body["task"]
        print("ASGI_NATIVE_PROOF " + json.dumps({"task_id": task_id,
            "receipt": saved.evidence.receipt.model_dump(mode="json"),
            "report": saved.evidence.forge_report.model_dump(mode="json"),
            "artifact_hex": artifact.hex(), "original_hex": (c.repo / "m.py").read_bytes().hex(),
            "owner_db": str(c.root / "owner.sqlite"), "quota_db": str(c.root / "quota.sqlite"),
            "audit_db": str(c.root / "audit.sqlite")}))
    c.service = make_service(c, coding=False)
    app = application(c, c.service)
    async with app.router.lifespan_context(app), client_for(c, app) as client:
        def forbidden(*a, **kw):
            raise AssertionError("historical replay must not allocate/reserve/provision/native/check")
        monkeypatch.setattr(module, "uuid4", forbidden)
        monkeypatch.setattr(c.service._store, "ensure_workspace", forbidden)
        monkeypatch.setattr(c.service._budget, "reserve_request", forbidden)
        monkeypatch.setattr(c.executor, "execute", forbidden)
        monkeypatch.setattr(native, "plan_edits", forbidden)
        replay = await client.post("/api/pilot/coding-tasks", json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json() == {**view, "replayed": True}
        assert len(c.calls) == 1 and invocations == ["planner", "forge"]


async def test_receipt_is_exact_saved_envelope_after_disabled_reopen(coding_case, monkeypatch):
    import agency.factory.coding_verification as checker
    c = coding_case
    c.service = make_service(c)
    await c.service.start()
    admitted = await c.service.submit_coding(c.owner, payload(c))
    await asyncio.shield(c.service._execution)
    expected = await c.service.get_coding_receipt(c.owner, admitted.task_id)
    await c.service.stop()
    c.service = make_service(c, coding=False)
    app = application(c, c.service)
    async with app.router.lifespan_context(app), client_for(c, app) as client:
        def forbidden(*a, **kw):
            raise AssertionError("saved receipt must not touch checker/native/provision")
        monkeypatch.setattr(checker, "verify_literal_artifact", forbidden)
        monkeypatch.setattr(checker, "checker_source_sha256", forbidden)
        monkeypatch.setattr(c.service._store, "ensure_workspace", forbidden)
        response = await client.get(f"/api/pilot/tasks/{admitted.task_id}/coding-receipt")
        assert response.status_code == 200, response.text
        assert response.json() == expected
        assert response.json()["receipt"]["tests_run"] is False
        assert response.json()["receipt"]["release_authorized"] is False
        assert len(c.calls) == 1


@pytest.fixture
async def coding_api(coding_case):
    c = coding_case
    c.service = make_service(c)
    app = application(c, c.service)
    async with app.router.lifespan_context(app), client_for(c, app) as client:
        yield client, c, app


@pytest.mark.parametrize("case,status,code", [
    ("missing_auth", 401, "unauthorized"), ("wrong_auth", 401, "unauthorized"),
    ("duplicate_auth", 401, "unauthorized"), ("host", 403, "forbidden"),
    ("origin", 403, "forbidden"), ("peer", 403, "forbidden"),
    ("missing_csrf", 403, "forbidden"), ("wrong_csrf", 403, "forbidden"),
    ("media", 415, "media"), ("encoding", 415, "media"),
    ("framing", 422, "invalid_request"), ("length", 422, "invalid_request"),
    ("oversize", 413, "too_large"), ("stream_oversize", 413, "too_large"),
    ("utf8", 422, "invalid_request"), ("json", 422, "invalid_request"),
    ("constant", 422, "invalid_request"), ("array", 422, "invalid_request"),
    ("unknown", 422, "invalid_request"), ("case", 422, "invalid_request"),
    ("type", 422, "invalid_request"), ("profile", 422, "invalid_request"),
])
async def test_guard_and_strict_dto_refusals_have_no_admission(coding_api, case, status, code, monkeypatch):
    client, c, app = coding_api
    body = payload(c).model_dump()
    headers = dict(client.headers)
    headers["content-type"] = "application/json"
    content = json.dumps(body).encode()
    if case == "missing_auth":
        headers.pop("authorization")
    elif case == "wrong_auth":
        headers["authorization"] = "Bearer wrong"
    elif case == "host":
        headers["host"] = "foreign.invalid"
    elif case == "origin":
        headers["origin"] = "http://127.0.0.1:9999"
    elif case == "missing_csrf":
        headers.pop("x-prototype-csrf")
    elif case == "wrong_csrf":
        headers["x-prototype-csrf"] = "wrong"
    elif case == "media":
        headers["content-type"] = "text/plain"
    elif case == "encoding":
        headers["content-encoding"] = "gzip"
    elif case == "framing":
        headers["transfer-encoding"] = "chunked"
        headers["content-length"] = str(len(content))
    elif case == "length":
        headers["content-length"] = "1"
    elif case == "oversize":
        headers["content-length"] = "32769"
    elif case == "stream_oversize":
        content = b" " * 32769
    elif case == "utf8":
        content = b'{"task":"\xff"}'
    elif case == "json":
        content = b"{"
    elif case == "constant":
        content = b'{"task":NaN}'
    elif case == "array":
        content = b"[]"
    elif case == "unknown":
        content = json.dumps({**body, "workspace_id": c.workspace}).encode()
    elif case == "case":
        body["Task"] = body.pop("task")
        content = json.dumps(body).encode()
    elif case == "type":
        content = json.dumps({**body, "task": 2}).encode()
    elif case == "profile":
        content = json.dumps({**body, "profile_id": "other"}).encode()
    header_list = list(headers.items())
    if case == "duplicate_auth":
        header_list.append(("authorization", "Bearer " + TOKEN))
    auth_denied = case in {"missing_auth", "wrong_auth", "duplicate_auth", "host", "origin", "peer", "missing_csrf", "wrong_csrf"}
    async def stream():
        if auth_denied:
            raise AssertionError("unauthorized request body was read")
        yield content
    def forbidden(*a, **kw):
        raise AssertionError("refused request reached service admission")
    monkeypatch.setattr(c.service, "submit_coding", forbidden)
    async with client_for(c, app, client=("192.0.2.1", 1234) if case == "peer" else ("127.0.0.1", 1234)) as raw:
        raw.headers.clear()
        response = await raw.send(raw.build_request("POST", "/api/pilot/coding-tasks", content=stream(), headers=header_list))
    assert response.status_code == status, response.text
    if case in {"host", "peer"}:
        assert response.json() == {"error": "This prototype accepts loopback requests only."}
    else:
        assert response.json()["code"] == code
    assert await c.service._store.list_tasks(c.owner) == [] and c.calls == []


async def test_default_disabled_and_store_error_precedence(coding_case, monkeypatch):
    c = coding_case
    c.service = make_service(c, coding=False)
    app = application(c, c.service)
    async with app.router.lifespan_context(app), client_for(c, app) as client:
        def forbidden(*a, **kw):
            raise AssertionError("disabled request must not provision/reserve")
        monkeypatch.setattr(c.service._store, "ensure_workspace", forbidden)
        monkeypatch.setattr(c.service._budget, "reserve_request", forbidden)
        response = await client.post("/api/pilot/coding-tasks", json=payload(c).model_dump())
        assert response.status_code == 503 and response.json()["code"] == "disabled"
        assert await c.service._store.list_tasks(c.owner) == [] and c.calls == []
        await c.service._store.close()
        response = await client.post("/api/pilot/coding-tasks", json=payload(c).model_dump())
        assert response.status_code == 503 and response.json()["code"] == "store"


@pytest.mark.parametrize("state", ["completed", "failed"])
async def test_saved_replay_conflict_corruption_scope_and_private_evidence(coding_api, monkeypatch, state):
    import sqlite3
    client, c, app = coding_api
    if state == "failed":
        c.response = json.dumps([{"path": "m.py", "old": "x = 1\n", "new": "x = 3"}])
    body = payload(c).model_dump()
    admitted = await client.post("/api/pilot/coding-tasks", json=body)
    assert admitted.status_code == 202, admitted.text
    task_id = admitted.json()["task_id"]
    view = await completed(client, task_id)
    assert view["state"] == state.upper()
    receipt = await client.get(f"/api/pilot/tasks/{task_id}/coding-receipt")
    assert receipt.status_code == 200
    assert (receipt.json()["receipt"] is not None) == (state == "completed")
    for field in ("task", "thread_id"):
        conflict = await client.post("/api/pilot/coding-tasks", json={**body, field: "different"})
        assert conflict.status_code == 409 and conflict.json()["code"] == "conflict"
    wrong_kind = await client.post("/api/pilot/tasks", json={"request_id": body["request_id"],
                                  "thread_id": body["thread_id"], "message": body["task"]})
    assert wrong_kind.status_code == 409
    app.state.owner_authenticator = OwnerAuthenticator(owner_id="foreign", subject_id="foreign", bearer_secret=SecretStr(TOKEN))
    foreign = await client.get(f"/api/pilot/tasks/{task_id}/coding-receipt")
    assert foreign.status_code == 401 and foreign.json()["code"] == "unauthorized"
    app.state.owner_authenticator = OwnerAuthenticator(owner_id=c.owner.owner_id, subject_id=c.owner.subject_id, bearer_secret=SecretStr(TOKEN))
    if state == "completed":
        with sqlite3.connect(c.root / "owner.sqlite") as conn:
            conn.execute("UPDATE pilot_coding_bindings SET evidence_json=?", (b"not text",))
        corrupt = await client.get(f"/api/pilot/tasks/{task_id}/coding-receipt")
        assert corrupt.status_code == 503 and corrupt.json()["code"] == "store"
    else:
        assert await c.service._store.list_messages(c.owner, c.thread.id) == []
        with sqlite3.connect(c.root / "owner.sqlite") as conn:
            evidence = json.loads(conn.execute("SELECT evidence_json FROM pilot_coding_bindings").fetchone()[0])
            assert evidence["receipt"]["objective_status"] == "failed"
    assert len(c.calls) == 1


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "duplicate"])
async def test_receipt_get_requires_exact_owner_auth(coding_api, authorization):
    client, c, _ = coding_api
    client.headers.pop("authorization")
    headers = [] if authorization is None else [("authorization", authorization)]
    if authorization == "duplicate":
        headers = [("authorization", "Bearer " + TOKEN)] * 2
    response = await client.get("/api/pilot/tasks/missing/coding-receipt", headers=headers)
    assert response.status_code == 401 and response.json()["code"] == "unauthorized"
    assert c.calls == [] and await c.service._store.list_tasks(c.owner) == []
