"""External entry point for ``RequestedAgentService.request_agent`` (gaps.md #6, #14).

Covers the fail-closed ``agent.request`` adapter (``request_agent_authorized``),
the HTTP route (``POST /v1/agent-requests``), the thin CLI command
(``agency agent request``), and the centralized agent bootstrap
(``bootstrap_agents``) that every entry point applies at startup.

The app is built without running its lifespan (routers get minimal state
manually), matching the repo's existing httpx ASGITransport test pattern.
"""

from __future__ import annotations

import json
from typing import Any, Self

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

import agency.api.server as server_module
from agency.agents.bootstrap import (
    AGENT_REQUEST_PERMISSION,
    bootstrap_agents,
    request_agent_authorized,
)
from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.api.server import create_app
from agency.factory.prompts import PromptRegistry
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.requested_agent import (
    RequestedAgentRequest,
    RequestedAgentResult,
    RequestedAgentService,
)
from agency.factory.service import AgentFactory
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.skills.registry import SkillRegistry, SkillStatus

OWNER_TOKEN = "sk-owner-token-with-at-least-24-chars"
AGENT1_TOKEN = "sk-agent1-token-with-at-least-24-char"

TOKENS = [
    {"token": OWNER_TOKEN, "caller_id": "danny", "role": "owner"},
    {"token": AGENT1_TOKEN, "caller_id": "agent-coder-1", "role": "agent"},
]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _request(**overrides: Any) -> RequestedAgentRequest:
    args: dict[str, Any] = {
        "blueprint_id": "scout",
        "name": "Scout",
        "purpose": "Search documents by keyword.",
        "creator": "danny",
        "domain": "research",
    }
    args.update(overrides)
    return RequestedAgentRequest(**args)


def _service(tmp_path) -> tuple[RequestedAgentService, AgentFactory, KernelRegistry]:  # type: ignore[no-untyped-def]
    prompts = PromptRegistry(str(tmp_path / "prompts.db"))
    ensure_builtin_prompts(prompts)
    skills = SkillRegistry(str(tmp_path / "skills.db"))
    kernels = KernelRegistry()
    factory = AgentFactory(
        str(tmp_path / "factory.db"),
        authorizer=lambda actor, perm: perm == "agent.approve",
        allowed_capabilities={"general", "search"},
        kernel_registry=kernels,
        runtime_registry=RuntimeRegistry(),
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
        is_skill_published=lambda sid, ver: skills.get(sid, ver).status is SkillStatus.PUBLISHED,
    )
    service = RequestedAgentService(factory=factory, prompt_registry=prompts, skill_registry=skills)
    return service, factory, kernels


@pytest.fixture
def app_and_state(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    tokens_file = tmp_path / "tokens.json"
    tokens_file.write_text(json.dumps(TOKENS), encoding="utf-8")
    monkeypatch.setenv("AGENCY_API_TOKENS", str(tokens_file))
    app = create_app()
    app.state.task_manager = None
    service, factory, kernels = _service(tmp_path)
    app.state.requested_agent_service = service
    app.state.agent_registry = kernels
    app.state.agent_request_authorizer = lambda actor, perm: (
        perm == AGENT_REQUEST_PERMISSION and actor == "danny"
    )
    app.state.agent_request_domains = ("research",)
    return app, service, factory


# --------------------------------------------------------------------------- #
# Adapter: fail-closed authorizer + domain allowlist (gaps.md #6)
# --------------------------------------------------------------------------- #


def test_adapter_fails_closed_without_authorizer(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, _factory, _kernels = _service(tmp_path)
    with pytest.raises(PermissionError, match="no agent.request authorizer"):
        request_agent_authorized(service, _request(), authorizer=None, allowed_domains=["research"])


def test_adapter_denies_denied_or_nontrue_authorizer(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, _factory, _kernels = _service(tmp_path)
    with pytest.raises(PermissionError, match="denied"):
        request_agent_authorized(
            service, _request(), authorizer=lambda actor, perm: False, allowed_domains=["research"]
        )
    # Anything but exactly True is a denial (mirrors AgentFactory.approve).
    with pytest.raises(PermissionError, match="denied"):
        request_agent_authorized(
            service, _request(), authorizer=lambda actor, perm: 1, allowed_domains=["research"]
        )


def test_adapter_fails_closed_when_authorizer_raises(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, _factory, _kernels = _service(tmp_path)

    def broken(actor: str, perm: str) -> bool:
        raise RuntimeError("boom")

    with pytest.raises(PermissionError, match="failed closed"):
        request_agent_authorized(
            service, _request(), authorizer=broken, allowed_domains=["research"]
        )


def test_adapter_denies_empty_allowlist(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, _factory, _kernels = _service(tmp_path)
    with pytest.raises(PermissionError, match="allowlist is empty"):
        request_agent_authorized(
            service,
            _request(),
            authorizer=lambda actor, perm: True,
            allowed_domains=(),
        )


def test_adapter_rejects_domain_outside_allowlist(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, _factory, _kernels = _service(tmp_path)
    with pytest.raises(PermissionError, match="not in the agent-request allowlist"):
        request_agent_authorized(
            service,
            _request(domain="coding"),
            authorizer=lambda actor, perm: True,
            allowed_domains=("research",),
        )
    # Matching is explicit, whitespace/case tolerant.
    result = request_agent_authorized(
        service,
        _request(domain=" Research "),
        authorizer=lambda actor, perm: True,
        allowed_domains=("research",),
    )
    assert result.agent_id


def test_adapter_delegates_to_request_agent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, factory, kernels = _service(tmp_path)
    consulted: list[tuple[str, str]] = []

    def authorizer(actor: str, permission: str) -> bool:
        consulted.append((actor, permission))
        return True

    result: RequestedAgentResult = request_agent_authorized(
        service, _request(), authorizer=authorizer, allowed_domains=("research", "coding")
    )
    assert consulted == [("danny", AGENT_REQUEST_PERMISSION)]
    assert result.blueprint_id == "scout" and result.agent_id
    assert kernels.get(result.agent_id) is not None
    assert factory.get("scout", "1.0.0").creator == "danny"


# --------------------------------------------------------------------------- #
# HTTP route: POST /v1/agent-requests
# --------------------------------------------------------------------------- #


async def test_route_requires_bearer_token(app_and_state) -> None:  # type: ignore[no-untyped-def]
    app, _service, _factory = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/agent-requests", json={})
    assert response.status_code == 401


async def test_route_unconfigured_service_fails_closed(
    tmp_path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    tokens_file = tmp_path / "tokens.json"
    tokens_file.write_text(json.dumps(TOKENS), encoding="utf-8")
    monkeypatch.setenv("AGENCY_API_TOKENS", str(tokens_file))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/agent-requests",
            json={"blueprint_id": "scout", "name": "Scout", "purpose": "find things"},
            headers=_auth(OWNER_TOKEN),
        )
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


async def test_route_denied_by_authorizer(app_and_state) -> None:  # type: ignore[no-untyped-def]
    app, _service, _factory = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/agent-requests",
            json={"blueprint_id": "scout", "name": "Scout", "purpose": "find things"},
            headers=_auth(AGENT1_TOKEN),
        )
    assert response.status_code == 403
    assert "agent-coder-1" in response.json()["detail"]


async def test_route_rejects_domain_outside_allowlist(app_and_state) -> None:  # type: ignore[no-untyped-def]
    app, _service, _factory = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/agent-requests",
            json={
                "blueprint_id": "smith",
                "name": "Smith",
                "purpose": "hammer metal",
                "domain": "forge",
            },
            headers=_auth(OWNER_TOKEN),
        )
    assert response.status_code == 403


async def test_route_creates_governed_agent_bound_to_caller(app_and_state) -> None:  # type: ignore[no-untyped-def]
    app, _service, factory = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/agent-requests",
            json={
                "blueprint_id": "scout",
                "name": "Scout",
                "purpose": "Search documents by keyword.",
                "domain": "research",
            },
            headers=_auth(OWNER_TOKEN),
        )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["blueprint_id"] == "scout"
    assert payload["agent_id"]
    # The creator is bound server-side to the authenticated caller.
    assert factory.get("scout", payload["version"]).creator == "danny"
    assert app.state.agent_registry.get(payload["agent_id"]) is not None


async def test_route_rejects_invalid_blueprint_id(app_and_state) -> None:  # type: ignore[no-untyped-def]
    app, _service, _factory = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/agent-requests",
            json={
                "blueprint_id": "bad id!",
                "name": "Scout",
                "purpose": "find things",
                "domain": "research",
            },
            headers=_auth(OWNER_TOKEN),
        )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Startup config: env authorizer + allowlist (fail-closed defaults)
# --------------------------------------------------------------------------- #


def test_env_config_builds_authorizer_and_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENCY_AGENT_REQUEST_ACTORS", "danny, ops ")
    monkeypatch.setenv("AGENCY_AGENT_REQUEST_DOMAINS", "Research, coding,")
    app = create_app()
    authorizer = app.state.agent_request_authorizer
    assert authorizer is not None
    assert authorizer("danny", AGENT_REQUEST_PERMISSION) is True
    assert authorizer("ops", AGENT_REQUEST_PERMISSION) is True
    assert authorizer("ghost", AGENT_REQUEST_PERMISSION) is False
    assert authorizer("danny", "agent.approve") is False
    assert app.state.agent_request_domains == ("research", "coding")


def test_env_config_defaults_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENCY_AGENT_REQUEST_ACTORS", raising=False)
    monkeypatch.delenv("AGENCY_AGENT_REQUEST_DOMAINS", raising=False)
    app = create_app()
    assert app.state.agent_request_authorizer is None
    assert app.state.agent_request_domains == ()


# --------------------------------------------------------------------------- #
# CLI: agency agent request
# --------------------------------------------------------------------------- #


def _cli_main() -> Any:
    """The ``agency.cli.main`` module.

    Resolved via importlib: the package ``__init__`` re-exports the ``main``
    function, so ``import agency.cli.main as m`` binds the function, not the
    module.
    """
    import importlib

    return importlib.import_module("agency.cli.main")


class _RecordingStubClient:
    """Minimal sync stand-in for ``httpx.Client`` (ASGITransport is async-only)."""

    def __init__(
        self,
        response: httpx.Response,
        requests: list[dict[str, Any]],
        headers: dict[str, str] | None = None,
    ) -> None:
        self._response = response
        self._requests = requests
        self._headers = headers or {}

    def post(self, path: str, json: Any = None) -> httpx.Response:
        self._requests.append({"path": path, "json": json, "headers": self._headers})
        return self._response

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def test_cli_agent_request_posts_to_route(app_and_state, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cli_main = _cli_main()
    _app, _service, _factory = app_and_state
    requests: list[dict[str, Any]] = []
    response = httpx.Response(
        201,
        json={"blueprint_id": "scout", "version": "1.0.0", "agent_id": "agent-1", "note": "ok"},
        request=httpx.Request("POST", "http://test/v1/agent-requests"),
    )
    monkeypatch.setattr(
        cli_main,
        "_client",
        lambda base, timeout=15.0, headers=None: _RecordingStubClient(response, requests, headers),
    )
    result = CliRunner().invoke(
        cli_main.app,
        [
            "agent",
            "request",
            "--blueprint-id",
            "scout",
            "--name",
            "Scout",
            "--purpose",
            "Search documents by keyword.",
            "--domain",
            "research",
            "--token",
            OWNER_TOKEN,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["blueprint_id"] == "scout"
    assert payload["agent_id"]
    assert requests[0]["path"] == "/v1/agent-requests"
    assert requests[0]["json"] == {
        "blueprint_id": "scout",
        "name": "Scout",
        "purpose": "Search documents by keyword.",
        "domain": "research",
    }
    assert requests[0]["headers"] == {"Authorization": f"Bearer {OWNER_TOKEN}"}


def test_cli_agent_request_denied_exits_nonzero(app_and_state, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cli_main = _cli_main()
    _app, _service, _factory = app_and_state
    requests: list[dict[str, Any]] = []
    response = httpx.Response(
        403,
        json={"detail": "actor 'agent-coder-1' denied for agent.request."},
        request=httpx.Request("POST", "http://test/v1/agent-requests"),
    )
    monkeypatch.setattr(
        cli_main,
        "_client",
        lambda base, timeout=15.0, headers=None: _RecordingStubClient(response, requests, headers),
    )
    result = CliRunner().invoke(
        cli_main.app,
        [
            "agent",
            "request",
            "--blueprint-id",
            "scout",
            "--name",
            "Scout",
            "--purpose",
            "Search documents by keyword.",
            "--token",
            AGENT1_TOKEN,
        ],
    )
    assert result.exit_code == 1
    assert "403" in result.output


# --------------------------------------------------------------------------- #
# Gap 14: centralized bootstrap applied by every entry point
# --------------------------------------------------------------------------- #


@pytest.fixture
async def _isolated_lattice(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from agency.lattice import factory as lattice_factory
    from agency.lattice.models import LatticeConfig

    await lattice_factory.reset_lattice()
    config = LatticeConfig(sqlite_path=str(tmp_path / "lattice.db"))
    monkeypatch.setattr(lattice_factory, "get_config", lambda: config)
    # The orchestrator's own memory store defaults to a cwd-relative file;
    # keep it in-memory so the chdir(tmp_path) isolation stays hermetic.
    import agency.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "_DEFAULT_MEMORY_DB", ":memory:")
    yield
    await lattice_factory.reset_lattice()


async def test_api_lifespan_uses_centralized_bootstrap_and_builds_request_service(
    tmp_path,
    monkeypatch,
    _isolated_lattice,  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENCY_TASKS_DB", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("AGENCY_MEMORY_DB", ":memory:")
    monkeypatch.setenv("AGENCY_AGENT_REQUEST_ACTORS", "danny")
    monkeypatch.setenv("AGENCY_AGENT_REQUEST_DOMAINS", "research")
    monkeypatch.setenv("AGENCY_FACTORY_DB", str(tmp_path / "factory.db"))
    monkeypatch.setenv("AGENCY_PROMPTS_DB", str(tmp_path / "prompts.db"))
    monkeypatch.setenv("AGENCY_SKILL_DB_PATH", str(tmp_path / "skills.db"))

    calls: list[Any] = []

    def spy(registry: Any, **kwargs: Any) -> Any:
        calls.append(registry)
        return bootstrap_agents(registry, **kwargs)

    monkeypatch.setattr(server_module, "bootstrap_agents", spy)
    app = server_module.create_app()
    async with server_module.lifespan(app):
        assert app.state.requested_agent_service is not None
        assert app.state.agent_registry in calls
    assert app.state.requested_agent_service is None  # lifespan cleanup resets it


async def test_api_lifespan_skips_request_service_without_allowlist(
    tmp_path,
    monkeypatch,
    _isolated_lattice,  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENCY_TASKS_DB", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("AGENCY_MEMORY_DB", ":memory:")
    monkeypatch.delenv("AGENCY_AGENT_REQUEST_DOMAINS", raising=False)
    app = server_module.create_app()
    async with server_module.lifespan(app):
        assert app.state.requested_agent_service is None


# Note: the orchestrator also has a clean startup seam (``start()`` ->
# ``_start_locked()``) where ``bootstrap_agents(orch._identity_registry)``
# verifies green in isolation, but it intentionally keeps its identity
# registry empty until agents are registered (tests/test_orchestrator.py
# codifies that invariant), so the wiring is left to operators — see the
# gap-14 report notes.

