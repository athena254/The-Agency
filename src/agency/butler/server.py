"""Butler HTTP server — FastAPI front-end over :class:`ButlerService`.

Endpoints
---------
POST /v1/message   Accept a message, route to an agent, return the reply.
GET  /v1/agents    List registered agents.
GET  /v1/health    Liveness + readiness probe.

Run with::

    uvicorn agency.butler.server:app --host 127.0.0.1 --port 8001
    # or
    butler start
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agency import __version__
from agency.butler.config import ButlerConfig
from agency.butler.service import ButlerService

log = structlog.get_logger(__name__)


# --------------------------------------------------------------------------- #
# Request / response models (Pydantic v2)
# --------------------------------------------------------------------------- #


class MessageRequest(BaseModel):
    """Payload for ``POST /v1/message``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, description="User message text.")
    sender: str = Field(default="anonymous", min_length=1, description="Sender identity.")
    context: dict[str, Any] = Field(default_factory=dict, description="Extra routing context.")


class MessageResponse(BaseModel):
    """Reply for ``POST /v1/message``."""

    model_config = ConfigDict(extra="forbid")

    response: str
    agent_id: str
    agent_name: str
    domain: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentResponse(BaseModel):
    """Public agent descriptor for ``GET /v1/agents``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    domain: str
    trust_level: str
    capabilities: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """Payload for ``GET /v1/health``."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="ok")
    version: str = Field(default=__version__)
    butler: str = Field(default="running")
    agents: int = Field(default=0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# --------------------------------------------------------------------------- #
# Application factory
# --------------------------------------------------------------------------- #


def _get_service(request: Request) -> ButlerService:
    service = getattr(request.app.state, "butler", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Butler service is not initialised.",
        )
    return service


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Build the ButlerService on startup; stop it on shutdown."""
    config = getattr(application.state, "butler_config", None) or ButlerConfig()
    service = getattr(application.state, "butler", None) or ButlerService(config=config)
    application.state.butler = service
    try:
        await service.start()
    except Exception:
        log.exception("butler.startup_failed")
        raise
    log.info("butler.api_startup", version=__version__)
    try:
        yield
    finally:
        try:
            await service.stop()
        except Exception:
            log.exception("butler.shutdown_failed")
        log.info("butler.api_shutdown")


def create_app(config: ButlerConfig | None = None) -> FastAPI:
    """Build the Butler FastAPI application."""
    application = FastAPI(
        title="Butler API",
        version=__version__,
        description="Conversational gateway for The Agency.",
        lifespan=lifespan,
    )
    application.state.butler_config = config or ButlerConfig()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.post(
        "/v1/message", response_model=MessageResponse, tags=["butler"], summary="Send a message"
    )
    async def post_message(payload: MessageRequest, request: Request) -> MessageResponse:
        service = _get_service(request)
        if len(payload.message) > service.config.max_message_length:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"message exceeds {service.config.max_message_length} chars.",
            )
        agent = await service.route(payload.message, {**payload.context, "sender": payload.sender})
        response = await service.handle_message(
            payload.message, payload.sender, dict(payload.context)
        )
        return MessageResponse(
            response=response, agent_id=agent.id, agent_name=agent.name, domain=agent.domain
        )

    @application.get(
        "/v1/agents", response_model=list[AgentResponse], tags=["butler"], summary="List agents"
    )
    async def list_agents(request: Request) -> list[AgentResponse]:
        service = _get_service(request)
        return [
            AgentResponse(
                id=agent.id,
                name=agent.name,
                domain=agent.domain,
                trust_level=str(agent.trust_level.value),
                capabilities=[c.name for c in agent.capabilities],
            )
            for agent in service.router.list_agents()
        ]

    @application.get(
        "/v1/health", response_model=HealthResponse, tags=["ops"], summary="Health check"
    )
    async def health(request: Request) -> HealthResponse:
        service = getattr(request.app.state, "butler", None)
        count = len(service.router.list_agents()) if service is not None else 0
        running = service is not None and getattr(service, "_started", False)
        return HealthResponse(
            status="ok" if running else "starting",
            butler="running" if running else "starting",
            agents=count,
        )

    @application.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "service": "butler",
            "version": __version__,
            "docs": "/docs",
            "health": "/v1/health",
        }

    @application.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, HTTPException):
            raise exc
        log.exception("butler.api_unhandled", path=str(request.url.path))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error."},
        )

    return application


app: FastAPI = create_app()

__all__ = [
    "AgentResponse",
    "HealthResponse",
    "MessageRequest",
    "MessageResponse",
    "app",
    "create_app",
    "lifespan",
]
