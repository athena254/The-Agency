"""Regression gate for the owner-guarded Forge API surface."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from agency.admission.auth import OwnerAuthenticator
from agency.forge import orchestration
from agency.forge.external_agent import ExternalAgentReport
from agency.forge.inspection import InspectionReport
from agency.forge.orchestration import BlacksmithOps
from agency.forge.security_scan import SecurityReport
from agency.prototype import forge_api as forge_api_module
from agency.prototype.app import create_app
from agency.prototype.forge_api import ForgeGateway
from tests.test_owner_service import concrete as concrete_fixture
from tests.test_owner_service import make_service

concrete = concrete_fixture

_TOKEN = "SYNTHETIC_OWNER_TOKEN_32_CHARACTERS"
_CAVEATS = ["tests_not_run", "security_review_not_run", "release_not_authorized"]
_DISPATCH_CAVEATS = ["external_agent_runs", "no_ship_no_merge_no_release"]
_SECRET_TEXT = "SYNTHETIC_KEY_CANARY"
_SECRET_PATH = "C:\\canary\\private\\path"
_PAYLOAD = {
    "request_id": "forge-1",
    "title": "Synthetic forge job",
    "task": "Add one tested line.",
    "language": "python",
    "acceptance": ["tests pass"],
    "test_paths": ["tests/test_example.py"],
}
_ERROR_KEYS = {
    "error",
    "code",
    "request_id",
    "task_id",
    "uncertain",
    "retryable",
}


class FakeRunner:
    def __init__(self) -> None:
        self.gate: Literal["PASS", "FAIL"] = "PASS"
        self.error: Exception | None = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> ExternalAgentReport:
        self.calls += 1
        self.entered.set()
        self.release.wait(timeout=5)
        if self.error is not None:
            raise self.error
        now = datetime.now(UTC)
        return ExternalAgentReport(
            report_id="synthetic-report",
            created_at=now,
            worktree_path="synthetic-worktree",
            branch="synthetic-branch",
            command=("opencode", "run"),
            task="synthetic task",
            exit_code=0 if self.gate == "PASS" else 1,
            changed_paths=("synthetic.py",),
            failure_detail=f"{_SECRET_TEXT} at {_SECRET_PATH}" if self.gate == "FAIL" else "",
            inspection=InspectionReport(
                report_id="synthetic-inspection",
                created_at=now,
                root="synthetic-worktree",
                gate="PASS",
            ),
            security=SecurityReport(
                report_id="synthetic-security",
                created_at=now,
                root="synthetic-worktree",
                gate="PASS",
                scanned_files=0,
                skipped_files=0,
            ),
            gate=self.gate,
        )


@pytest.fixture(autouse=True)
def fake_external_agent(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    runner = FakeRunner()
    runner.release.set()
    monkeypatch.setattr(orchestration, "run_external_agent", runner)
    return runner


@pytest.fixture
async def forge_api(concrete, tmp_path: Path):
    service = make_service(concrete)
    auth = OwnerAuthenticator(
        owner_id="owner",
        subject_id="subject",
        bearer_secret=SecretStr(_TOKEN),
    )
    app = create_app(
        concrete["butler"],
        pilot=service,
        authenticator=auth,
        forge_db=tmp_path / "forge_jobs.db",
        forge_repo=tmp_path,
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client,
    ):
        client.headers.update(
            {
                "Authorization": f"Bearer {_TOKEN}",
                "X-Prototype-CSRF": app.state.csrf_token,
                "Origin": "http://127.0.0.1",
            },
        )
        yield client, concrete, app, tmp_path


async def _wait_dispatch(client: AsyncClient) -> dict[str, Any]:
    for _ in range(250):
        response = await client.get("/api/pilot/forge/dispatch")
        assert response.status_code == 200, response.text
        body = response.json()
        if not body["running"] and body["last"] is not None:
            return body
        await asyncio.sleep(0.02)
    raise AssertionError("dispatch did not complete")


async def test_forge_routes_are_minimal():
    app = create_app()
    routes = set()
    for included in app.routes:
        owner = getattr(included, "original_router", included)
        for route in getattr(owner, "routes", []):
            path = getattr(route, "path", "")
            if path.startswith("/api/pilot/forge"):
                routes.add((path, tuple(sorted(getattr(route, "methods", ())))))
    assert routes == {
        ("/api/pilot/forge/status", ("GET",)),
        ("/api/pilot/forge/metrics", ("GET",)),
        ("/api/pilot/forge/dispatch", ("GET",)),
        ("/api/pilot/forge/dispatch", ("POST",)),
        ("/api/pilot/forge/jobs", ("POST",)),
        ("/api/pilot/forge/jobs/{job_id}", ("GET",)),
    }
    for forbidden in ("ship", "merge", "release", "cancel", "resurrect", "ingest", "build_tool"):
        assert all(forbidden not in path for path, _methods in routes)


async def test_unconfigured_forge_is_disabled_503():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        for path in ("/api/pilot/forge/status", "/api/pilot/forge/dispatch"):
            response = await client.get(path)
            assert response.status_code == 503, response.text
            assert response.json()["code"] == "disabled"
        response = await client.post("/api/pilot/forge/dispatch", json={})
        assert response.status_code == 503, response.text
        assert response.json()["code"] == "disabled"


async def test_invalid_repo_disables_forge_without_leaking_path(concrete, tmp_path: Path):
    auth = OwnerAuthenticator(
        owner_id="owner",
        subject_id="subject",
        bearer_secret=SecretStr(_TOKEN),
    )
    app = create_app(
        concrete["butler"],
        pilot=make_service(concrete),
        authenticator=auth,
        forge_db=tmp_path / "forge_jobs.db",
        forge_repo=tmp_path / "missing",
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client,
    ):
        client.headers.update(
            {
                "Authorization": f"Bearer {_TOKEN}",
                "X-Prototype-CSRF": app.state.csrf_token,
                "Origin": "http://127.0.0.1",
            },
        )
        for path in (
            "/api/pilot/forge/status",
            "/api/pilot/forge/metrics",
            "/api/pilot/forge/jobs/unknown",
            "/api/pilot/forge/dispatch",
        ):
            response = await client.get(path)
            assert response.status_code == 503, response.text
            assert set(response.json()) == _ERROR_KEYS
            assert response.json()["code"] == "disabled"
            assert str(tmp_path) not in response.text
        for path in ("/api/pilot/forge/jobs", "/api/pilot/forge/dispatch"):
            submitted = await client.post(path, json=_PAYLOAD if path.endswith("jobs") else {})
            assert submitted.status_code == 503, submitted.text
            assert submitted.json()["code"] == "disabled"
            assert str(tmp_path) not in submitted.text
    assert not (tmp_path / "forge_jobs.db").exists()


async def test_status_metrics_submit_get_replay_conflict_and_sanitization(forge_api):
    client, c, app, tmp_path = forge_api
    initial = await client.get("/api/pilot/forge/status")
    assert initial.status_code == 200, initial.text
    assert set(initial.json()) == {"status", "caveats"}
    assert initial.json()["caveats"] == _CAVEATS
    assert set(initial.json()["status"]) == {"total", "by_state", "blocked", "recent"}
    assert initial.json()["status"] == {"total": 0, "by_state": {}, "blocked": [], "recent": []}
    initial_metrics = await client.get("/api/pilot/forge/metrics")
    assert initial_metrics.status_code == 200, initial_metrics.text
    assert set(initial_metrics.json()) == {"metrics", "caveats"}
    assert initial_metrics.json()["caveats"] == _CAVEATS
    assert initial_metrics.json()["metrics"]["total_jobs"] == 0

    created = await client.post("/api/pilot/forge/jobs", json=_PAYLOAD)
    assert created.status_code == 202, created.text
    body = created.json()
    assert set(body) == {"job", "replayed", "caveats"}
    assert body["replayed"] is False
    assert body["caveats"] == ["queued_not_dispatched"]
    job = body["job"]
    assert job["state"] == "QUEUED"
    assert job["title"] == _PAYLOAD["title"]
    assert job["acceptance"] == _PAYLOAD["acceptance"]
    assert job["test_paths"] == _PAYLOAD["test_paths"]
    assert "repo" not in job
    assert "failure_detail" not in job
    assert "changed_paths" not in job
    assert str(tmp_path) not in created.text
    assert _TOKEN not in created.text
    assert _SECRET_TEXT not in created.text
    job_id = job["job_id"]

    fetched = await client.get(f"/api/pilot/forge/jobs/{job_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == {"job": job}
    missing = await client.get("/api/pilot/forge/jobs/unknown")
    assert missing.status_code == 404, missing.text
    assert missing.json()["code"] == "not_found"

    replay = await client.post("/api/pilot/forge/jobs", json=_PAYLOAD)
    assert replay.status_code == 200, replay.text
    assert replay.json() == {"job": job, "replayed": True, "caveats": ["queued_not_dispatched"]}

    conflict = await client.post(
        "/api/pilot/forge/jobs", json={**_PAYLOAD, "task": "Different task."}
    )
    assert conflict.status_code == 409, conflict.text
    assert set(conflict.json()) == _ERROR_KEYS
    assert conflict.json()["code"] == "conflict"
    assert "Different task." not in conflict.text

    status = await client.get("/api/pilot/forge/status")
    assert status.status_code == 200, status.text
    assert status.json()["caveats"] == _CAVEATS
    assert status.json()["status"]["total"] == 1
    assert [row["job_id"] for row in status.json()["status"]["recent"]] == [job_id]
    assert all("repo" not in row for row in status.json()["status"]["recent"])
    metrics = await client.get("/api/pilot/forge/metrics")
    assert metrics.status_code == 200, metrics.text
    assert metrics.json()["caveats"] == _CAVEATS
    assert metrics.json()["metrics"]["total_jobs"] == 1
    assert c["requests"] == []
    assert isinstance(app.state.forge_gateway, ForgeGateway)


@pytest.mark.parametrize(
    "headers,body,status,code",
    [
        ({"Authorization": "Bearer wrong"}, _PAYLOAD, 401, "unauthorized"),
        ({"X-Prototype-CSRF": "wrong"}, _PAYLOAD, 403, "forbidden"),
        ({"Origin": "http://evil.invalid"}, _PAYLOAD, 403, "forbidden"),
        ({"Content-Type": "text/plain"}, _PAYLOAD, 415, "media"),
    ],
    ids=["wrong-bearer", "wrong-csrf", "foreign-origin", "wrong-media"],
)
async def test_forge_write_guard_rejects_before_any_effect(forge_api, headers, body, status, code):
    client, c, _app, _tmp_path = forge_api
    response = await client.post("/api/pilot/forge/jobs", json=body, headers=headers)
    assert response.status_code == status, response.text
    assert set(response.json()) == _ERROR_KEYS
    assert response.json()["code"] == code
    assert c["requests"] == []


async def test_forge_read_routes_require_bearer(forge_api):
    client, c, _app, _tmp_path = forge_api
    client.headers.pop("Authorization")
    for path in (
        "/api/pilot/forge/status",
        "/api/pilot/forge/metrics",
        "/api/pilot/forge/jobs/unknown",
        "/api/pilot/forge/dispatch",
    ):
        response = await client.get(path)
        assert response.status_code == 401, response.text
        assert response.json()["code"] == "unauthorized"
    assert c["requests"] == []


async def test_forge_rejects_oversized_body(forge_api):
    client, c, _app, _tmp_path = forge_api
    response = await client.post(
        "/api/pilot/forge/jobs",
        content=b"x" * 32769,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413, response.text
    assert response.json()["code"] == "too_large"
    assert c["requests"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**_PAYLOAD, "request_id": "bad id!"},
        {**_PAYLOAD, "title": ""},
        {**_PAYLOAD, "task": ""},
        {**_PAYLOAD, "language": "x" * 33},
        {**_PAYLOAD, "acceptance": ["x" * 1001]},
        {**_PAYLOAD, "test_paths": ["p"] * 17},
        {**_PAYLOAD, "forbidden": True},
    ],
    ids=[
        "empty",
        "bad-request-id",
        "empty-title",
        "empty-task",
        "long-language",
        "long-acceptance",
        "too-many-test-paths",
        "extra-field",
    ],
)
async def test_forge_rejects_invalid_payload(forge_api, payload):
    client, c, _app, _tmp_path = forge_api
    response = await client.post("/api/pilot/forge/jobs", json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "invalid_request"
    assert c["requests"] == []


async def test_forge_responses_have_owner_security_headers(forge_api):
    client, _c, _app, tmp_path = forge_api
    created = await client.post("/api/pilot/forge/jobs", json=_PAYLOAD)
    assert created.status_code == 202, created.text
    for path in (
        "/api/pilot/forge/status",
        "/api/pilot/forge/metrics",
        f"/api/pilot/forge/jobs/{created.json()['job']['job_id']}",
    ):
        response = await client.get(path)
        assert response.status_code == 200, response.text
        assert _TOKEN not in response.text
        assert _SECRET_TEXT not in response.text
        assert str(tmp_path) not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'none'" in response.headers["content-security-policy"]


async def test_dispatch_start_and_status(forge_api, fake_external_agent):
    client, c, _app, tmp_path = forge_api
    initial = await client.get("/api/pilot/forge/dispatch")
    assert initial.status_code == 200, initial.text
    assert initial.json() == {"running": False, "last": None}

    created = await client.post("/api/pilot/forge/jobs", json=_PAYLOAD)
    assert created.status_code == 202, created.text
    job_id = created.json()["job"]["job_id"]

    started = await client.post("/api/pilot/forge/dispatch", json={})
    assert started.status_code == 202, started.text
    assert started.json() == {
        "started": True,
        "replayed": False,
        "caveats": _DISPATCH_CAVEATS,
    }
    body = await _wait_dispatch(client)
    assert set(body) == {"running", "last"}
    assert body["running"] is False
    last = body["last"]
    assert set(last) == {"ok", "job_id", "state", "error"}
    assert last == {"ok": True, "job_id": job_id, "state": "AWAITING_HUMAN", "error": None}
    assert fake_external_agent.calls == 1
    for response_text in (started.text, initial.text, str(body)):
        assert _TOKEN not in response_text
        assert _SECRET_TEXT not in response_text
        assert str(tmp_path) not in response_text
    assert c["requests"] == []


async def test_dispatch_conflict_and_concurrent_reads(forge_api, fake_external_agent):
    client, c, _app, _tmp_path = forge_api
    created = await client.post("/api/pilot/forge/jobs", json=_PAYLOAD)
    assert created.status_code == 202, created.text
    job_id = created.json()["job"]["job_id"]

    fake_external_agent.release.clear()
    try:
        started = await client.post("/api/pilot/forge/dispatch", json={})
        assert started.status_code == 202, started.text
        assert fake_external_agent.entered.wait(timeout=5)
        running = await client.get("/api/pilot/forge/dispatch")
        assert running.json() == {"running": True, "last": None}
        conflict = await client.post("/api/pilot/forge/dispatch", json={})
        assert conflict.status_code == 409, conflict.text
        assert set(conflict.json()) == _ERROR_KEYS
        assert conflict.json()["code"] == "conflict"
        status = await client.get("/api/pilot/forge/status")
        assert status.status_code == 200, status.text
        assert status.json()["status"]["total"] == 1
        metrics = await client.get("/api/pilot/forge/metrics")
        assert metrics.status_code == 200, metrics.text
        assert metrics.json()["metrics"]["total_jobs"] == 1
        second = await client.post(
            "/api/pilot/forge/jobs", json={**_PAYLOAD, "request_id": "forge-2"}
        )
        assert second.status_code == 202, second.text
    finally:
        fake_external_agent.release.set()

    body = await _wait_dispatch(client)
    assert body["last"]["job_id"] == job_id
    assert fake_external_agent.calls == 1
    assert c["requests"] == []


async def test_dispatch_write_guard_rejects_before_dispatch(forge_api, fake_external_agent):
    client, _c, _app, _tmp_path = forge_api
    client.headers.pop("Authorization")
    unauthorized = await client.post("/api/pilot/forge/dispatch", json={})
    assert unauthorized.status_code == 401, unauthorized.text
    assert unauthorized.json()["code"] == "unauthorized"
    client.headers.update({"Authorization": f"Bearer {_TOKEN}"})

    csrf = client.headers.pop("X-Prototype-CSRF")
    forbidden = await client.post("/api/pilot/forge/dispatch", json={})
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["code"] == "forbidden"
    client.headers.update({"X-Prototype-CSRF": str(csrf)})

    extra = await client.post("/api/pilot/forge/dispatch", json={"ship": True})
    assert extra.status_code == 422, extra.text
    assert extra.json()["code"] == "invalid_request"
    too_large = await client.post(
        "/api/pilot/forge/dispatch",
        content=b"x" * 32769,
        headers={"Content-Type": "application/json"},
    )
    assert too_large.status_code == 413, too_large.text
    assert too_large.json()["code"] == "too_large"
    assert fake_external_agent.calls == 0
    status = await client.get("/api/pilot/forge/dispatch")
    assert status.json() == {"running": False, "last": None}


async def test_dispatch_error_is_sanitized(forge_api, monkeypatch, tmp_path):
    client, _c, _app, _tmp_path = forge_api

    def explode(self: Any) -> None:
        raise RuntimeError(f"{_SECRET_TEXT} at {_SECRET_PATH}")

    monkeypatch.setattr(BlacksmithOps, "dispatch_next", explode)
    started = await client.post("/api/pilot/forge/dispatch", json={})
    assert started.status_code == 202, started.text
    body = await _wait_dispatch(client)
    assert body["last"] == {
        "ok": False,
        "job_id": None,
        "state": None,
        "error": "RuntimeError",
    }
    response = await client.get("/api/pilot/forge/dispatch")
    assert _SECRET_TEXT not in response.text
    assert _SECRET_PATH not in response.text
    assert str(tmp_path) not in response.text


async def test_dispatch_failure_detail_never_exposed(forge_api, fake_external_agent, tmp_path):
    client, _c, _app, _tmp_path = forge_api
    fake_external_agent.gate = "FAIL"
    created = await client.post("/api/pilot/forge/jobs", json=_PAYLOAD)
    assert created.status_code == 202, created.text
    job_id = created.json()["job"]["job_id"]

    started = await client.post("/api/pilot/forge/dispatch", json={})
    assert started.status_code == 202, started.text
    body = await _wait_dispatch(client)
    last = body["last"]
    assert last["ok"] is True
    assert last["job_id"] == job_id
    assert last["state"] in {"FAILED", "BLOCKED"}
    assert last["error"] is None
    response = await client.get("/api/pilot/forge/dispatch")
    assert _SECRET_TEXT not in response.text
    assert _SECRET_PATH not in response.text
    assert str(tmp_path) not in response.text
    job = await client.get(f"/api/pilot/forge/jobs/{job_id}")
    assert _SECRET_TEXT not in job.text
    assert _SECRET_PATH not in job.text


async def test_close_forge_joins_dispatch_within_bound(
    concrete, tmp_path: Path, monkeypatch, fake_external_agent
):
    assert forge_api_module._SHUTDOWN_JOIN_S == 5
    monkeypatch.setattr(forge_api_module, "_SHUTDOWN_JOIN_S", 0.05)
    auth = OwnerAuthenticator(
        owner_id="owner",
        subject_id="subject",
        bearer_secret=SecretStr(_TOKEN),
    )
    app = create_app(
        concrete["butler"],
        pilot=make_service(concrete),
        authenticator=auth,
        forge_db=tmp_path / "forge_jobs.db",
        forge_repo=tmp_path,
    )
    fake_external_agent.release.clear()
    started_at = time.monotonic()
    try:
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client,
        ):
            client.headers.update(
                {
                    "Authorization": f"Bearer {_TOKEN}",
                    "X-Prototype-CSRF": app.state.csrf_token,
                    "Origin": "http://127.0.0.1",
                },
            )
            created = await client.post("/api/pilot/forge/jobs", json=_PAYLOAD)
            assert created.status_code == 202, created.text
            response = await client.post("/api/pilot/forge/dispatch", json={})
            assert response.status_code == 202, response.text
            assert fake_external_agent.entered.wait(timeout=5)
        assert time.monotonic() - started_at < 3
    finally:
        fake_external_agent.release.set()
