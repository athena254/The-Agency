"""Bounded synthetic load probe with measured acceptance/latency and backpressure.

Deep module: a caller injects an admitter (the only thing that can "run" a
synthetic job) and, optionally, a clock and a job factory. The harness itself
never opens a socket, never imports orchestration and never touches a real
job. It submits a bounded number of synthetic jobs, measures per-submission
latency through the injected clock, and derives an explicit backpressure
recommendation from the observed rejection rate and tail latency.

Results are a snapshot of this in-process probe, never a claim about a real
system: every report carries those caveats.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import Enum
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

MAX_JOBS = 512
DEFAULT_REJECT_THRESHOLD = 0.10
DEFAULT_LATENCY_THRESHOLD_MS = 1000.0


class SyntheticJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    label: str = "synthetic"


class Admission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool


class Backpressure(str, Enum):
    NONE = "none"
    THROTTLE = "throttle"
    SHED_LOAD = "shed_load"


Admitter: TypeAlias = Callable[[SyntheticJob], Admission]
Clock: TypeAlias = Callable[[], float]
JobFactory: TypeAlias = Callable[[int], SyntheticJob]


class LoadReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    submitted: int = Field(ge=0)
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    p50_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)
    max_ms: float = Field(ge=0)
    backpressure: Backpressure
    recommendation: str = Field(min_length=1)
    caveats: tuple[str, ...] = ()


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * (len(ordered) - 1)))
    return ordered[index]


def _recommend(
    *,
    submitted: int,
    rejected: int,
    p95_ms: float,
    reject_threshold: float,
    latency_threshold_ms: float,
) -> tuple[Backpressure, str]:
    if submitted == 0:
        return Backpressure.NONE, "no_load_submitted_nothing_to_recommend"
    reject_rate = rejected / submitted
    if reject_rate >= reject_threshold:
        return (
            Backpressure.SHED_LOAD,
            (
                f"shed_load: reject_rate={reject_rate:.2f} >= {reject_threshold:.2f}; "
                "slow or shed inbound jobs before adding capacity"
            ),
        )
    if p95_ms >= latency_threshold_ms:
        return (
            Backpressure.THROTTLE,
            (
                f"throttle: p95={p95_ms:.0f}ms >= {latency_threshold_ms:.0f}ms; "
                "reduce submission rate until tail latency recovers"
            ),
        )
    return (
        Backpressure.NONE,
        (
            f"no_backpressure: reject_rate={reject_rate:.2f}, p95={p95_ms:.0f}ms; "
            "current rate is within the configured thresholds"
        ),
    )


def _default_job(index: int) -> SyntheticJob:
    return SyntheticJob(job_id=f"synthetic-{index}")


def run_load_probe(
    admitter: Admitter,
    *,
    max_jobs: int,
    clock: Clock | None = None,
    job_factory: JobFactory | None = None,
    reject_threshold: float = DEFAULT_REJECT_THRESHOLD,
    latency_threshold_ms: float = DEFAULT_LATENCY_THRESHOLD_MS,
) -> LoadReport:
    """Submit a bounded number of synthetic jobs and measure admission.

    The only external effect is calls to ``admitter``; latency is measured as
    elapsed time on ``clock`` (default :func:`time.perf_counter`). Both seams
    are injected so a caller can drive a fully deterministic probe with no
    sleeps and no network.
    """
    observed_clock: Clock = clock if clock is not None else time.perf_counter
    make_job: JobFactory = job_factory if job_factory is not None else _default_job

    caveats = [
        "synthetic_jobs_only",
        "in_process_no_network",
        "single_threaded_probe",
        "not_end_to_end_latency",
    ]

    count = max(0, int(max_jobs))
    if count > MAX_JOBS:
        count = MAX_JOBS
        caveats.append("max_jobs_clamped")
    if count == 0:
        caveats.append("no_jobs")

    latencies_ms: list[float] = []
    accepted = 0
    rejected = 0
    for index in range(count):
        job = make_job(index)
        started = observed_clock()
        admission = admitter(job)
        elapsed_ms = (observed_clock() - started) * 1000.0
        latencies_ms.append(max(0.0, round(elapsed_ms, 3)))
        if admission.accepted:
            accepted += 1
        else:
            rejected += 1

    p50 = round(_percentile(latencies_ms, 0.50), 3)
    p95 = round(_percentile(latencies_ms, 0.95), 3)
    worst = round(max(latencies_ms, default=0.0), 3)
    backpressure, recommendation = _recommend(
        submitted=count,
        rejected=rejected,
        p95_ms=p95,
        reject_threshold=reject_threshold,
        latency_threshold_ms=latency_threshold_ms,
    )
    return LoadReport(
        submitted=count,
        accepted=accepted,
        rejected=rejected,
        p50_ms=p50,
        p95_ms=p95,
        max_ms=worst,
        backpressure=backpressure,
        recommendation=recommendation,
        caveats=tuple(caveats),
    )


def format_load_report(report: LoadReport) -> str:
    """Render a compact, human-readable load report (for CLI wiring)."""
    lines = [
        (
            f"Synthetic load: {report.submitted} submitted, "
            f"{report.accepted} accepted, {report.rejected} rejected"
        ),
        (f"  latency ms: p50={report.p50_ms:.1f} p95={report.p95_ms:.1f} max={report.max_ms:.1f}"),
        f"  backpressure: {report.backpressure.value} - {report.recommendation}",
        "  caveats: " + ", ".join(report.caveats),
    ]
    return "\n".join(lines)


__all__ = [
    "MAX_JOBS",
    "Admission",
    "Admitter",
    "Backpressure",
    "Clock",
    "LoadReport",
    "SyntheticJob",
    "format_load_report",
    "run_load_probe",
]
