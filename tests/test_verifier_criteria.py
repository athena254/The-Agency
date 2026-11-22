"""Independent-checker strictness (gap-10): no fabricated check-category criteria.

Pins that the orchestrator's classic path no longer passes the verifier's own
check-category names ("completeness", "accuracy", "safety") as if they were
task requirements: a correct output like ``"42"`` must verify as passed, while
safety enforcement (denylist) still fails a poisoned output. Also protects the
verifier contract for callers that pass real task criteria: the mention-check
behavior is unchanged for them.
"""

from __future__ import annotations

from typing import Any

import pytest

from agency.agents.planner import Subtask, TaskGraph
from agency.agents.verifier import AgentVerifier, VerificationResult
from agency.orchestrator import AgencyOrchestrator
from tests.test_orchestrator_specialist_dispatch import (
    _install_plan,
    _orchestrator,
    _RecordingLLM,
    _submit_classic_task,
)


async def _run_classic_task(
    orch: AgencyOrchestrator,
    monkeypatch: pytest.MonkeyPatch,
    reply: str,
) -> tuple[object, VerificationResult]:
    """Run one classic-path subtask with a canned reply; return (assessment, verification)."""
    worker = await orch.register_agent("worker", "test", [])
    _install_plan(
        orch, monkeypatch, TaskGraph(task="parent work", subtasks=[Subtask(title="Compute")])
    )
    monkeypatch.setattr(orch._executor, "_llm", _RecordingLLM(replies=[reply]))
    real_verify = orch._verifier.verify
    calls: list[dict[str, Any]] = []
    verdicts: list[VerificationResult] = []

    def spy(result: Any, **kwargs: Any) -> VerificationResult:
        calls.append({"result": result, **kwargs})
        verdict = real_verify(result, **kwargs)
        verdicts.append(verdict)
        return verdict

    monkeypatch.setattr(orch._verifier, "verify", spy)
    task = await _submit_classic_task(orch, worker.id)

    result = await orch.execute_task(task.task_id)

    assert result.assessment is not None
    # The classic path must pass the output and fabricate no criteria.
    assert calls == [{"result": reply}]
    return result.assessment, verdicts[0]


@pytest.mark.asyncio
async def test_correct_output_passes_without_fabricated_criteria(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A correct, terse answer ("42") must not be scored failed for lacking meta-words."""
    orch = _orchestrator()
    await orch.start()
    try:
        assessment, verification = await _run_classic_task(orch, monkeypatch, "42")

        # The verifier's own verdict: passed, no fabricated "missing criteria" issues.
        assert verification.passed is True
        assert verification.status.value == "passed"
        assert verification.checks["completeness"].passed is True
        assert all("missing criteria" not in issue for issue in verification.issues)

        # The assessment layer mirrors it honestly: completeness/safety passed,
        # accuracy honestly not performed (no trusted oracle), aggregate needs_review.
        subtask = assessment.subtasks[0]
        completeness, accuracy, safety = subtask.checks
        assert completeness.status == "passed"
        assert completeness.score == 1.0
        assert safety.status == "passed"
        assert accuracy.status == "not_performed"
        assert accuracy.reason == "no_trusted_accuracy_oracle"
        assert assessment.verification.value == "needs_review"
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_denylist_hit_still_fails_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing fabricated criteria must not weaken safety enforcement."""
    orch = _orchestrator()
    await orch.start()
    try:
        assessment, verification = await _run_classic_task(
            orch, monkeypatch, "here is my api_key=SECRET123"
        )

        assert verification.passed is False
        assert verification.checks["safety"].passed is False
        subtask = assessment.subtasks[0]
        _, _, safety = subtask.checks
        assert safety.status == "failed"
        assert safety.method == "legacy_denylist/v1"
        assert assessment.verification.value == "failed"
    finally:
        await orch.stop()


def test_real_task_criteria_mention_check_unchanged() -> None:
    """Callers passing real task requirements still get the mention-check contract."""
    verifier = AgentVerifier()

    passed = verifier.verify("port 443 open on target", ["port", "443"])
    assert passed.checks["completeness"].passed is True
    assert passed.checks["completeness"].details == "covers all 2 criteria."
    assert passed.passed is True

    missing = verifier.verify("nothing relevant here", ["exfiltration"])
    assert missing.checks["completeness"].passed is False
    assert "missing criteria" in missing.checks["completeness"].details
    assert missing.passed is False
