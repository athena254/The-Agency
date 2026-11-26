"""Integration tests for the Butler conversational gateway.

Covers keyword routing, the orchestrator execution pipeline
(submit_task + execute_task), memory/audit persistence, and the
FastAPI HTTP surface.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agency.agents.prompts import issued_system_prompt
from agency.butler.config import ButlerConfig
from agency.butler.router import MessageRouter
from agency.butler.server import create_app
from agency.butler.service import ButlerService
from agency.factory.default_agents import DEFAULT_AGENT_DOMAINS
from agency.factory.prompts import PromptRegistry
from agency.factory.service import AgentFactory
from agency.orchestrator import AgencyOrchestrator


@pytest_asyncio.fixture
async def service() -> ButlerService:
    # Isolated memory db: the default relative path shares state across every
    # test in the worktree, and list_all(limit=100) hides new turns once the
    # accumulated file crosses 100 rows.
    svc = ButlerService(
        config=ButlerConfig(), orchestrator=AgencyOrchestrator(memory_db_path=":memory:")
    )
    await svc.start()
    try:
        yield svc
    finally:
        await svc.stop()


# ------------------------------------------------------------------ #
# Router
# ------------------------------------------------------------------ #


def test_detect_domains() -> None:
    router = MessageRouter()
    assert router.detect_domain("scan for vulnerabilities and exploits") == "security"
    assert router.detect_domain("please remember what we discussed") == "memory"
    assert router.detect_domain("verify this forensic evidence report") == "evidence"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param("scan for evidence", "security", id="security-before-evidence"),
        pytest.param("remember the risk", "memory", id="memory-before-risk"),
        pytest.param("firewall and proof", "security", id="security-before-evidence-2"),
    ],
)
def test_detect_domain_ties_resolve_to_first_declared(message: str, expected: str) -> None:
    router = MessageRouter()
    assert router.detect_domain(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
        pytest.param("xyzzy qwerty", id="no-hit"),
    ],
)
def test_detect_domain_returns_none_without_signal(message: str) -> None:
    router = MessageRouter()
    assert router.detect_domain(message) is None


def test_detect_domain_single_hit_unchanged() -> None:
    router = MessageRouter()
    assert router.detect_domain("scan for vulnerabilities and exploits") == "security"


@pytest.mark.asyncio
async def test_router_routes_to_correct_agent(service: ButlerService) -> None:
    agent = await service.route("check the firewall for intrusions", {"sender": "t"})
    assert agent.domain == "security"
    agent = await service.route("research the latest AI news", {"sender": "t"})
    assert agent.domain == "research"


# ------------------------------------------------------------------ #
# Service pipeline
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_handle_message_returns_response(service: ButlerService) -> None:
    response = await service.handle_message("scan for vulnerabilities", "tester", {})
    assert isinstance(response, str) and response


@pytest.mark.asyncio
async def test_handle_message_uses_orchestrator_pipeline(service: ButlerService) -> None:
    before = await service.orchestrator.list_tasks()
    response = await service.handle_message("assess the risk exposure", "tester", {})
    assert response
    after = await service.orchestrator.list_tasks()
    assert len(after) == len(before) + 1
    assert after[-1].status.value == "completed"


@pytest.mark.asyncio
async def test_handle_message_stores_memory_and_audit(service: ButlerService) -> None:
    response = await service.handle_message("remember this security finding", "tester", {})
    assert response
    # The turn is persisted in the Butler's own memory store ...
    turns = await service._memory.list_all(limit=100)
    assert any("remember this security finding" in t.content for t in turns)
    # ... and audit-logged.
    entries = await service._audit.query()
    actions = {e.action for e in entries}
    assert "butler.handle_message" in actions
    assert "butler.route" in actions


@pytest.mark.asyncio
async def test_handle_message_rejects_empty(service: ButlerService) -> None:
    with pytest.raises(ValueError):
        await service.handle_message("   ", "tester", {})


# ------------------------------------------------------------------ #
# HTTP API
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_http_message_flow() -> None:
    app = create_app(ButlerConfig())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/v1/health")
        assert health.status_code == 200
        assert health.json()["status"] in ("ok", "starting")
        assert (await client.get("/v1/agents")).status_code == 503
        for message in (
            "scan the network for threats",
            "recall what we discussed yesterday",
            "verify the forensic evidence",
        ):
            resp = await client.post(
                "/v1/message", json={"message": message, "sender": "http-test"}
            )
            assert resp.status_code == 503
            assert resp.json() == {"detail": "Peer authorization is unavailable."}


# ------------------------------------------------------------------ #
# Factory-issued default agents (PLAN_FACTORY_MANAGED_DEFAULT_AGENTS #3)
# ------------------------------------------------------------------ #


def _allow_approve_and_recover(actor: str, permission: str) -> bool:
    return permission in {"agent.approve", "agent.recover"}


def _wired_service(tmp_path) -> tuple[ButlerService, AgentFactory, PromptRegistry]:
    orchestrator = AgencyOrchestrator(memory_db_path=":memory:")
    prompts = PromptRegistry(str(tmp_path / "prompts.db"))
    factory = AgentFactory(
        str(tmp_path / "factory.db"),
        authorizer=_allow_approve_and_recover,
        allowed_capabilities=set(DEFAULT_AGENT_DOMAINS) | {"respond"},
        kernel_registry=orchestrator._identity_registry,
        runtime_registry=orchestrator._runtime_registry,
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
    )
    service = ButlerService(
        config=ButlerConfig(),
        orchestrator=orchestrator,
        factory=factory,
        prompt_registry=prompts,
        seed_authorizer=_allow_approve_and_recover,
        seed_approver="human-lead",
    )
    return service, factory, prompts


@pytest.mark.asyncio
async def test_factory_wired_seeding_registers_factory_issued_agents(tmp_path) -> None:
    service, factory, prompts = _wired_service(tmp_path)
    await service.start()
    try:
        by_domain = {a.domain: a for a in service.router.list_agents()}
        assert set(by_domain) == set(DEFAULT_AGENT_DOMAINS)
        for domain, agent in by_domain.items():
            assert agent.metadata["factory"] == "agent-factory-v1"
            assert agent.metadata["blueprint_id"] == f"{domain}-agent"
            prompt = issued_system_prompt(agent)
            assert prompt
            assert agent.metadata["system_prompt"] == prompt
            # The orchestrator resolves the issued identity at execution time.
            resolved = service.orchestrator._identity_registry.get(agent.id)
            assert resolved is agent
        # Re-start must not duplicate anything (idempotent seeding).
        agents_after = service.router.list_agents()
        assert len(agents_after) == len(DEFAULT_AGENT_DOMAINS)
    finally:
        await service.stop()
        factory.close()
        prompts.close()


@pytest.mark.asyncio
async def test_factory_wired_without_authorizer_fails_closed(tmp_path) -> None:
    service, factory, prompts = _wired_service(tmp_path)
    service._seed_authorizer = None
    with pytest.raises(ValueError, match="fail closed"):
        await service.start()
    factory.close()
    prompts.close()


@pytest.mark.asyncio
async def test_partially_wired_factory_fails_closed(tmp_path) -> None:
    service, factory, prompts = _wired_service(tmp_path)
    service._prompt_registry = None
    with pytest.raises(ValueError, match="prompt_registry"):
        await service.start()
    factory.close()
    prompts.close()


@pytest.mark.asyncio
async def test_legacy_seeding_unchanged_without_factory(service: ButlerService) -> None:
    agents = service.router.list_agents()
    assert {a.domain for a in agents} == set(DEFAULT_AGENT_DOMAINS)
    for agent in agents:
        assert "factory" not in agent.metadata
        assert "system_prompt" not in agent.metadata
