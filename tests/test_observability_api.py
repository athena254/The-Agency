"""Regression gate for the read-only owner observability surface.

The contract under test:
  * every ``/api/pilot/observe/*`` route is a ``GET`` behind the same loopback +
    Host + Origin + owner-bearer boundary as the owner pilot;
  * no write route exists, and no request can mutate local state;
  * the payload is honestly labelled read-only / peer-authority-unavailable;
  * the closed ``/v1/*`` business boundary is not relaxed by this surface.
"""

from __future__ import annotations

import inspect

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from agency.admission.auth import OwnerAuthenticator
from agency.evidence.store.models import EvidenceEntry, EvidenceLevel, Finding, Severity
from agency.evidence.store.store import EvidenceStore
from agency.kernel.audit import AuditEntry
from agency.kernel.identity import Agent, Capability, TrustLevel
from agency.kernel.policies import ActionClass
from agency.kernel.tasks import TaskStatus
from agency.memory.sms.models import MemoryItem, MemoryTier
from agency.prototype import observe
from agency.prototype.app import create_app
from tests.test_owner_service import concrete as concrete_fixture
from tests.test_owner_service import make_service

concrete = concrete_fixture

TOKEN = "SYNTHETIC_OWNER_TOKEN_32_CHARACTERS"
OBSERVE_PATHS = [
    "/api/pilot/observe/status",
    "/api/pilot/observe/runtime",
    "/api/pilot/observe/agents",
    "/api/pilot/observe/tasks",
    "/api/pilot/observe/memories",
    "/api/pilot/observe/evidence",
    "/api/pilot/observe/audit",
    "/api/pilot/observe/owner",
    "/api/pilot/observe/skills",
    "/api/pilot/observe/tools",
    "/api/pilot/observe/overview",
]


@pytest.fixture
async def api(concrete):
    c = concrete
    service = make_service(c)
    auth = OwnerAuthenticator(
        owner_id="owner",
        subject_id="subject",
        bearer_secret=SecretStr(TOKEN),
    )
    app = create_app(c["butler"], pilot=service, authenticator=auth)
    try:
        async with (
            app.router.lifespan_context(app),
            c["client"](transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client,
        ):
            client.headers.update(
                {
                    "Authorization": f"Bearer {TOKEN}",
                    "X-Prototype-CSRF": app.state.csrf_token,
                    "Origin": "http://127.0.0.1",
                }
            )
            yield client, c, app
    finally:
        # The shared ``concrete`` fixture does not own the orchestrator's own
        # stores. Leaving their aiosqlite worker threads open hangs interpreter
        # shutdown, so close them here.
        orchestrator = c["orch"]
        for store in (
            orchestrator._memory_store,
            orchestrator._evidence_store,
            orchestrator._audit_log,
        ):
            close = getattr(store, "close", None)
            if close is not None:
                await close()


async def _seed_local_state(c) -> None:
    """Populate the orchestrator's own stores so the reads prove they are real.

    The stores are initialized directly (not via ``orchestrator.start()``) because
    the Lattice lease is bound to the fixture's event loop, and this test runs in
    its own loop. The reads under test only need the stores, not the lease.
    """
    orch = c["orch"]
    await orch._memory_store.initialize()
    await orch._evidence_store.initialize()
    await orch._audit_log.initialize()
    agent = Agent(
        name="observer-agent",
        domain="research",
        capabilities=[Capability(name="inspect", max_level=ActionClass.L1_SAFE_ANALYSIS)],
        trust_level=TrustLevel.OBSERVED,
    )
    orch._identity_registry.register(agent)
    orch._runtime_registry.register(agent)
    task = await orch._task_manager.create_task(title="observe-me", created_by="observer-agent")
    await orch._task_manager.update_status(task.task_id, TaskStatus.COMPLETED)
    store = orch._memory_store
    await store.store(
        MemoryItem(agent_id="observer-agent", content="local memory body", tier=MemoryTier.HOT)
    )
    evidence: EvidenceStore = orch._evidence_store
    finding = await evidence.add_finding(
        Finding(
            target="local-target",
            evidence="local evidence summary",
            methodology="local probe",
            confidence=0.7,
            severity=Severity.HIGH,
            affected_component="local-component",
        )
    )
    await evidence.add_evidence(
        EvidenceEntry(finding_id=finding.id, level=EvidenceLevel.LEVEL_2_BEHAVIORAL)
    )
    await orch._audit_log.append(
        AuditEntry(agent="observer-agent", action=ActionClass.L1_SAFE_ANALYSIS, result="allowed")
    )


# --------------------------------------------------------------------------- #
# Boundary: same guards as the pilot router.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", OBSERVE_PATHS)
async def test_observe_reads_require_bearer(api, path):
    client, c, _ = api
    client.headers.pop("Authorization")
    response = await client.get(path)
    assert response.status_code == 401, (path, response.text)
    assert response.json()["code"] == "unauthorized"
    assert c["requests"] == []


@pytest.mark.parametrize("path", ["/api/pilot/observe/status", "/api/pilot/observe/overview"])
async def test_observe_rejects_foreign_origin(api, path):
    client, c, _ = api
    response = await client.get(path, headers={"Origin": "http://evil.invalid"})
    assert response.status_code == 403, response.text
    assert c["requests"] == []


@pytest.mark.parametrize("path", ["/api/pilot/observe/status", "/api/pilot/observe/agents"])
async def test_observe_rejects_wrong_bearer(api, path):
    client, c, _ = api
    response = await client.get(path, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401, response.text
    assert c["requests"] == []


async def test_observe_unconfigured_pilot_is_disabled_503():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        response = await client.get("/api/pilot/observe/overview")
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "disabled"


async def test_observe_surface_is_get_only():
    """No write route may ever be added to the observability router."""
    routes = [route for route in observe.router.routes if getattr(route, "methods", None)]
    assert routes, "observability router exposes no routes"
    for route in routes:
        assert route.methods == {"GET"}, (route.path, route.methods)


async def test_observe_module_exposes_no_mutating_helpers():
    """The module must not import a write entry point from any local store."""
    source = inspect.getsource(observe)
    for banned in ("add_finding", "add_evidence", ".store(", "update_finding", "transition("):
        assert banned not in source, banned


# --------------------------------------------------------------------------- #
# Honest labelling.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", OBSERVE_PATHS)
async def test_observe_payload_is_honestly_labelled(api, path):
    client, _c, _ = api
    response = await client.get(path)
    assert response.status_code == 200, (path, response.text)
    body = response.json()
    assert body["mode"] == "read_only", path
    assert body["peer_authority"] == "unavailable", path
    assert body["write_controls"] is False, path
    assert "peer authority is unavailable" in body["notice"].lower(), path


async def test_observe_security_headers_and_no_secret_leak(api):
    client, _c, _ = api
    for path in ("/api/pilot/observe/status", "/api/pilot/observe/overview"):
        response = await client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert TOKEN not in response.text
        assert "SYNTHETIC_KEY" not in response.text


# --------------------------------------------------------------------------- #
# Real local reads.
# --------------------------------------------------------------------------- #


async def test_observe_agents_reads_the_real_identity_registry(api):
    client, c, _ = api
    await _seed_local_state(c)
    body = (await client.get("/api/pilot/observe/agents")).json()
    names = {agent["name"] for agent in body["agents"]}
    assert "observer-agent" in names, body
    row = next(agent for agent in body["agents"] if agent["name"] == "observer-agent")
    assert row["domain"] == "research"
    assert row["trust_level"] == "observed"
    assert row["capabilities"] == [
        {"name": "inspect", "max_level": ActionClass.L1_SAFE_ANALYSIS.value}
    ]
    assert row["runtime_status"] == "idle"
    assert body["by_domain"]["research"] >= 1
    assert body["total"] == len(body["agents"])


async def test_observe_tasks_reads_the_real_task_manager(api):
    client, c, _ = api
    await _seed_local_state(c)
    body = (await client.get("/api/pilot/observe/tasks")).json()
    assert body["total"] == 1, body
    assert body["tasks"][0]["title"] == "observe-me"
    assert body["tasks"][0]["status"] == "completed"
    assert body["by_status"] == {"completed": 1}


async def test_observe_memories_reads_the_real_memory_store(api):
    client, c, _ = api
    await _seed_local_state(c)
    body = (await client.get("/api/pilot/observe/memories")).json()
    assert body["total"] == 1, body
    assert body["items"][0]["content"] == "local memory body"
    assert body["items"][0]["tier"] == "hot"
    assert body["items"][0]["agent_id"] == "observer-agent"
    assert body["by_tier"] == {"hot": 1}


async def test_observe_evidence_computes_local_heuristic_risk(api):
    client, c, _ = api
    await _seed_local_state(c)
    body = (await client.get("/api/pilot/observe/evidence")).json()
    assert body["total"] == 1, body
    row = body["findings"][0]
    assert row["target"] == "local-target"
    assert row["severity"] == "high"
    assert row["verification_status"] == "unverified"
    assert row["strongest_level"] == "LEVEL_2_BEHAVIORAL"
    assert row["evidence_count"] == 1
    assert row["risk_category"] in {"negligible", "low", "medium", "high", "critical"}
    assert 0.0 <= row["risk_impact"] <= 1.0
    assert body["method"] == "legacy_risk_engine/v1"
    assert body["heuristic_only"] is True
    assert {dimension["label"] for dimension in body["dimensions"]} >= {"impact", "likelihood"}


async def test_observe_audit_reads_the_real_audit_log(api):
    client, c, _ = api
    await _seed_local_state(c)
    body = (await client.get("/api/pilot/observe/audit")).json()
    assert body["total"] == 1, body
    assert body["entries"][0]["agent"] == "observer-agent"
    assert body["entries"][0]["action"] == ActionClass.L1_SAFE_ANALYSIS.value
    assert body["by_result"] == {"allowed": 1}


async def test_observe_owner_reads_owner_scoped_state(api):
    client, _c, _ = api
    made = await client.post(
        "/api/pilot/threads", json={"request_id": "observe-thread", "title": "Observe"}
    )
    assert made.status_code == 201, made.text
    body = (await client.get("/api/pilot/observe/owner")).json()
    assert body["owner_id"] == "owner"
    assert body["subject_id"] == "subject"
    assert body["thread_total"] == 1, body
    assert body["threads"][0]["title"] == "Observe"
    assert body["tasks"] == []


async def test_observe_skills_and_tools_report_shape_honestly(api):
    """Either real local rows or an honest unavailable reason — never a guess."""
    client, _c, _ = api
    skills = (await client.get("/api/pilot/observe/skills")).json()
    assert isinstance(skills["available"], bool)
    assert skills["total"] == len(skills["skills"])
    assert skills["total"] <= 200
    assert skills["truncated"] is (skills["total"] == 200)
    tools = (await client.get("/api/pilot/observe/tools")).json()
    assert isinstance(tools["available"], bool)
    assert tools["total"] == len(tools["tools"])


async def test_observe_overview_merges_every_section(api):
    client, c, _ = api
    await _seed_local_state(c)
    body = (await client.get("/api/pilot/observe/overview")).json()
    assert body["runtime"]["status"] == "started"
    assert body["runtime"]["pilot_configured"] is True
    assert body["agents"]["total"] >= 1
    assert body["tasks"]["total"] == 1
    assert body["memories"]["total"] == 1
    assert body["evidence"]["total"] == 1
    assert body["audit"]["total"] == 1
    assert body["collected_at"]
    assert body["risk"] == body["evidence"]
    assert body["owner"]["owner_id"] == "owner"
    assert body["owner"]["subject_id"] == "subject"


# --------------------------------------------------------------------------- #
# Read-only proof: the surface must not change local state.
# --------------------------------------------------------------------------- #


async def test_observe_reads_do_not_mutate_local_state(api):
    client, c, _ = api
    await _seed_local_state(c)
    orch = c["orch"]
    before = (
        len(await orch.list_agents()),
        len(await orch.list_tasks()),
        await orch._memory_store.count(),
        len(await orch._evidence_store.list_findings()),
    )
    for path in OBSERVE_PATHS:
        response = await client.get(path)
        assert response.status_code == 200, (path, response.text)
    after = (
        len(await orch.list_agents()),
        len(await orch.list_tasks()),
        await orch._memory_store.count(),
        len(await orch._evidence_store.list_findings()),
    )
    assert before == after


async def test_observe_methods_cannot_write(api):
    client, c, _ = api
    await _seed_local_state(c)
    for method in ("post", "patch", "put", "delete"):
        response = await client.request(
            method.upper(), "/api/pilot/observe/overview", json={"probe": True}
        )
        assert response.status_code in (405, 403, 401, 422), (method, response.status_code)
    assert len(await c["orch"].list_tasks()) == 1


async def test_observe_does_not_relax_the_closed_v1_boundary(api):
    """The business routers stay closed; observability is a separate surface.

    The prototype app does not mount ``/v1`` at all, so the closed business
    boundary is unreachable through this surface — 404, never a relaxed route.
    """
    client, c, _ = api
    for path in ("/v1/agents", "/v1/tasks", "/v1/evidence", "/v1/memory/search"):
        response = await client.get(path)
        assert response.status_code == 404, (path, response.text)
    assert c["requests"] == []


async def test_observe_isolated_from_the_business_app():
    """The business app factory must not grow the observability surface."""
    from agency.api import server

    app = server.create_app()
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert not any(path.startswith("/api/pilot/observe") for path in paths)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        response = await client.get("/v1/agents")
    assert response.status_code == 503, response.text


async def test_observe_works_from_a_clean_clone_without_env(concrete):
    """No provider, no tracked database, no live network is required."""
    service = make_service(concrete)
    auth = OwnerAuthenticator(
        owner_id="owner", subject_id="subject", bearer_secret=SecretStr(TOKEN)
    )
    app = create_app(concrete["butler"], pilot=service, authenticator=auth)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client,
    ):
        client.headers.update({"Authorization": f"Bearer {TOKEN}", "Origin": "http://127.0.0.1"})
        body = (await client.get("/api/pilot/observe/agents")).json()
        assert body["mode"] == "read_only"
        assert isinstance(body["agents"], list)


def test_observe_models_reject_unknown_fields():
    """Payloads are closed: the console cannot be handed an unexpected shape."""
    from pydantic import ValidationError

    from agency.prototype.observe import AgentsView

    with pytest.raises(ValidationError):
        AgentsView(mode="read_only", extra="forged", agents=[], total=0)
