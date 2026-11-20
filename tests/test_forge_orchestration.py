"""Hermetic BlacksmithOps lifecycle tests."""

from __future__ import annotations

from agency.forge.orchestration import BlacksmithOps, JobState


def test_submit_dispatch_pass_awaiting_human():
    ops = BlacksmithOps()
    job = ops.submit("t", "task", "repo")
    assert job.state == JobState.QUEUED
    dispatched = ops.dispatch_next()
    assert dispatched is not None and dispatched.job_id == job.job_id
    assert dispatched.state == JobState.DISPATCHED
    verdict = ops.record_verdict(job.job_id, {"passed": True, "report_id": "r1"})
    assert verdict.state == JobState.AWAITING_HUMAN
    assert ops.status().queued == 0


def test_fail_retry_then_block():
    ops = BlacksmithOps(max_retries=1)
    job = ops.submit("t", "task", "repo")
    ops.dispatch_next()
    v1 = ops.record_verdict(job.job_id, {"passed": False})
    assert v1.state == JobState.RETRYING
    ops.dispatch_next()
    v2 = ops.record_verdict(job.job_id, {"passed": False})
    assert v2.state == JobState.BLOCKED


def test_one_in_flight():
    ops = BlacksmithOps()
    ops.submit("a", "t", "r")
    ops.submit("b", "t", "r")
    first = ops.dispatch_next()
    assert ops.dispatch_next() is None
    ops.record_verdict(first.job_id, {"passed": True})
    second = ops.dispatch_next()
    assert second is not None
