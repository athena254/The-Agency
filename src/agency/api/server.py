"""Agency FastAPI server — task, agent, memory, evidence, risk and bridge APIs.

Endpoint inventory (all JSON)
-----------------------------
POST /v1/tasks                    Create a task.
GET  /v1/tasks                    List tasks (``?status=&limit=``).
GET  /v1/tasks/{id}               Fetch one task.
GET  /v1/agents                   List agents.
POST /v1/agents                   Register an agent.
POST /v1/agent-requests           Request a governed agent from a purpose.
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
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agency import __version__
from agency.agents.bootstrap import (
    AGENT_REQUEST_PERMISSION,
    bootstrap_agents,
    request_agent_authorized,
)
from agency.agents.coding_bootstrap import bootstrap_coding_agent
from agency.agents.registry import AgentRegistry as RuntimeAgentRegistry
from agency.api.auth import Principal, TokenRegistry, principal_dependency
from agency.api.authority import (
    attach_fencing_commit,
    attach_grant_authority,
    detach_grant_authority,
    require_peer_authority,
)
from agency.api.routers import agents as agents_router
from agency.api.routers import bridges as bridges_router
from agency.api.routers import evidence as evidence_router
from agency.api.routers import governance as governance_router
from agency.api.routers import memory as memory_router
from agency.api.routers import tasks as tasks_router
from agency.autonomy.blacksmith import activate_blacksmith
from agency.bridges.coordinator import ExternalCoordinator
from agency.consensus.fencing_commit import GrantFencingCommit
from agency.evidence.store.store import EvidenceStore
from agency.factory.prompts import PromptRegistry
from agency.factory.requested_agent import RequestedAgentRequest, RequestedAgentService
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
from agency.skills.registry import SkillRegistry, SkillStatus

log = structlog.get_logger(__name__)

# Paths served to authenticated callers (see agency.api.auth). The middleware
# below refuses to route them when no token file is configured (fail closed);
# fine-grained auth + scopes are enforced by the router dependencies.
ENABLED_AUTHED_PREFIXES = ("/v1/tasks", "/v1/agent-requests")

# Requested-agent entrypoint policy (gaps.md #6). The authorizer mirrors the
# orchestrator's ``factory_self_improve_authorizer`` config-key pattern (an
# explicit callable; the env-representable form is the caller-id allowlist).
AGENCY_REQUEST_ACTORS_ENV = "AGENCY_AGENT_REQUEST_ACTORS"
AGENCY_REQUEST_DOMAINS_ENV = "AGENCY_AGENT_REQUEST_DOMAINS"
AGENCY_REQUEST_APPROVER_ENV = "AGENCY_AGENT_REQUEST_APPROVER"
DEFAULT_REQUEST_APPROVER = "factory-improvement"


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


class AgentRequestCreateRequest(BaseModel):
    """Payload for ``POST /v1/agent-requests`` (gaps.md #6)."""

    model_config = ConfigDict(extra="forbid")

    blueprint_id: str = Field(min_length=1, description="Blueprint id for the new governed agent.")
    name: str = Field(min_length=1, description="Human-readable agent name.")
    purpose: str = Field(min_length=1, description="Free-text purpose the agent serves.")
    domain: str = Field(
        default="general", description="Operational domain; must be in the allowlist."
    )


class AgentRequestResponse(BaseModel):
    """Payload for a successful ``POST /v1/agent-requests``."""

    model_config = ConfigDict(extra="forbid")

    blueprint_id: str
    version: str
    agent_id: str
    attached_skills: list[tuple[str, str]] = Field(default_factory=list)
    prompt_refs: list[tuple[str, str]] = Field(default_factory=list)
    note: str = ""


# --------------------------------------------------------------------------- #
# Application factory
# --------------------------------------------------------------------------- #


def _database_path(env_var: str, default: str) -> str:
    return os.environ.get(env_var, default)


def _env_agent_request_authorizer() -> Callable[[str, str], bool] | None:
    """Build the ``agent.request`` authorizer from the caller-id allowlist.

    Fail closed: an unset/blank ``AGENCY_AGENT_REQUEST_ACTORS`` returns
    ``None`` and every request is denied by the bootstrap adapter.
    """
    raw = os.environ.get(AGENCY_REQUEST_ACTORS_ENV, "")
    actors = frozenset(part.strip() for part in raw.split(",") if part.strip())
    if not actors:
        return None

    def authorizer(actor: str, permission: str) -> bool:
        return permission == AGENT_REQUEST_PERMISSION and actor in actors

    return authorizer


def _env_agent_request_domains() -> tuple[str, ...]:
    """Parse the domain allowlist; empty denies every request (fail closed)."""
    raw = os.environ.get(AGENCY_REQUEST_DOMAINS_ENV, "")
    return tuple(dict.fromkeys(part.strip().casefold() for part in raw.split(",") if part.strip()))


def _build_requested_agent_service(
    kernel_registry: Any, runtime_registry: Any
) -> RequestedAgentService:
    """Compose the requested-agent entrypoint service from the default stores."""
    prompts = PromptRegistry(_database_path("AGENCY_PROMPTS_DB", "data/prompts.db"))
    skills = SkillRegistry(_database_path("AGENCY_SKILL_DB_PATH", "data/skills.db"))
    approver = os.environ.get(AGENCY_REQUEST_APPROVER_ENV, "").strip() or DEFAULT_REQUEST_APPROVER
    factory = AgentFactory(
        _database_path("AGENCY_FACTORY_DB", "data/factory.db"),
        authorizer=lambda actor, permission: permission == "agent.approve" and actor == approver,
        kernel_registry=kernel_registry,
        runtime_registry=runtime_registry,
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
        is_skill_published=lambda skill_id, version: (
            skills.get(skill_id, version).status is SkillStatus.PUBLISHED
        ),
    )
    return RequestedAgentService(
        factory=factory, prompt_registry=prompts, skill_registry=skills, approver=approver
    )


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
    fencing_commit: GrantFencingCommit | None = None
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
        "requested_agent_service",
        "peer_membership",
        "grant_ledger",
        "grant_bft_time_ms",
        "fencing_commit",
    )
    for name in state_names:
        setattr(application.state, name, None)
    try:
        task_store = TaskStore(_database_path("AGENCY_TASKS_DB", "data/tasks.db"))
        await task_store.initialize()
        application.state.task_store = task_store
        if os.environ.get("AGENCY_TASKS_RECOVER", "").strip() == "1":
            # Lease-aware recovery: only claims whose lease expired (or
            # pre-lease rows) are requeued, so a second live process's
            # running tasks — leases still fresh via its dispatcher
            # heartbeats — are left alone. Safe across processes when every
            # claiming process runs a lease-aware build; see TaskStore
            # module docstring for the honest limits.
            requeued = await task_store.requeue_running(
                note="requeued after restart (lease expired)", expired_only=True
            )
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
        # submitted over this API actually execute in parallel. The
        # audit/evidence/memory paths are passed through so one process uses
        # one durable backend per store (gaps.md #19): api + butler share
        # rows when the same env vars point both servers at one file.
        orchestrator = AgencyOrchestrator(
            task_manager=application.state.task_manager,
            audit_db_path=_database_path("AGENCY_AUDIT_DB", ":memory:"),
            evidence_db_path=_database_path("AGENCY_EVIDENCE_DB", ":memory:"),
            memory_db_path=_database_path("AGENCY_MEMORY_DB", ":memory:"),
        )
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
                # Single centralized agent bootstrap (gaps.md #14): installed
                # agents + external-skill attachment manifest grants, shared
                # by every entry point instead of an API-only inline grant.
                bootstrap_agents(kernel_registry)
            except BaseException as exc:  # noqa: BLE001 - startup must not crash
                log.warning(
                    "api.startup.agent_bootstrap_failed",
                    error_type=type(exc).__name__,
                )
            # Requested-agent entrypoint service (gaps.md #6): built only when
            # the operator opted in via a non-empty domain allowlist; the
            # explicit authorizer + allowlist stay fail-closed per request.
            domains = getattr(application.state, "agent_request_domains", None) or ()
            if domains:
                try:
                    application.state.requested_agent_service = _build_requested_agent_service(
                        kernel_registry, application.state.runtime_registry
                    )
                except BaseException as exc:  # noqa: BLE001 - startup must not crash
                    log.warning(
                        "api.startup.agent_request_service_failed",
                        error_type=type(exc).__name__,
                    )

        # Wire peer authority from a pinned genesis if available (opens the
        # ballot route, the grant-verifying HTTP dependency and — gaps.md #18
        # route swap — the grant path on the intake routes).
        attach_grant_authority(application)
        # Resource-side fencing commit for grant-admitted tasks (gaps.md #18):
        # built when the pinned genesis grant ledger exists; the executor then
        # fences every consequential execution of a grant task on (grant
        # digest, task id). Without a ledger the grant intake path stays 503,
        # so no grant-admitted task can exist and nothing is fenced.
        fencing_commit = attach_fencing_commit(application, orchestrator)

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

            async def close_grant_authority() -> None:
                # Releases the durable grant store's SQLite connection (a
                # no-op for the in-memory ledger) and clears the state.
                detach_grant_authority(application)

            closers = []
            if orchestrator is not None:
                closers.append(orchestrator.stop)
            if fencing_commit is not None:
                closers.append(fencing_commit.close)
            if lease is not None:
                closers.append(lease.release)
            closers.append(close_grant_authority)
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
    # Requested-agent entrypoint policy (gaps.md #6), fail closed: an unset
    # authorizer denies every caller; an empty allowlist denies every domain.
    application.state.agent_request_authorizer = _env_agent_request_authorizer()
    application.state.agent_request_domains = _env_agent_request_domains()

    @application.middleware("http")
    async def deny_unverified_authority(request: Request, call_next: Any) -> Any:
        # The ASGI path can include root_path when mounted behind a prefix.
        # Public: GET health. Enabled + authenticated: ENABLED_AUTHED_PREFIXES
        # (route dependencies authenticate; the middleware refuses to route
        # them at all when no token registry is configured). A signed intake
        # grant (X-Agency-Grant, verified by the route dependency against the
        # pinned grant ledger) also reaches the enabled prefixes — the grant
        # path is a first-class alternative to bearer tokens, gaps.md #18.
        # Everything else fails closed exactly as before.
        path = request.scope["path"]
        root = request.scope.get("root_path", "")
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        # Signed ballots carry their own authority (Ed25519 envelope verified
        # by PeerAuthority); they must reach the router to be verified.
        from agency.api.authority import GRANT_HEADER, _is_read_scope, _writes_open

        is_ballot = path == "/v1/governance/ballot"
        is_health = request.method == "GET" and path == "/v1/health"
        scope = f"{request.method} {path}"
        is_scoped_read = request.method == "GET" and _is_read_scope(scope)
        is_scoped_write = _writes_open() and path.startswith("/v1/")
        if is_health or is_ballot or is_scoped_read or is_scoped_write:
            return await call_next(request)
        has_grant_intake = (
            getattr(application.state, "grant_ledger", None) is not None
            and request.headers.get(GRANT_HEADER) is not None
        )
        if any(
            path == prefix or path.startswith(prefix + "/") for prefix in ENABLED_AUTHED_PREFIXES
        ) and (getattr(application.state, "token_registry", None) is not None or has_grant_intake):
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

    @application.post(
        "/v1/agent-requests",
        response_model=AgentRequestResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["agents"],
        summary="Request a governed agent",
    )
    async def request_agent(
        payload: AgentRequestCreateRequest,
        request: Request,
        principal: Annotated[Principal, Depends(principal_dependency("tasks:write"))],
    ) -> AgentRequestResponse:
        """Manufacture a governed agent from a free-text request (gaps.md #6).

        The creator is bound server-side to the authenticated caller; the
        explicit ``agent.request`` authorizer and the domain allowlist are
        consulted fail-closed before the factory flow runs.
        """
        service = getattr(request.app.state, "requested_agent_service", None)
        if service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent-request entrypoint is not configured.",
            )
        authorizer = getattr(request.app.state, "agent_request_authorizer", None)
        domains = getattr(request.app.state, "agent_request_domains", None) or ()
        agent_request = RequestedAgentRequest(
            blueprint_id=payload.blueprint_id,
            name=payload.name,
            purpose=payload.purpose,
            creator=principal.caller_id,
            domain=payload.domain,
        )
        try:
            result = request_agent_authorized(
                service, agent_request, authorizer=authorizer, allowed_domains=domains
            )
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        log.info(
            "api.agent_request.created",
            agent_id=result.agent_id,
            blueprint_id=result.blueprint_id,
            caller=principal.caller_id,
            role=principal.role,
        )
        return AgentRequestResponse(
            blueprint_id=result.blueprint_id,
            version=result.version,
            agent_id=result.agent_id,
            attached_skills=list(result.attached_skills),
            prompt_refs=list(result.prompt_refs),
            note=result.note,
        )

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

__all__ = [
    "AgentRequestCreateRequest",
    "AgentRequestResponse",
    "HealthResponse",
    "RiskReportResponse",
    "app",
    "create_app",
    "lifespan",
]
