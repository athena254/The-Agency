"""Tests for ECC wave-2 scale slice: bounded load probe and live job view.

Two deep modules are exercised through their public seams only:

* ``run_load_probe`` — a bounded synthetic load driven by an injected
  admitter and clock. No network, no orchestration, no real jobs.
* ``view_job`` / ``build_jobs_view`` / ``render_jobs_view`` — a read-only
  view over injected job records. No mutation, no DB access.

Wiring into the CLI belongs to the parent wave, not these tests.
"""

from __future__ import annotations

import copy
import socket
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pytest
from pydantic import ValidationError

from agency.forge.live_view import (
    JobView,
    build_jobs_view,
    render_jobs_view,
    view_job,
)
from agency.forge.load_harness import (
    MAX_JOBS,
    Admission,
    Backpressure,
    LoadReport,
    SyntheticJob,
    run_load_probe,
)

# ---------------------------------------------------------------------------
# load_harness fakes
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _admitter(
    clock: _Clock,
    latencies: list[float],
    accept: Callable[[int], bool],
) -> tuple[Callable[[Any], Admission], list[str]]:
    seen: list[str] = []

    def admit(job: Any) -> Admission:
        index = len(seen)
        seen.append(job.job_id)
        clock.advance(latencies[index % len(latencies)])
        return Admission(accepted=accept(index))

    return admit, seen


def test_probe_with_no_jobs_is_empty_and_never_calls_admitter() -> None:
    def explode(job: Any) -> Admission:
        raise AssertionError("admitter must not be called for an empty probe")

    report = run_load_probe(explode, max_jobs=0)
    assert report.submitted == 0
    assert report.accepted == 0
    assert report.rejected == 0
    assert report.backpressure is Backpressure.NONE
    assert "no_jobs" in report.caveats
    assert "in_process_no_network" in report.caveats


def test_probe_counts_acceptance_and_measures_latency_deterministically() -> None:
    clock = _Clock()
    admit, seen = _admitter(clock, [0.01, 0.02, 0.03], lambda _: True)

    report = run_load_probe(admit, max_jobs=3, clock=clock)

    assert seen == ["synthetic-0", "synthetic-1", "synthetic-2"]
    assert report.submitted == 3
    assert report.accepted == 3
    assert report.rejected == 0
    assert report.p50_ms == pytest.approx(20.0)
    assert report.p95_ms == pytest.approx(20.0)
    assert report.max_ms == pytest.approx(30.0)
    assert report.backpressure is Backpressure.NONE
    assert "synthetic_jobs_only" in report.caveats


def test_high_rejection_rate_recommends_shed_load() -> None:
    clock = _Clock()
    admit, _ = _admitter(clock, [0.01], lambda i: i == 0)

    report = run_load_probe(admit, max_jobs=10, clock=clock)

    assert report.submitted == 10
    assert report.accepted == 1
    assert report.rejected == 9
    assert report.backpressure is Backpressure.SHED_LOAD
    assert "shed" in report.recommendation.lower()


def test_high_latency_without_rejection_recommends_throttle() -> None:
    clock = _Clock()
    admit, _ = _admitter(clock, [1.5], lambda _: True)

    report = run_load_probe(admit, max_jobs=4, clock=clock)

    assert report.rejected == 0
    assert report.p95_ms == pytest.approx(1500.0)
    assert report.backpressure is Backpressure.THROTTLE
    assert "slow" in report.recommendation.lower() or "thrott" in report.recommendation.lower()


def test_probe_is_bounded_and_clamps_oversized_requests() -> None:
    admits = 0

    def admit(job: Any) -> Admission:
        nonlocal admits
        admits += 1
        return Admission(accepted=True)

    report = run_load_probe(admit, max_jobs=MAX_JOBS + 50)

    assert admits == MAX_JOBS
    assert report.submitted == MAX_JOBS
    assert "max_jobs_clamped" in report.caveats


def test_probe_uses_injected_job_factory() -> None:
    clock = _Clock()
    admit, seen = _admitter(clock, [0.01], lambda _: True)

    def factory(index: int) -> SyntheticJob:
        return SyntheticJob(job_id=f"job-{index}")

    report = run_load_probe(admit, max_jobs=2, clock=clock, job_factory=factory)

    assert seen == ["job-0", "job-1"]
    assert report.accepted == 2


def test_probe_never_uses_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("load probe attempted network I/O")

    monkeypatch.setattr(socket, "socket", deny)
    clock = _Clock()
    admit, _ = _admitter(clock, [0.01], lambda _: True)

    report = run_load_probe(admit, max_jobs=3, clock=clock)

    assert report.accepted == 3
    assert "in_process_no_network" in report.caveats


def test_load_report_is_frozen() -> None:
    clock = _Clock()
    admit, _ = _admitter(clock, [0.01], lambda _: True)
    report = run_load_probe(admit, max_jobs=1, clock=clock)

    assert isinstance(report, LoadReport)
    with pytest.raises(ValidationError):
        report.submitted = 99


# ---------------------------------------------------------------------------
# live_view fakes
# ---------------------------------------------------------------------------


class _State(str, Enum):
    QUEUED = "QUEUED"
    UNCERTAIN = "UNCERTAIN"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    AWAITING_HUMAN = "AWAITING_HUMAN"


@dataclass
class _Phase:
    name: str
    duration_s: float


@dataclass
class _Bare:
    job_id: str


@dataclass
class _Trace:
    phases: tuple[_Phase, ...]
    estimated_cost_usd: float | None = None
    caveats: tuple[str, ...] = ()


@dataclass
class _Job:
    job_id: str
    state: object = "QUEUED"
    trace: object | None = None
    gate: str = ""
    changed_paths: tuple[str, ...] = ()
    failure_detail: str = ""
    cost_usd: float = 0.0


def test_view_reads_state_phases_cost_gates_and_does_not_mutate_record() -> None:
    job = _Job(
        job_id="j1",
        state=_State.AWAITING_HUMAN,
        trace=_Trace(
            phases=(_Phase("agent", 1.2), _Phase("tests", 0.5)),
            estimated_cost_usd=0.0123456,
        ),
        gate="PASS",
        changed_paths=("src/auth/login.py",),
    )
    before = copy.deepcopy(job)

    view = view_job(job)

    assert job == before
    assert isinstance(view, JobView)
    assert view.job_id == "j1"
    assert view.state == "AWAITING_HUMAN"
    assert tuple(p.name for p in view.phases) == ("agent", "tests")
    assert view.total_s == pytest.approx(1.7)
    assert view.cost_usd == pytest.approx(0.012346)
    assert view.gate == "PASS"
    assert "auth" in view.required_gates
    assert view.stuck_reason == ""


def test_missing_data_produces_honest_caveats() -> None:
    view = view_job(_Bare(job_id="bare"))

    assert view.state == "UNKNOWN"
    assert view.phases == ()
    assert view.total_s == 0.0
    assert view.cost_usd is None
    assert view.gate == ""
    assert "state_not_reported" in view.caveats
    assert "phases_not_reported" in view.caveats
    assert "cost_not_reported" in view.caveats
    assert "gate_not_reported" in view.caveats


def test_stuck_reasons_from_state_and_failure_detail() -> None:
    uncertain = view_job(_Job(job_id="u", state="UNCERTAIN"))
    assert "uncertain" in uncertain.stuck_reason.lower()

    blocked = view_job(_Job(job_id="b", state=_State.BLOCKED, failure_detail="needs human gate"))
    assert "blocked" in blocked.stuck_reason.lower()
    assert "needs human gate" in blocked.stuck_reason


def test_view_accepts_mapping_records() -> None:
    record = {
        "job_id": "m1",
        "state": "FAILED",
        "failure_detail": "retries exhausted",
        "gate": "FAIL",
    }

    view = view_job(record)

    assert view.state == "FAILED"
    assert view.gate == "FAIL"
    assert "retries exhausted" in view.stuck_reason


def test_build_and_render_jobs_view_are_read_only_and_bounded() -> None:
    jobs = [
        _Job(job_id="a", state="QUEUED"),
        _Job(job_id="b", state="UNCERTAIN", failure_detail="crashed dispatch"),
    ]
    before = copy.deepcopy(jobs)

    collection = build_jobs_view(jobs)
    text = render_jobs_view(jobs)

    assert jobs == before
    assert collection.jobs[0].job_id == "a"
    assert collection.jobs[1].job_id == "b"
    assert isinstance(collection.jobs, tuple)
    assert "Jobs: 2" in text
    assert "a" in text and "b" in text
    assert "UNCERTAIN" in text
    assert "read_only_snapshot" in text


def test_render_jobs_view_handles_empty_input() -> None:
    collection = build_jobs_view([])
    text = render_jobs_view([])

    assert collection.jobs == ()
    assert "Jobs: 0" in text
