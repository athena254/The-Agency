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

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agency import __version__
from agency.api.routers import agents as agents_router
from agency.api.routers import bridges as bridges_router
from agency.api.routers import evidence as evidence_router
from agency.api.routers import governance as governance_router
from agency.api.routers import memory as memory_router
from agency.api.routers import tasks as tasks_router
from agency.bridges.coordinator import ExternalCoordinator
from agency.evidence.store.store import EvidenceStore
from agency.kernel.registry import AgentRegistry
from agency.kernel.tasks import TaskManager
from agency.lattice import get_lattice
from agency.memory.sms.retrieval import RetrievalEngine
from agency.memory.sms.store import MemoryStore
from agency.risk.engine.engine import RiskEngine
from agency.risk.engine.models import RiskCategory, RiskModel

log = structlog.get_logger(__name__)


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


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Create shared services on startup; close persistent stores on shutdown."""
    application.state.task_manager = TaskManager()
    application.state.agent_registry = AgentRegistry()
    application.state.risk_engine = RiskEngine()
    application.state.bridge_coordinator = ExternalCoordinator()

    evidence_store = EvidenceStore(_database_path("AGENCY_EVIDENCE_DB", ":memory:"))
    await evidence_store.initialize()
    application.state.evidence_store = evidence_store

    memory_store = MemoryStore(_database_path("AGENCY_MEMORY_DB", ":memory:"))
    await memory_store.initialize()
    application.state.memory_store = memory_store
    application.state.memory_retrieval = RetrievalEngine(memory_store)

    # Lattice
    lattice = await get_lattice()
    application.state.lattice = lattice

    log.info("api.startup", version=__version__)
    try:
        yield
    finally:
        try:
            await lattice.close()
        except Exception:
            log.exception("api.shutdown.lattice_close_failed")
        try:
            await evidence_store.close()
        except Exception:  # Shutdown must not raise.
            log.exception("api.shutdown.evidence_close_failed")
        try:
            await memory_store.close()
        except Exception:
            log.exception("api.shutdown.memory_close_failed")
        log.info("api.shutdown")


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

    application.include_router(tasks_router.router, prefix="/v1/tasks")
    application.include_router(agents_router.router, prefix="/v1/agents")
    application.include_router(memory_router.router, prefix="/v1/memory")
    application.include_router(evidence_router.router, prefix="/v1/evidence")
    application.include_router(bridges_router.router, prefix="/v1/bridges")
    application.include_router(governance_router.router)

    @application.get("/v1/health", response_model=HealthResponse, tags=["ops"], summary="Health check")
    async def health(request: Request) -> HealthResponse:
        """Liveness probe plus per-component readiness."""
        components: dict[str, bool] = {
            "task_manager": getattr(request.app.state, "task_manager", None) is not None,
            "agent_registry": getattr(request.app.state, "agent_registry", None) is not None,
            "evidence_store": getattr(request.app.state, "evidence_store", None) is not None,
            "memory": getattr(request.app.state, "memory_retrieval", None) is not None,
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
        return {"service": "agency", "version": __version__, "docs": "/docs", "health": "/v1/health"}

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
