"""Hermetic tests for BlacksmithOps: the Forge job lifecycle (fake worker)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agency.forge.evidence import EvidenceStore
from agency.forge.external_agent import ExternalAgentReport
from agency.forge.inspection import InspectionReport
from agency.forge.merge_queue import MergeQueueStore
from agency.forge.orchestration import BlacksmithOps, JobState
from agency.forge.security_scan import SecurityReport
from agency.tools.registry import ToolRegistry

MINI_ADD_CODE = """\
def _add(a, b):
    return a + b


async def run(args, ctx):
    return {"result": _add(int(args["a"]), int(args["b"]))}
"""

MINI_ADD_TESTS = """\
import asyncio

import tool


def test_add():
    assert asyncio.run(tool.run({"a": 2, "b": 3}, None)) == {"result": 5}
"""


def _report(
    gate: str = "PASS",
    *,
    isolation: tuple[str, ...] = (),
    changed: tuple[str, ...] = ("src/x.py",),
    failure: str = "",
) -> ExternalAgentReport:
    now = datetime.now(UTC)
    root = "D:/fake/worktree"
    return ExternalAgentReport(
        report_id=str(uuid4()),
        created_at=now,
        worktree_path=root,
        branch="blacksmith-test",
        task="t",
        exit_code=0 if gate == "PASS" else 1,
        changed_paths=changed,
        isolation_violations=isolation,
        failure_detail=failure,
        inspection=InspectionReport(report_id=str(uuid4()), created_at=now, root=root, gate="PASS"),
        security=SecurityReport(
            report_id=str(uuid4()),
            created_at=now,
            root=root,
            gate="PASS",
            scanned_files=1,
            skipped_files=0,
        ),
        gate=gate,  # type: ignore[arg-type]
    )


def _ops(tmp_path, runner, **over):
    events: list[tuple[str, dict]] = []
    ops = BlacksmithOps(
        str(tmp_path / "jobs.db"),
        runner=runner,
        evidence=EvidenceStore(str(tmp_path / "evidence.db")),
        merge_queue=MergeQueueStore(str(tmp_path / "mq.db")),
        audit_sink=lambda event, payload: events.append((event, payload)),
        **over,
    )
    return ops, events


def test_submit_creates_queued_job(tmp_path) -> None:
    calls = []

    def runner(*a, **k):
        calls.append(a)
        return _report()

    ops, events = _ops(tmp_path, runner)
    try:
        job = ops.submit("Add x", "Add function x", "D:/fake/repo")
        assert job.state is JobState.QUEUED
        assert job.attempts == 0
        assert job.job_id
        assert any(e == "job_submitted" for e, _ in events)
        # persisted: a fresh ops on the same db sees the job
        ops2 = BlacksmithOps(str(tmp_path / "jobs.db"), runner=runner)
        try:
            assert ops2.status().by_state["QUEUED"] == 1
        finally:
            ops2.close()
    finally:
        ops.close()


def test_submit_refuses_relative_repo(tmp_path) -> None:
    ops, _ = _ops(tmp_path, lambda *a, **k: _report())
    try:
        with pytest.raises(ValueError, match="absolute"):
            ops.submit("t", "task", "relative/repo")
    finally:
        ops.close()


def test_dispatch_pass_lands_in_merge_queue(tmp_path) -> None:
    rep = _report()
    ops, events = _ops(tmp_path, lambda *a, **k: rep)
    try:
        job = ops.submit("Add x", "Add function x", "D:/fake/repo")
        done = ops.dispatch_next()
        assert done is not None
        assert done.job_id == job.job_id
        assert done.state is JobState.AWAITING_HUMAN
        assert done.attempts == 1
        assert done.gate == "PASS"
        assert done.report_id == rep.report_id
        assert done.artifact_sha256
        assert done.merge_report_id
        mq = ops.merge_queue.load(done.merge_report_id)
        assert mq is not None
        assert mq.merge_allowed is False  # human approval still required
        assert ops.evidence.load(rep.report_id) is not None
        names = [e for e, _ in events]
        assert "job_dispatched" in names
        assert "job_passed" in names
        assert "job_awaiting_human" in names
    finally:
        ops.close()


def test_fail_retries_then_blocks(tmp_path) -> None:
    ops, _ = _ops(tmp_path, lambda *a, **k: _report("FAIL", failure="tests failed"))
    try:
        job = ops.submit("t", "task", "D:/fake/repo")
        states = []
        for _ in range(3):
            done = ops.dispatch_next()
            assert done is not None
            states.append(done.state)
        assert states == [JobState.FAILED, JobState.FAILED, JobState.BLOCKED]
        blocked = ops.get(job.job_id)
        assert blocked.attempts == 3
        assert "tests failed" in blocked.failure_detail
        assert ops.dispatch_next() is None  # nothing dispatchable left
        assert job.job_id in ops.status().blocked
    finally:
        ops.close()


def test_isolation_violation_fails_even_on_pass(tmp_path) -> None:
    rep = _report("PASS", isolation=("src/evil.py",))
    ops, _ = _ops(tmp_path, lambda *a, **k: rep)
    try:
        ops.submit("t", "task", "D:/fake/repo")
        done = ops.dispatch_next()
        assert done is not None
        assert done.state is JobState.FAILED
        assert "isolation" in done.failure_detail
        assert done.merge_report_id == ""
    finally:
        ops.close()


def test_one_job_per_dispatch_and_queue_order(tmp_path) -> None:
    ops, _ = _ops(tmp_path, lambda *a, **k: _report())
    try:
        j1 = ops.submit("first", "task 1", "D:/fake/repo")
        j2 = ops.submit("second", "task 2", "D:/fake/repo")
        done = ops.dispatch_next()
        assert done is not None and done.job_id == j1.job_id
        assert ops.get(j2.job_id).state is JobState.QUEUED
    finally:
        ops.close()


def test_crash_recovery_marks_dispatched_uncertain(tmp_path) -> None:
    ops, _ = _ops(tmp_path, lambda *a, **k: _report())
    try:
        job = ops.submit("t", "task", "D:/fake/repo")
        # simulate a crash mid-dispatch: row left DISPATCHED
        ops._conn.execute(
            "UPDATE forge_jobs SET state = ? WHERE job_id = ?",
            (JobState.DISPATCHED.value, job.job_id),
        )
        ops._conn.commit()
    finally:
        ops.close()
    ops2, events = _ops(tmp_path, lambda *a, **k: _report())
    try:
        recovered = ops2.get(job.job_id)
        assert recovered.state is JobState.UNCERTAIN
        assert ops2.dispatch_next() is None  # never auto-resumed
        assert any(e == "job_uncertain" for e, _ in events)
    finally:
        ops2.close()


def test_runner_exception_fails_honestly(tmp_path) -> None:
    def boom(*a, **k):
        raise RuntimeError("worker exploded")

    ops, _ = _ops(tmp_path, boom)
    try:
        ops.submit("t", "task", "D:/fake/repo")
        done = ops.dispatch_next()
        assert done is not None
        assert done.state is JobState.FAILED
        assert "RuntimeError" in done.failure_detail
        assert done.gate != "PASS"
    finally:
        ops.close()


def test_status_reports_queue(tmp_path) -> None:
    ops, _ = _ops(tmp_path, lambda *a, **k: _report("FAIL", failure="no"))
    try:
        ops.submit("t", "task", "D:/fake/repo")
        for _ in range(3):
            ops.dispatch_next()
        status = ops.status()
        assert status.total == 1
        assert status.by_state.get("BLOCKED") == 1
        assert len(status.recent) == 1
    finally:
        ops.close()


def test_build_tool_job_uses_real_gates(tmp_path) -> None:
    ops, events = _ops(tmp_path, lambda *a, **k: _report())
    registry = ToolRegistry()
    try:
        result = ops.build_tool_job("mini_add", MINI_ADD_CODE, MINI_ADD_TESTS, registry)
        assert result.gate == "PASS"
        assert result.registered is True
        assert result.sha256
        assert result.report_id
        assert any(e == "tool_build_recorded" for e, _ in events)
        jobs = ops.status().recent
        assert any(j.report_id == result.report_id for j in jobs)
    finally:
        ops.close()
