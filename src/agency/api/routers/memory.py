"""Memory router — full-text search over the Sovereign Mind System.

Mounted by :mod:`agency.api.server` at ``/v1/memory``.

Endpoints
---------
GET /v1/memory/search   Semantic (FTS5) search, optionally scoped to an agent.
"""

from __future__ import annotations

from typing import cast

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from agency.memory.sms.models import MemoryItem
from agency.memory.sms.retrieval import RetrievalEngine

log = structlog.get_logger(__name__)

router = APIRouter(tags=["memory"])


class MemorySearchResponse(BaseModel):
    """Envelope for ``GET /v1/memory/search``."""

    model_config = ConfigDict(extra="forbid")

    query: str
    results: list[MemoryItem] = Field(default_factory=list)
    count: int = Field(default=0)


def _retrieval(request: Request) -> RetrievalEngine:
    engine = getattr(request.app.state, "memory_retrieval", None)
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory subsystem is not initialised.",
        )
    return cast(RetrievalEngine, engine)


@router.get("/search", response_model=MemorySearchResponse, summary="Search memory")
async def search_memory(
    request: Request,
    q: str = Query(min_length=1, description="Free-text query."),
    agent_id: str | None = Query(default=None, description="Scope results to one agent."),
    limit: int = Query(default=10, ge=1, le=100),
) -> MemorySearchResponse:
    """Full-text (BM25) search over remembered content.

    Uses the FTS-backed semantic path of
    :class:`agency.memory.sms.retrieval.RetrievalEngine`; graph traversal
    results are merged in by the hybrid path when available.
    """
    engine = _retrieval(request)
    try:
        items = await engine.semantic_search(q, agent_id=agent_id, limit=limit)
    except Exception as exc:  # Storage failure — surface as 503, not 500.
        log.exception("api.memory.search_failed", query=q)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"memory search failed: {exc}",
        ) from exc
    log.debug("api.memory.search", query=q, results=len(items))
    return MemorySearchResponse(query=q, results=items, count=len(items))


__all__ = ["MemorySearchResponse", "router"]
