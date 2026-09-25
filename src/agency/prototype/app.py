"""Local prototype API — same-origin FastAPI surface over :class:`ButlerService`.

This is the *local* prototype entry point. It binds loopback only when run
with the documented command and deliberately exposes a much smaller, safer
surface than :mod:`agency.butler.server`:

``GET  /``            Static UI shell (once the UI lane adds assets).
``GET  /assets/*``    Static UI assets from ``prototype/static``.
``GET  /api/health``  ``{status, butler, agents}``.
``GET  /api/agents``  ``{agents: [{id, name, domain}]}``.
``GET  /api/session`` ``{csrf_token}`` for accepted local requests.
``POST /api/chat``    ``{message}`` -> ``{response}`` for the local sender.

Every ``/api`` request must carry a loopback ``Host``; ``POST /api/chat``
additionally requires a non-foreign ``Origin``, the per-process CSRF header
and a JSON content type. There is no CORS middleware, no caller-supplied
``sender``/``context``/tool grant, and no agent creation or thread access.
Failures return an explicit non-2xx ``{"error": ...}`` without echoing the
inbound message, model output, or raw exception text.
"""

from __future__ import annotations

import ipaddress
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from agency import __version__
from agency.butler.service import ButlerService
from agency.orchestrator import AgencyOrchestrator

log = structlog.get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
CSRF_HEADER = "X-Prototype-CSRF"
LOCAL_SENDER = "local:prototype"
_DEFAULT_MAX_MESSAGE_LENGTH = 8000
_LOOPBACK_HOSTNAMES = frozenset({"localhost"})


# --------------------------------------------------------------------------- #
# Local-only request guards
# --------------------------------------------------------------------------- #


def _loopback_hostname(host_header: str) -> str | None:
    """Return the hostname from a Host/URL value when it is loopback, else None."""
    raw = (host_header or "").strip()
    if not raw:
        return None
    if raw.startswith("["):  # bracketed IPv6, e.g. "[::1]:8000"
        end = raw.find("]")
        if end == -1:
            return None
        candidate = raw[1:end]
    elif raw.count(":") == 1:  # host:port
        candidate = raw.rsplit(":", 1)[0]
    else:
        candidate = raw
    if candidate.lower() in _LOOPBACK_HOSTNAMES:
        return candidate.lower()
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return str(address) if address.is_loopback else None


def _is_loopback_host(host_header: str) -> bool:
    """Whether a ``Host`` header names the local machine."""
    return _loopback_hostname(host_header) is not None


def _is_local_origin(origin: str) -> bool:
    """Whether an ``Origin`` header is an http(s) loopback origin."""
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    return _is_loopback_host(parts.hostname or "")


async def _require_local_host(request: Request) -> None:
    """Reject DNS-rebinding-style requests with a non-loopback ``Host``."""
    if not _is_loopback_host(request.headers.get("host", "")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API accepts loopback requests only.",
        )


async def _guard_chat_request(request: Request) -> None:
    """Validate Host, Origin, CSRF and content type before the Butler call."""
    await _require_local_host(request)

    origin = request.headers.get("origin")
    if origin is not None and not _is_local_origin(origin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request origin is not allowed.",
        )

    expected = getattr(request.app.state, "csrf_token", "")
    supplied = request.headers.get(CSRF_HEADER) or ""
    if (
        not isinstance(expected, str)
        or not expected
        or not secrets.compare_digest(supplied, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid CSRF token.",
        )

    content_type = request.headers.get("content-type") or ""
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Content-Type must be application/json.",
        )


def _get_butler(request: Request) -> Any:
    """Return the running Butler or fail with a sanitized error."""
    service = getattr(request.app.state, "butler", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Butler service is unavailable.",
        )
    return service


def _agent_view(agent: Any) -> AgentView:
    """Project a kernel Agent onto the public, minimal UI shape."""
    return AgentView(id=str(agent.id), name=str(agent.name), domain=str(agent.domain))


async def _collect_agents(service: Any) -> list[AgentView]:
    """Read the real roster from the Butler router, else the orchestrator."""
    views: dict[str, AgentView] = {}
    router = getattr(service, "router", None)
    if router is not None:
        for agent in router.list_agents():
            views[str(agent.id)] = _agent_view(agent)
    if not views:
        orchestrator = getattr(service, "orchestrator", None)
        if orchestrator is not None:
            try:
                listed = await orchestrator.list_agents()
            except Exception:  # noqa: BLE001 — roster read is best-effort.
                listed = []
            for agent in listed:
                views[str(agent.id)] = _agent_view(agent)
    return list(views.values())


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #


class ChatRequest(BaseModel):
    """Payload for ``POST /api/chat``; extra fields are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, description="User message text.")


class ChatResponse(BaseModel):
    """Reply for ``POST /api/chat``."""

    model_config = ConfigDict(extra="forbid")

    response: str


class AgentView(BaseModel):
    """Public agent descriptor for ``GET /api/agents``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    domain: str


class AgentsResponse(BaseModel):
    """Payload for ``GET /api/agents``."""

    model_config = ConfigDict(extra="forbid")

    agents: list[AgentView]


class HealthResponse(BaseModel):
    """Payload for ``GET /api/health``."""

    model_config = ConfigDict(extra="forbid")

    status: str
    butler: str
    agents: int


class SessionResponse(BaseModel):
    """Payload for ``GET /api/session``."""

    model_config = ConfigDict(extra="forbid")

    csrf_token: str


# --------------------------------------------------------------------------- #
# Lifespan + application factory
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Start exactly one Butler (injected or freshly built) and stop it cleanly."""
    service: ButlerService | None = getattr(application.state, "butler", None)
    if service is None:
        service = ButlerService(orchestrator=AgencyOrchestrator())
        application.state.butler = service
    await service.start()
    application.state.butler_started = True
    log.info("prototype.api_startup", version=__version__)
    try:
        yield
    finally:
        try:
            await service.stop()
        finally:
            application.state.butler_started = False
        log.info("prototype.api_shutdown")


def create_app(butler: ButlerService | None = None) -> FastAPI:
    """Build the local prototype FastAPI application.

    ``butler`` may be injected (tests, or a pre-built service). When ``None``,
    startup builds the default real :class:`ButlerService`.
    """
    application = FastAPI(
        title="Agency Prototype API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.butler = butler
    application.state.butler_started = False
    application.state.csrf_token = secrets.token_urlsafe(32)

    @application.exception_handler(HTTPException)
    async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, HTTPException)
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})

    @application.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"error": "Invalid request payload."},
        )

    @application.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("prototype.api_unhandled", path=str(request.url.path))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Internal server error."},
        )

    @application.get("/api/health", response_model=HealthResponse, tags=["prototype"])
    async def health(
        request: Request, _host: None = Depends(_require_local_host)
    ) -> HealthResponse:
        service = getattr(request.app.state, "butler", None)
        running = bool(getattr(request.app.state, "butler_started", False))
        agents = await _collect_agents(service)
        return HealthResponse(
            status="ok" if running else "starting",
            butler="running" if running else "starting",
            agents=len(agents),
        )

    @application.get("/api/agents", response_model=AgentsResponse, tags=["prototype"])
    async def list_agents(
        request: Request, _host: None = Depends(_require_local_host)
    ) -> AgentsResponse:
        service = getattr(request.app.state, "butler", None)
        return AgentsResponse(agents=await _collect_agents(service))

    @application.get("/api/session", response_model=SessionResponse, tags=["prototype"])
    async def session(
        request: Request, _host: None = Depends(_require_local_host)
    ) -> SessionResponse:
        return SessionResponse(csrf_token=str(request.app.state.csrf_token))

    @application.post("/api/chat", response_model=ChatResponse, tags=["prototype"])
    async def chat(
        payload: ChatRequest,
        request: Request,
        _guard: None = Depends(_guard_chat_request),
    ) -> ChatResponse:
        service = _get_butler(request)
        config = getattr(service, "config", None)
        limit = int(getattr(config, "max_message_length", _DEFAULT_MAX_MESSAGE_LENGTH))
        if len(payload.message) > limit:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Message exceeds the maximum allowed length.",
            )
        response = await service.handle_message(
            payload.message, LOCAL_SENDER, {}, memory_enabled=False
        )
        return ChatResponse(response=str(response))

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        index_file = STATIC_DIR / "index.html"
        if not index_file.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Prototype UI assets are not installed.",
            )
        return FileResponse(index_file)

    if STATIC_DIR.is_dir():
        application.mount(
            "/assets",
            StaticFiles(directory=str(STATIC_DIR), check_dir=False),
            name="assets",
        )

    return application


__all__ = [
    "AgentView",
    "AgentsResponse",
    "ChatRequest",
    "ChatResponse",
    "HealthResponse",
    "SessionResponse",
    "create_app",
    "lifespan",
]
