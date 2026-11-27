"""Bounded parallel execution of the cheap Forge gates, with a deferred review.

A caller injects named, side-effect-free cheap gate callables (inspection,
security, tests). They all run concurrently in a bounded thread pool; the
review callable is submitted *only after every cheap gate has produced a
verdict*. Each callable returns any object exposing a ``gate`` string
(``PASS``/``FAIL``/``SKIP``/``UNCERTAIN``); a bare string is accepted too.
No subprocess, network, repository or LLM is created here.

A stalled or raising gate is ``UNCERTAIN``, never a silent ``PASS``. The pool
is never waited on after a timeout, so a hanging gate cannot hold the caller
hostage. This module reports a verdict only; it never authorizes merge/deploy.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

MAX_WORKERS = 8
DEFAULT_WORKERS = 3
DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 300.0

GateState = Literal["PASS", "FAIL", "SKIP", "UNCERTAIN"]
GateFn = Callable[[], object]

CAVEATS: tuple[str, ...] = ("release_not_authorized",)


class GateResult(BaseModel):
    """Normalized outcome of one gate. Exception text is never retained."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    gate: GateState
    detail: str = ""


class ParallelGateReport(BaseModel):
    """One bounded cheap-gate round plus its deferred review verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    gate: GateState
    results: tuple[GateResult, ...]
    review_permitted: bool = False
    review: GateResult | None = None
    outcomes: dict[str, Any] = Field(default_factory=dict)
    caveats: tuple[str, ...] = CAVEATS
    note: str = "Cheap gates run first; review is gated on them. No merge/deploy authorization."


def _state_of(outcome: object) -> GateState:
    """Read the gate string from a returned object (or a bare string)."""
    if isinstance(outcome, str):
        text = outcome.upper()
    else:
        text = str(getattr(outcome, "gate", "")).upper()
    if text == "PASS":
        return "PASS"
    if text == "SKIP":
        return "SKIP"
    if text == "UNCERTAIN":
        return "UNCERTAIN"
    return "FAIL"


def _collect(name: str, future: Future[object], finished: set[Future[object]]) -> GateResult:
    """Turn one future into a result; timeout and exceptions are UNCERTAIN."""
    if future not in finished:
        future.cancel()
        return GateResult(name=name, gate="UNCERTAIN", detail="gate_timeout")
    try:
        outcome = future.result()
    except Exception as exc:  # noqa: BLE001 - a crashing gate yields no verdict; never pass silently
        # Log only the exception type: messages may contain secrets.
        return GateResult(
            name=name, gate="UNCERTAIN", detail=f"gate_exception:{type(exc).__name__}"
        )
    state = _state_of(outcome)
    detail = "" if state == "PASS" else f"gate_{state.lower()}"
    return GateResult(name=name, gate=state, detail=detail)


def run_parallel_gates(
    gates: dict[str, GateFn],
    *,
    review: GateFn | None = None,
    max_workers: int = DEFAULT_WORKERS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ParallelGateReport:
    """Run cheap gates concurrently, then run ``review`` only if all passed.

    ``gates`` maps gate name to an injected callable; insertion order is
    preserved in the report. A ``SKIP`` outcome is missing evidence and blocks
    review just like ``FAIL``/``UNCERTAIN``. Worker count and per-phase timeout
    are clamped to bounded ceilings; timed-out gates are cancelled, never
    waited on. ``review`` is optional and runs on the caller thread's behalf
    only after all cheap verdicts exist.
    """
    if not gates:
        raise ValueError("gates must not be empty")
    if any(not isinstance(name, str) or not name.strip() for name in gates):
        raise ValueError("gate names must be non-empty strings")
    if any(not callable(fn) for fn in gates.values()):
        raise ValueError("every gate value must be callable")
    if review is not None and not callable(review):
        raise ValueError("review must be callable")

    workers = max(1, min(max_workers, len(gates), MAX_WORKERS))
    budget_s = max(0.0, min(float(timeout_s), MAX_TIMEOUT_S))
    caveats: list[str] = list(CAVEATS)
    results: list[GateResult] = []
    review_result: GateResult | None = None
    permitted = False

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="forge-cheap-gate")
    try:
        futures = {name: pool.submit(fn) for name, fn in gates.items()}
        finished, _pending = wait(futures.values(), timeout=budget_s)
        results = [_collect(name, futures[name], finished) for name in gates]
        outcomes: dict[str, Any] = {}
        for name, future in futures.items():
            if future in finished:
                try:
                    outcomes[name] = future.result()
                except Exception as exc:  # noqa: BLE001 - verdict already captured by _collect
                    logger.debug("forge.parallel_outcome_skipped", error=type(exc).__name__)
        permitted = all(r.gate == "PASS" for r in results)

        if not permitted:
            caveats.append("review_not_permitted")
        elif review is None:
            caveats.append("review_not_run")
        else:
            review_future = pool.submit(review)
            review_finished, _ = wait((review_future,), timeout=budget_s)
            review_result = _collect("review", review_future, review_finished)
            if review_future in review_finished:
                try:
                    outcomes["review"] = review_future.result()
                except Exception as exc:  # noqa: BLE001 - verdict already captured
                    logger.debug("forge.parallel_review_outcome_skipped", error=type(exc).__name__)
    finally:
        # Never block on a timed-out/stalled gate; drop its result entirely.
        pool.shutdown(wait=False, cancel_futures=True)

    for result in results:
        if result.gate == "UNCERTAIN":
            caveats.append(f"uncertain:{result.name}:{result.detail}")
        elif result.gate == "SKIP":
            caveats.append(f"gate_skipped:{result.name}")
        elif result.gate == "FAIL":
            caveats.append(f"gate_failed:{result.name}")
    if review_result is not None and review_result.gate != "PASS":
        caveats.append("review_rejected")

    overall: GateState = (
        "PASS" if permitted and (review_result is None or review_result.gate == "PASS") else "FAIL"
    )
    report = ParallelGateReport(
        report_id=str(uuid4()),
        created_at=datetime.now(UTC),
        gate=overall,
        results=tuple(results),
        review_permitted=permitted,
        review=review_result,
        outcomes=outcomes,
        caveats=tuple(caveats),
    )
    logger.info(
        "forge.parallel_gates", gate=overall, cheap=len(results), review=review_result is not None
    )
    return report


__all__ = [
    "CAVEATS",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_WORKERS",
    "MAX_TIMEOUT_S",
    "MAX_WORKERS",
    "GateFn",
    "GateResult",
    "GateState",
    "ParallelGateReport",
    "run_parallel_gates",
]
