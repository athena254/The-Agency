"""Agency FastAPI server — task, agent, memory, evidence, risk and bridge APIs.

Endpoint inventory (all JSON)
-----------------------------
POST /v1/tasks                    Create a task.
GET  /v1/tasks                    List tasks (``?status=&limit=``).
GET  /v1/tasks/{id}               Fetch one task.
GET  /v1/agents                   List agents.
POST /v1/agents                   Register an agent.
GET  /v1/memory/search            Full-text memory search (``?q=&agent_id=&limit=``).
GET  /v1/evidence                 List findings with filters.
GET  /v1/risk/{finding_id}        Risk vector + category for a finding.
POST /v1/bridges/{name}/execute   Execute a task via the named bridge.
GET  /v1/health                   Liveness probe.

State
-----
Shared services live on ``app.state`` and are built in the ``lifespan``
handler so routers stay stateless and unit-testable without a database:

* ``task_manager`` — :class:`agency.kernel.tasks.TaskManager`
* ``agent_registry`` — :class:`agency.kernel.registry.AgentRegistry`
* ``evidence_store`` — :class:`agency.evidence.store.store.EvidenceStore`
* ``risk_engine`` — :class:`agency.risk.engine.engine.RiskEngine`
* ``memory_store`` / ``memory_retrieval`` — SMS persistence + search
* ``bridge_coordinator`` — :class:`agency.bridges.coordinator.ExternalCoordinator`

Run with::

    uvicorn agency.api.server:app --host 127.0.0.1 --port 8000
    # or
    agency start
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agency import __version__
from agency.agents.coding_bootstrap import bootstrap_coding_agent
from agency.agents.registry import AgentRegistry as RuntimeAgentRegistry
from agency.api.auth import TokenRegistry
from agency.api.authority import require_peer_authority
from agency.api.routers import agents as agents_router
from agency.api.routers import bridges as bridges_router
from agency.api.routers import evidence as evidence_router
from agency.api.routers import governance as governance_router
from agency.api.routers import memory as memory_router
from agency.api.routers import tasks as tasks_router
from agency.autonomy.blacksmith import activate_blacksmith
from agency.bridges.coordinator import ExternalCoordinator
from agency.evidence.store.store import EvidenceStore
from agency.factory.service import AgentFactory
from agency.kernel.registry import AgentRegistry
from agency.kernel.task_store import TaskStore
from agency.kernel.tasks import TaskManager
from agency.lattice import LatticeLease, acquire_lattice
from agency.memory.factory import build_memory_backend
from agency.memory.sms.lifecycle import TieredMemoryEngine
from agency.memory.sms.retrieval import RetrievalEngine
from agency.memory.sms.store import MemoryStore
from agency.orchestrator import AgencyOrchestrator
from agency.risk.engine.engine import RiskEngine
from agency.risk.engine.models import RiskCategory, RiskModel

log = structlog.get_logger(__name__)

# Paths served to authenticated callers (see agency.api.auth). The middleware
# below refuses to route them when no token file is configured (fail closed);
# fine-grained auth + scopes are enforced by the router dependencies.
ENABLED_AUTHED_PREFIXES = ("/v1/tasks",)


# --------------------------------------------------------------------------- #
# Response models (Pydantic v2, kernel-anchored where applicable)
# --------------------------------------------------------------------------- #


class HealthResponse(BaseModel):
    """Payload for ``GET /v1/health``."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="ok")
    version: str = Field(default=__version__)
    components: dict[str, bool] = Field(default_factory=dict)


class RiskReportResponse(BaseModel):
    """Payload for ``GET /v1/risk/{finding_id}``."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    risk: RiskModel
    category: RiskCategory
    evidence_count: int = Field(default=0)


# --------------------------------------------------------------------------- #
# Application factory
# --------------------------------------------------------------------------- #


def _database_path(env_var: str, default: str) -> str:
    return os.environ.get(env_var, default)


async def _finish_cleanup(task: asyncio.Task[BaseException | None]) -> None:
    """Keep cleanup owned and awaited even if its caller is cancelled again."""
    cancelled: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = exc
    error = task.result()
    if cancelled is not None:
        raise cancelled
    if error is not None:
        raise error


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Own one local Lattice lease and roll back every allocated store."""
    evidence_store: EvidenceStore | None = None
    memory_store: MemoryStore | None = None
    memory_lifecycle: TieredMemoryEngine | None = None
    task_store: TaskStore | None = None
    orchestrator: AgencyOrchestrator | None = None
    lease: LatticeLease | None = None
    primary: BaseException | None = None
    state_names = (
        "task_store",
        "task_manager",
        "agent_registry",
        "runtime_registry",
        "coding_agent",
        "risk_engine",
        "bridge_coordinator",
        "evidence_store",
        "memory_store",
        "memory_retrieval",
        "lattice",
        "peer_authority",
        "orchestrator",
        "peer_membership",
        "grant_ledger",
        "grant_bft_time_ms",
    )
    for name in state_names:
        setattr(application.state, name, None)
    try:
        task_store = TaskStore(_database_path("AGENCY_TASKS_DB", "data/tasks.db"))
        await task_store.initialize()
        application.state.task_store = task_store
        if os.environ.get("AGENCY_TASKS_RECOVER", "").strip() == "1":
            requeued = await task_store.requeue_running(note="requeued after restart")
            if requeued:
                log.info("api.tasks_recovered", requeued=requeued)

        application.state.task_manager = TaskManager(task_store)
        application.state.agent_registry = AgentRegistry()
        application.state.runtime_registry = RuntimeAgentRegistry()
        application.state.risk_engine = RiskEngine()
        application.state.bridge_coordinator = ExternalCoordinator()

        evidence_store = EvidenceStore(_database_path("AGENCY_EVIDENCE_DB", ":memory:"))
        await evidence_store.initialize()
        application.state.evidence_store = evidence_store

        memory_store = build_memory_backend(_database_path("AGENCY_MEMORY_DB", ":memory:"))
        await memory_store.initialize()
        application.state.memory_store = memory_store
        memory_lifecycle = TieredMemoryEngine(memory_store)
        memory_lifecycle.start_periodic()
        application.state.memory_retrieval = RetrievalEngine(memory_store)

        # Record ownership before exposing the borrowed handle or running hooks.
        lease = await acquire_lattice()
        application.state.lattice = lease.lattice

        # Full execution stack: the dispatcher inside the orchestrator drains
        # the shared task queue (same file as the butler server), so tasks
        # submitted over this API actually execute in parallel.
        orchestrator = AgencyOrchestrator(task_manager=application.state.task_manager)
        application.state.orchestrator = orchestrator
        await orchestrator.start()
        await orchestrator.start_dispatcher()

        kernel_registry = getattr(application.state, "agent_registry", None)
        if kernel_registry is None:
            kernel_registry = getattr(application.state, "kernel_registry", None)
        runtime_registry = getattr(application.state, "runtime_registry", None)
        if kernel_registry is not None and runtime_registry is not None:
            try:
                application.state.coding_agent = bootstrap_coding_agent(
                    kernel_registry, runtime_registry
                )
            except BaseException as exc:  # noqa: BLE001 - startup must not crash
                application.state.coding_agent = None
                log.warning(
                    "api.startup.coding_agent_failed",
                    error_type=type(exc).__name__,
                )
            try:
                from agency.factory.prompts import PromptRegistry
                from agency.factory.prompts_builtin import ensure_builtin_prompts

                blacksmith_prompts = PromptRegistry(":memory:")
                ensure_builtin_prompts(blacksmith_prompts)
                activate_blacksmith(
                    AgentFactory(
                        ":memory:",
                        kernel_registry=kernel_registry,
                        runtime_registry=runtime_registry,
                        allowed_capabilities={"forge"},
                        is_prompt_published=blacksmith_prompts.is_published,
                        prompt_resolver=blacksmith_prompts,
                    )
                )
            except BaseException as exc:  # noqa: BLE001 - startup must not crash
                log.warning(
                    "api.startup.blacksmith_failed",
                    error_type=type(exc).__name__,
                )
            try:
                from agency.factory.github_skills import grant_manifest_capabilities

<<<<<<< HEAD
                grant_manifest_capabilities(kernel_registry)
            except BaseException as exc:  # noqa: BLE001 - startup must not crash
                log.warning(
                    "api.startup.external_skill_attachments_failed",
                    error_type=type(exc).__name__,
                )
            try:
                from agency.agents.bootstrap import ensure_installed_agents
                from agency.factory.manager import AgentManager

                ensure_installed_agents(AgentManager(), kernel_registry)
            except BaseException as exc:  # noqa: BLE001 - startup must not crash
                log.warning(
                    "api.startup.installed_agents_failed",
                    error_type=type(exc).__name__,
                )

        # Wire peer authority from a pinned genesis if available (opens the
        # ballot route and the grant-verifying HTTP dependency).
        try:
            from agency.consensus.genesis import load_genesis
            from agency.consensus.grants import GrantLedger
            from agency.peers.authority import PeerAuthority, PeerRegistry

            genesis_path = os.environ.get("AGENCY_GENESIS_PATH", "data/consensus/genesis.json")
            if os.path.exists(genesis_path):
                membership = load_genesis(genesis_path)
                registry = PeerRegistry()
                for member in membership.members:
                    registry.register_public_key(member.peer_id, member.public_key)
                application.state.peer_authority = PeerAuthority(membership, registry)
                application.state.peer_membership = membership
                application.state.grant_ledger = GrantLedger(membership=membership)
                application.state.grant_bft_time_ms = int(
                    os.environ.get("AGENCY_GRANT_BFT_TIME_MS", "1000")
                )
                log.info("api.startup.peer_authority_loaded", path=genesis_path)
        except BaseException as exc:  # noqa: BLE001 - startup must not crash
            application.state.peer_authority = None
            log.warning("api.startup.peer_authority_failed", error_type=type(exc).__name__)

        log.info("api.startup", version=__version__)
        yield
    except BaseException as exc:
        primary = exc
        raise
    finally:
        for name in state_names:
            setattr(application.state, name, None)

        async def cleanup() -> BaseException | None:
            error: BaseException | None = None
            closers = []
            if orchestrator is not None:
                closers.append(orchestrator.stop)
            if lease is not None:
                closers.append(lease.release)
            if evidence_store is not None:
                closers.append(evidence_store.close)
            if memory_lifecycle is not None:
                closers.append(memory_lifecycle.stop_periodic)
            if memory_store is not None:
                closers.append(memory_store.close)
            if task_store is not None:
                closers.append(task_store.close)
            for close in closers:
                try:
                    await close()
                except BaseException as exc:  # noqa: BLE001 - finish independent cleanup
                    if error is None:
                        error = exc
                    with suppress(BaseException):
                        log.warning("api.shutdown.cleanup_failed", error_type=type(exc).__name__)
            if error is None:
                with suppress(BaseException):
                    log.info("api.shutdown")
            return error

        try:
            await _finish_cleanup(asyncio.create_task(cleanup()))
        except BaseException:
            if primary is None:
                raise


def create_app() -> FastAPI:
    """Build and configure the Agency FastAPI application."""
    application = FastAPI(
        title="Agency API",
        version=__version__,
        description="Security-first autonomous agent system.",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Owner-issued bearer tokens (agency.api.auth). ``None`` keeps every
    # authed route at 503 — the default-deny posture is unchanged.
    application.state.token_registry = TokenRegistry.from_env()

    @application.middleware("http")
    async def deny_unverified_authority(request: Request, call_next: Any) -> Any:
        # The ASGI path can include root_path when mounted behind a prefix.
        # Public: GET health. Enabled + authenticated: ENABLED_AUTHED_PREFIXES
        # (route dependencies authenticate; the middleware refuses to route
        # them at all when no token registry is configured). Everything else
        # fails closed exactly as before.
        path = request.scope["path"]
        root = request.scope.get("root_path", "")
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        # Signed ballots carry their own authority (Ed25519 envelope verified
        # by PeerAuthority); they must reach the router to be verified.
        from agency.api.authority import _is_read_scope, _writes_open

        is_ballot = path == "/v1/governance/ballot"
        is_health = request.method == "GET" and path == "/v1/health"
        scope = f"{request.method} {path}"
        is_scoped_read = request.method == "GET" and _is_read_scope(scope)
        is_scoped_write = _writes_open() and path.startswith("/v1/")
        if is_health or is_ballot or is_scoped_read or is_scoped_write:
            return await call_next(request)
        if any(
            path == prefix or path.startswith(prefix + "/")
            for prefix in ENABLED_AUTHED_PREFIXES
        ) and getattr(application.state, "token_registry", None) is not None:
            return await call_next(request)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Peer authorization is unavailable."},
        )

    application.include_router(tasks_router.router, prefix="/v1/tasks")
    application.include_router(agents_router.router, prefix="/v1/agents")
    application.include_router(memory_router.router, prefix="/v1/memory")
    application.include_router(evidence_router.router, prefix="/v1/evidence")
    application.include_router(bridges_router.router, prefix="/v1/bridges")
    application.include_router(governance_router.router)
    application.include_router(governance_router.ballot_router)

    @application.get(
        "/v1/health", response_model=HealthResponse, tags=["ops"], summary="Health check"
    )
    async def health(request: Request) -> HealthResponse:
        """Liveness probe plus per-component readiness."""
        memory_store = getattr(request.app.state, "memory_store", None)
        components: dict[str, bool] = {
            "task_manager": getattr(request.app.state, "task_manager", None) is not None,
            "agent_registry": getattr(request.app.state, "agent_registry", None) is not None,
            "evidence_store": getattr(request.app.state, "evidence_store", None) is not None,
            "memory": await memory_store.check_ready() if memory_store is not None else False,
            "risk_engine": getattr(request.app.state, "risk_engine", None) is not None,
            "bridges": getattr(request.app.state, "bridge_coordinator", None) is not None,
        }
        overall = all(components.values())
        return HealthResponse(status="ok" if overall else "degraded", components=components)

    @application.get(
        "/v1/risk/{finding_id}",
        response_model=RiskReportResponse,
        tags=["risk"],
        summary="Get risk assessment",
        dependencies=[Depends(require_peer_authority)],
    )
    async def get_risk(finding_id: str, request: Request) -> RiskReportResponse:
        """Compute the risk vector and category for a finding."""
        store: EvidenceStore | None = getattr(request.app.state, "evidence_store", None)
        engine: RiskEngine | None = getattr(request.app.state, "risk_engine", None)
        if store is None or engine is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Risk subsystem is not initialised.",
            )
        finding = await store.get_finding(finding_id)
        if finding is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown finding {finding_id!r}",
            )
        evidence = await store.evidence_for_finding(finding_id)
        risk, category = engine.assess(finding, evidence)
        log.info("api.risk.assessed", finding_id=finding_id, category=category.value)
        return RiskReportResponse(
            finding_id=finding_id, risk=risk, category=category, evidence_count=len(evidence)
        )

    @application.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "service": "agency",
            "version": __version__,
            "docs": "/docs",
            "health": "/v1/health",
        }

    @application.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("api.unhandled", path=str(request.url.path))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error."},
        )

    return application


app: FastAPI = create_app()

__all__ = ["HealthResponse", "RiskReportResponse", "app", "create_app", "lifespan"]
