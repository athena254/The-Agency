"""Evidence router — filtered browsing of findings and their evidence trail.

Mounted by :mod:`agency.api.server` at ``/v1/evidence``.

Endpoints
---------
GET  /v1/evidence                  List findings with filters.
GET  /v1/evidence/{finding_id}     Fetch one finding.
POST /v1/evidence                  Record a new finding.
GET  /v1/evidence/{finding_id}/trail  Append-only evidence entries for a finding.
"""

from __future__ import annotations

from typing import Annotated, cast

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from agency.evidence.store.models import EvidenceEntry, Finding, Severity, VerificationState
from agency.evidence.store.store import EvidenceStore, FindingsFilter

log = structlog.get_logger(__name__)

router = APIRouter(tags=["evidence"])


class FindingListResponse(BaseModel):
    """Envelope for ``GET /v1/evidence``."""

    model_config = ConfigDict(extra="forbid")

    findings: list[Finding] = Field(default_factory=list)
    count: int = Field(default=0)


def _store(request: Request) -> EvidenceStore:
    store = getattr(request.app.state, "evidence_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Evidence store is not initialised.",
        )
    return cast(EvidenceStore, store)


@router.get("", response_model=FindingListResponse, summary="List findings")
async def list_findings(
    request: Request,
    target: Annotated[str | None, Query(description="Substring match on target.")] = None,
    component: Annotated[
        str | None, Query(description="Substring match on affected component.")
    ] = None,
    severity: Annotated[Severity | None, Query()] = None,
    verification_status: Annotated[VerificationState | None, Query()] = None,
    min_confidence: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
    max_confidence: Annotated[float, Query(ge=0.0, le=1.0)] = 1.0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FindingListResponse:
    """List findings matching the given filters (newest first by default)."""
    store = _store(request)
    filters = FindingsFilter(
        target=target,
        component=component,
        severity=severity,
        verification_status=verification_status,
        min_confidence=min_confidence,
        max_confidence=max_confidence,
        limit=limit,
        offset=offset,
    )
    findings = await store.list_findings(filters)
    return FindingListResponse(findings=findings, count=len(findings))


@router.get("/{finding_id}", response_model=Finding, summary="Get a finding")
async def get_finding(finding_id: str, request: Request) -> Finding:
    """Fetch one finding by id."""
    store = _store(request)
    finding = await store.get_finding(finding_id)
    if finding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown finding {finding_id!r}"
        )
    return finding


@router.post(
    "", response_model=Finding, status_code=status.HTTP_201_CREATED, summary="Record a finding"
)
async def create_finding(payload: Finding, request: Request) -> Finding:
    """Persist a new finding."""
    store = _store(request)
    try:
        finding = await store.add_finding(payload)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    log.info("api.finding.created", finding_id=finding.id, target=finding.target)
    return finding


@router.get("/{finding_id}/trail", response_model=list[EvidenceEntry], summary="Evidence trail")
async def evidence_trail(finding_id: str, request: Request) -> list[EvidenceEntry]:
    """Return the append-only evidence trail for a finding, oldest first."""
    store = _store(request)
    if await store.get_finding(finding_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown finding {finding_id!r}"
        )
    return await store.evidence_for_finding(finding_id)


__all__ = ["FindingListResponse", "router"]
