"""Owner-guarded Forge API: bounded read and submit surface over BlacksmithOps."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agency.admission.models import PilotError
from agency.forge.metrics import compute_metrics
from agency.forge.orchestration import BlacksmithOps, BlacksmithStatus, ForgeJob
from agency.prototype.pilot import _boundary

router = APIRouter(prefix="/api/pilot/forge", tags=["owner-forge"])

_CAVEATS = ("tests_not_run", "security_review_not_run", "release_not_authorized")
_QUEUED_CAVEATS = ("queued_not_dispatched",)
_DISPATCH_CAVEATS = ("external_agent_runs", "no_ship_no_merge_no_release")
_DISPATCH_TIMEOUT_S = 1800
_SHUTDOWN_JOIN_S = 5
_DISPATCH_EXCEPTIONS = (
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    RuntimeError,
    OSError,
    sqlite3.Error,
)
_SAFE_ERROR_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_SENSITIVE_FIELDS = frozenset({"repo", "failure_detail", "changed_paths"})
_RequestId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", max_length=128)]
_BoundedText = Annotated[str, Field(max_length=1000)]
_RECEIPTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS forge_request_receipts (
    request_id  TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL,
    fingerprint TEXT NOT NULL
);
"""
_T = TypeVar("_T")


class ForgeJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: _RequestId
    title: Annotated[str, Field(min_length=1, max_length=200)]
    task: Annotated[str, Field(min_length=1, max_length=8000)]
    language: Annotated[str, Field(max_length=32)] = ""
    acceptance: Annotated[list[_BoundedText], Field(max_length=16)] = Field(default_factory=list)
    test_paths: Annotated[list[_BoundedText], Field(max_length=16)] = Field(default_factory=list)


class DispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _safe_error(exc: Exception) -> str:
    name = type(exc).__name__
    return name if _SAFE_ERROR_RE.fullmatch(name) else "DispatchFailed"


def _safe_job(job: ForgeJob) -> dict[str, Any]:
    data = job.model_dump(mode="json")
    return {key: value for key, value in data.items() if key not in _SENSITIVE_FIELDS}


def _safe_status(status: BlacksmithStatus) -> dict[str, Any]:
    data = status.model_dump(mode="json")
    data["recent"] = [
        {key: value for key, value in job.items() if key not in _SENSITIVE_FIELDS}
        for job in data["recent"]
    ]
    return data


class ForgeGateway:
    def __init__(self, db: Path, repo: Path) -> None:
        self._lock = threading.RLock()
        self._dispatch_lock = threading.Lock()
        self._dispatch_running = False
        self._dispatch_thread: threading.Thread | None = None
        self._last: dict[str, Any] | None = None
        self._db = db
        self._repo = str(repo)
        self._closed = False
        self._ops = BlacksmithOps(str(db))
        self._receipts = sqlite3.connect(str(db), check_same_thread=False)
        with self._receipts:
            self._receipts.executescript(_RECEIPTS_SCHEMA)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return _safe_status(self._ops.status())

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            metrics = compute_metrics(self._db)
        return metrics.model_dump(mode="json")

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return _safe_job(self._ops.get(job_id))

    def submit(
        self, *, request_id: str, fingerprint: str, payload: ForgeJobRequest
    ) -> tuple[dict[str, Any], bool]:
        with self._lock:
            row = self._receipts.execute(
                "SELECT job_id, fingerprint FROM forge_request_receipts WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is not None:
                if row[1] != fingerprint:
                    raise PilotError("conflict")
                try:
                    return _safe_job(self._ops.get(row[0])), True
                except KeyError:
                    raise PilotError("conflict") from None
            job = self._ops.submit(
                payload.title,
                payload.task,
                self._repo,
                acceptance=payload.acceptance,
                test_paths=payload.test_paths,
                language=payload.language,
            )
            with self._receipts:
                self._receipts.execute(
                    "INSERT INTO forge_request_receipts (request_id, job_id, fingerprint)"
                    " VALUES (?, ?, ?)",
                    (request_id, job.job_id, fingerprint),
                )
            return _safe_job(job), False

    def dispatch(self) -> None:
        with self._dispatch_lock:
            if self._dispatch_running:
                raise PilotError("conflict")
            self._dispatch_running = True
            thread = threading.Thread(target=self._run_dispatch, daemon=True)
            self._dispatch_thread = thread
        thread.start()

    def dispatch_status(self) -> dict[str, Any]:
        with self._dispatch_lock:
            last = None if self._last is None else dict(self._last)
            return {"running": self._dispatch_running, "last": last}

    def _run_dispatch(self) -> None:
        outcome: dict[str, Any] = {
            "ok": False,
            "job_id": None,
            "state": None,
            "error": None,
        }
        box: dict[str, Any] = {}
        done = threading.Event()

        def worker() -> None:
            box["error"] = "DispatchFailed"
            try:
                box["job"] = self._ops.dispatch_next()
            except _DISPATCH_EXCEPTIONS as exc:
                box["error"] = _safe_error(exc)
            else:
                box["error"] = None
            finally:
                done.set()

        runner = threading.Thread(target=worker, daemon=True)
        runner.start()
        if not done.wait(_DISPATCH_TIMEOUT_S):
            outcome["error"] = "DispatchTimeout"
        else:
            error = box.get("error")
            if error is not None:
                outcome["error"] = error
            else:
                job = box.get("job")
                outcome["ok"] = True
                if isinstance(job, ForgeJob):
                    outcome["job_id"] = job.job_id
                    outcome["state"] = job.state.value
        with self._dispatch_lock:
            self._last = outcome
            self._dispatch_running = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._ops.close()
            finally:
                self._receipts.close()

    def shutdown(self) -> None:
        with self._dispatch_lock:
            thread = self._dispatch_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=_SHUTDOWN_JOIN_S)
        self.close()


_GATEWAY_LOCK = threading.RLock()


def _gateway(request: Request) -> ForgeGateway:
    db = getattr(request.app.state, "forge_db", None)
    repo = getattr(request.app.state, "forge_repo", None)
    if (
        not isinstance(db, Path)
        or not db.is_absolute()
        or not isinstance(repo, Path)
        or not repo.is_absolute()
        or not repo.is_dir()
    ):
        raise PilotError("disabled")
    with _GATEWAY_LOCK:
        gateway = getattr(request.app.state, "forge_gateway", None)
        if not isinstance(gateway, ForgeGateway):
            gateway = ForgeGateway(db, repo)
            request.app.state.forge_gateway = gateway
        return gateway


def _call(request: Request, operation: Callable[[ForgeGateway], _T]) -> _T:
    try:
        return operation(_gateway(request))
    except PilotError:
        raise
    except KeyError:
        raise PilotError("not_found") from None
    except Exception:  # noqa: BLE001
        raise PilotError("disabled") from None


def close_forge(application: FastAPI) -> None:
    with _GATEWAY_LOCK:
        gateway = getattr(application.state, "forge_gateway", None)
        if isinstance(gateway, ForgeGateway):
            try:
                gateway.shutdown()
            except Exception:  # noqa: BLE001
                application.state.forge_gateway = None


@router.get("/status")
async def forge_status(request: Request) -> JSONResponse:
    await _boundary(request)
    status = _call(request, lambda gateway: gateway.status())
    return JSONResponse(content={"status": status, "caveats": list(_CAVEATS)})


@router.get("/metrics")
async def forge_metrics(request: Request) -> JSONResponse:
    await _boundary(request)
    metrics = _call(request, lambda gateway: gateway.metrics())
    return JSONResponse(content={"metrics": metrics, "caveats": list(_CAVEATS)})


@router.get("/jobs/{job_id}")
async def forge_job(request: Request, job_id: str) -> JSONResponse:
    await _boundary(request)
    if not job_id or len(job_id) > 128:
        raise PilotError("not_found")
    job = _call(request, lambda gateway: gateway.get(job_id))
    return JSONResponse(content={"job": job})


@router.post("/jobs")
async def submit_job(request: Request) -> JSONResponse:
    _pilot, _principal, body = await _boundary(request, write=True)
    if body is None:
        raise PilotError("invalid_request")
    try:
        payload = ForgeJobRequest.model_validate(body)
    except ValidationError:
        raise PilotError("invalid_request") from None
    fingerprint = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    job, replayed = _call(
        request,
        lambda gateway: gateway.submit(
            request_id=payload.request_id, fingerprint=fingerprint, payload=payload
        ),
    )
    return JSONResponse(
        status_code=200 if replayed else 202,
        content={"job": job, "replayed": replayed, "caveats": list(_QUEUED_CAVEATS)},
    )


@router.post("/dispatch")
async def dispatch_forge(request: Request) -> JSONResponse:
    _pilot, _principal, body = await _boundary(request, write=True)
    if body is None:
        raise PilotError("invalid_request")
    try:
        DispatchRequest.model_validate(body)
    except ValidationError:
        raise PilotError("invalid_request") from None
    _call(request, lambda gateway: gateway.dispatch())
    return JSONResponse(
        status_code=202,
        content={
            "started": True,
            "replayed": False,
            "caveats": list(_DISPATCH_CAVEATS),
        },
    )


@router.get("/dispatch")
async def forge_dispatch(request: Request) -> JSONResponse:
    await _boundary(request)
    return JSONResponse(content=_call(request, lambda gateway: gateway.dispatch_status()))
