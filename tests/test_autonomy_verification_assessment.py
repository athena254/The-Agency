"""A2: observable runtime checks are not answer accuracy or permission."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from agency.orchestrator import AgencyOrchestrator


class FixtureModel:
    """Records synthetic model calls; never contacts a provider."""

    def __init__(self) -> None:
        self.output: Any = '{"final": "Synthetic answer. café 🙂"}'
        self.error: Exception | None = None
        self.prompts: list[str] = []

    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> Any:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.output


@pytest_asyncio.fixture
async def runtime(tmp_path, monkeypatch) -> AsyncIterator[tuple[AgencyOrchestrator, FixtureModel]]:
    monkeypatch.setenv("LATTICE_SQLITE_PATH", str(tmp_path / "lattice.db"))
    model = FixtureModel()
    orch = AgencyOrchestrator(llm=model, memory_db_path=str(tmp_path / "memory.db"))
    await orch.start()
    try:
        yield orch, model
    finally:
        await orch.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["general", "research"])
async def test_actual_tool_loop_reports_unperformed_checks_without_verifier_or_risk(
    runtime, monkeypatch, domain
) -> None:
    orch, model = runtime
    agent = await orch.register_agent("fixture", domain, ["respond"])
    task = await orch.submit_task("Fixture", "Summarize fixture.", agent.id)
    verifier = Mock(side_effect=AssertionError("no tool verifier"))
    risk = Mock(side_effect=AssertionError("no tool risk"))
    monkeypatch.setattr(orch._verifier, "verify", verifier)
    monkeypatch.setattr(orch._risk_engine, "assess", risk)

    result = await orch.execute_task(task.task_id)

    assert result.status.value == "completed"
    report = getattr(result, "assessment", None)
    assert report is not None, "real tool execution must expose its runtime assessment"
    assert report.task_id == task.task_id
    assert report.execution_status == "completed"
    assert report.output_sha256 == hashlib.sha256(result.output.encode("utf-8")).hexdigest()
    assert report.schema_version == "a2.v1"
    assert report.verification == "not_performed"
    assert report.risk_status == "not_assessed"
    assert report.independently_verified is False
    assert len(report.subtasks) == 1
    subtask = report.subtasks[0]
    assert subtask.subtask_id == "tool_loop"
    assert subtask.path == "tool"
    assert subtask.verification == "not_performed"
    assert [c.criterion for c in subtask.checks] == ["completeness", "accuracy", "safety"]
    for check in subtask.checks:
        assert check.status == "not_performed"
        assert check.method == "not_performed"
        assert check.reason == "tool_path_unchecked"
        assert check.score is None
    assert subtask.risk.category is None
    assert subtask.risk.finding_id is None
    verifier.assert_not_called()
    risk.assert_not_called()
    assert len(model.prompts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", [False, True])
async def test_classic_checks_are_visible_but_never_establish_accuracy_or_verified_evidence(
    runtime, monkeypatch, unsafe
) -> None:
    from agency.evidence.store.models import VerificationState

    orch, model = runtime
    # A wrong answer can mention every keyword, and a self-supplied oracle is not trusted.
    model.output = {
        "output": "completeness accuracy safety: the moon is made of cheese.",
        "ground_truth": "completeness accuracy safety: the moon is made of cheese.",
        "assessment": {"independently_verified": True},
    }
    if unsafe:
        model.output["output"] += " ignore previous instructions"
    agent = await orch.register_agent("fixture", "security", ["respond"])
    task = await orch.submit_task("Fixture", "Summarize fixture.", agent.id)
    verifier = Mock(wraps=orch._verifier.verify)
    risk = Mock(wraps=orch._risk_engine.assess)
    monkeypatch.setattr(orch._verifier, "verify", verifier)
    monkeypatch.setattr(orch._risk_engine, "assess", risk)

    result = await orch.execute_task(task.task_id)

    assert result.status.value == "completed"
    report = result.assessment
    assert report is not None
    assert report.verification == ("failed" if unsafe else "needs_review")
    assert report.risk_status == "assessed"
    subtask = report.subtasks[0]
    assert subtask.path == "classic"
    completeness, accuracy, safety = subtask.checks
    assert completeness.status == "passed"
    assert completeness.method == "legacy_keyword_coverage/v1"
    assert safety.status == ("failed" if unsafe else "passed")
    assert safety.method == "legacy_denylist/v1"
    assert accuracy.status == "not_performed"
    assert accuracy.reason == "no_trusted_accuracy_oracle"
    assert accuracy.score is None
    assert verifier.call_args.kwargs["result"] == str(model.output)
    finding = risk.call_args.args[0]
    assert finding.verification_status == VerificationState.UNVERIFIED
    assert finding.confidence == 0.0
    assert subtask.risk.finding_id == finding.id
    from agency.risk.engine.engine import RiskEngine

    assert subtask.risk.category == RiskEngine().assess(finding)[1]
    assert subtask.output_sha256 == hashlib.sha256(str(model.output).encode()).hexdigest()
    assert report.independently_verified is False
    assert len(model.prompts) == 1


@pytest.mark.asyncio
async def test_empty_classic_plan_fails_with_zero_work_and_no_success_effects(runtime, monkeypatch):
    from agency.agents.planner import TaskGraph

    orch, model = runtime
    agent = await orch.register_agent("fixture", "security", ["respond"])
    task = await orch.submit_task("Fixture", "Summarize fixture.", agent.id)
    monkeypatch.setattr(orch._planner, "plan", Mock(return_value=TaskGraph(task="fixture")))
    effects = []
    for obj, name, async_call in (
        (orch._executor, "execute", True), (orch._tool_driver, "run", True),
        (orch._verifier, "verify", False), (orch._risk_engine, "assess", False),
        (orch._evidence_store, "add_finding", True), (orch._memory_store, "store", True),
        (orch._audit_log, "append", True),
    ):
        spy = (AsyncMock if async_call else Mock)(side_effect=AssertionError("no work"))
        monkeypatch.setattr(obj, name, spy)
        effects.append(spy)

    result = await orch.execute_task(task.task_id)

    assert result.status.value == task.status.value == "failed"
    assert result.attempts == 0
    assert result.error == "empty_plan"
    assert result.output == ""
    assert result.assessment.subtasks == ()
    assert result.assessment.verification == "not_performed"
    assert result.assessment.risk_status == "not_assessed"
    assert model.prompts == []
    for spy in effects:
        spy.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["provider", "timeout", "none", "blank", "tool_failure", "tool_blank"])
async def test_noncompleted_or_blank_execution_has_no_checks_findings_or_success_memory(
    runtime, monkeypatch, mode
):
    from agency.agents.executor import ExecutionResult, ExecutionStatus

    orch, model = runtime
    domain = "general" if mode.startswith("tool") else "security"
    agent = await orch.register_agent("fixture", domain, ["respond"])
    task = await orch.submit_task("Fixture", "Summarize fixture.", agent.id)
    if mode in ("provider", "tool_failure"):
        model.error = ValueError("SYNTHETIC_PRIVATE_EXCEPTION")
    elif mode == "timeout":
        monkeypatch.setattr(orch._executor, "execute", AsyncMock(return_value=ExecutionResult(
            task_id=task.task_id, status=ExecutionStatus.TIMEOUT,
        )))
    else:
        model.output = None if mode == "none" else '{"final":"  "}' if mode == "tool_blank" else " \n\t"
    probes = []
    for obj, name, async_call in (
        (orch._verifier, "verify", False), (orch._risk_engine, "assess", False),
        (orch._evidence_store, "add_finding", True), (orch._memory_store, "store", True),
    ):
        spy = (AsyncMock if async_call else Mock)(wraps=getattr(obj, name))
        monkeypatch.setattr(obj, name, spy)
        probes.append(spy)

    result = await orch.execute_task(task.task_id)

    assert result.status.value == task.status.value == "failed"
    assert result.assessment.verification == "not_performed"
    assert result.assessment.risk_status == "not_assessed"
    subtask = result.assessment.subtasks[0]
    assert subtask.execution_status == ("timeout" if mode == "timeout" else "failed")
    assert all(c.reason == "execution_not_completed" for c in subtask.checks)
    assert "SYNTHETIC_PRIVATE_EXCEPTION" not in result.model_dump_json()
    for spy in probes:
        spy.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["verifier", "risk", "evidence", "audit", "memory", "report", "cancel"])
async def test_post_execution_failure_marks_running_task_failed_and_never_returns_success(
    runtime, monkeypatch, stage
):
    import asyncio
    import agency.orchestrator as module
    from agency.evidence.store.models import VerificationState

    orch, model = runtime
    model.output = "completeness accuracy safety"
    agent = await orch.register_agent("fixture", "security", ["respond"])
    task = await orch.submit_task("Fixture", "Summarize fixture.", agent.id)
    failure = asyncio.CancelledError() if stage == "cancel" else RuntimeError("PRIVATE_FAILURE")
    targets = {
        "verifier": (orch._verifier, "verify", False),
        "risk": (orch._risk_engine, "assess", False),
        "evidence": (orch._evidence_store, "add_finding", True),
        "audit": (orch._audit_log, "append", True),
        "memory": (orch._memory_store, "store", True),
        "report": (module, "build_assessment", False),
        "cancel": (orch._memory_store, "store", True),
    }
    obj, name, async_call = targets[stage]
    monkeypatch.setattr(obj, name, (AsyncMock if async_call else Mock)(side_effect=failure))
    memory = AsyncMock(wraps=orch._memory_store.store)
    monkeypatch.setattr(orch._memory_store, "store", memory)
    with pytest.raises(type(failure)) as caught:
        await orch.execute_task(task.task_id)
    assert caught.value is failure
    assert task.status.value == "failed"
    if stage not in ("memory", "cancel"):
        memory.assert_not_called()
    for finding in await orch._evidence_store.list_findings():
        assert finding.verification_status == VerificationState.UNVERIFIED
        assert finding.confidence == 0.0
