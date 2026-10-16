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
        (orch._executor, "execute", True),
        (orch._tool_driver, "run", True),
        (orch._verifier, "verify", False),
        (orch._risk_engine, "assess", False),
        (orch._evidence_store, "add_finding", True),
        (orch._memory_store, "store", True),
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
@pytest.mark.parametrize(
    "mode", ["provider", "timeout", "none", "blank", "tool_failure", "tool_blank"]
)
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
        monkeypatch.setattr(
            orch._executor,
            "execute",
            AsyncMock(
                return_value=ExecutionResult(
                    task_id=task.task_id,
                    status=ExecutionStatus.TIMEOUT,
                )
            ),
        )
    else:
        model.output = (
            None if mode == "none" else '{"final":"  "}' if mode == "tool_blank" else " \n\t"
        )
    probes = []
    for obj, name, async_call in (
        (orch._verifier, "verify", False),
        (orch._risk_engine, "assess", False),
        (orch._evidence_store, "add_finding", True),
        (orch._memory_store, "store", True),
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
@pytest.mark.parametrize(
    "stage", ["verifier", "risk", "evidence", "audit", "memory", "report", "cancel"]
)
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


def _tool_report():
    from agency.agents.assessment import build_assessment, unchecked_subtask

    return build_assessment(
        " fixture-id ",
        execution_status="completed",
        output="body",
        subtasks=(
            unchecked_subtask(
                "tool_loop", path="tool", execution_status="completed", output="body"
            ),
        ),
    )


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), "a2.v2"),
        (("extra",), "private"),
        (("task_id",), ""),
        (("task_id",), 12),
        (("execution_status",), "running"),
        (("output_sha256",), "A" * 64),
        (("output_sha256",), "0" * 64 + "\n"),
        (("verification",), "passed"),
        (("risk_status",), "assessed"),
        (("independently_verified",), True),
        (("independently_verified",), 0),
        (("subtasks",), []),
        (("subtasks", 0, "checks"), []),
        (("subtasks", 0, "verification"), "passed"),
        (("subtasks", 0, "subtask_id"), "other"),
        (("subtasks", 0, "subtask_id"), ""),
        (("subtasks", 0, "output_sha256"), "not-a-digest"),
        (("subtasks", 0, "checks", 0, "method"), "new_method"),
        (("subtasks", 0, "checks", 0, "reason"), "heuristic_passed"),
        (("subtasks", 0, "checks", 0, "status"), "needs_review"),
        (("subtasks", 0, "checks", 0, "score"), 0.0),
        (("subtasks", 0, "checks", 1, "criterion"), "completeness"),
        (("subtasks", 0, "checks", 1, "reason"), "no_trusted_accuracy_oracle"),
        (("subtasks", 0, "risk", "category"), "none"),
        (("subtasks", 0, "risk", "category"), "low"),
        (("subtasks", 0, "risk", "method"), "legacy_risk_engine/v1"),
        (("subtasks", 0, "risk", "finding_id"), "fabricated"),
    ],
)
def test_report_schema_rejects_unknown_fields_and_contradictory_snapshots(path, value):
    from pydantic import ValidationError

    from agency.agents.assessment import ExecutionAssessment

    payload = _tool_report().model_dump(mode="json")
    cursor = payload
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(ValidationError):
        ExecutionAssessment.model_validate(payload)


@pytest.mark.parametrize("score", [True, False, float("nan"), float("inf"), -0.01, 1.01, "0.5"])
def test_performed_check_rejects_nonfinite_boolean_coerced_or_out_of_range_scores(score):
    from pydantic import ValidationError

    from agency.agents.assessment import CheckAssessment

    with pytest.raises(ValidationError):
        CheckAssessment(
            criterion="completeness",
            status="passed",
            method="legacy_keyword_coverage/v1",
            reason="heuristic_passed",
            score=score,
        )


def test_report_is_recursively_frozen_and_json_round_trips_without_normalizing_ids():
    from pydantic import ValidationError

    from agency.agents.assessment import ExecutionAssessment

    report = _tool_report()
    assert ExecutionAssessment.model_validate_json(report.model_dump_json()) == report
    assert report.task_id == " fixture-id "
    for obj, field, value in (
        (report, "task_id", "changed"),
        (report.subtasks[0], "path", "classic"),
        (report.subtasks[0].checks[0], "score", 1.0),
        (report.subtasks[0].risk, "category", "low"),
    ):
        with pytest.raises(ValidationError):
            setattr(obj, field, value)
    assert isinstance(report.subtasks, tuple)
    assert isinstance(report.subtasks[0].checks, tuple)


@pytest.mark.parametrize(
    "output", [None, {"output": "value", "ground_truth": "value"}, "  café 🙂\r\n\n ", 0, False]
)
def test_binding_preserves_exact_rendered_bytes_and_downgrades_changed_results(output):
    from agency.agents import assessment as a

    rendered = "" if output is None else output if isinstance(output, str) else str(output)
    subtask = a.unchecked_subtask(
        "isolated", path="isolated", execution_status="failed", output=output
    )
    report = a.build_assessment("ID", execution_status="failed", output=output, subtasks=(subtask,))
    assert a.render_output(output) == rendered
    assert a.output_digest(output) == hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    assert (
        a.bound_assessment(report, task_id="ID", execution_status="failed", output=output) == report
    )
    for tid, status, body in (
        (" ID", "failed", output),
        ("ID", "completed", output),
        ("ID", "failed", rendered + " "),
    ):
        assert a.bound_assessment(report, task_id=tid, execution_status=status, output=body) is None
    assert (
        a.bound_assessment(
            report.model_dump(), task_id="ID", execution_status="failed", output=output
        )
        is None
    )
    malformed = report.model_copy(update={"verification": "passed"})
    assert (
        a.bound_assessment(malformed, task_id="ID", execution_status="failed", output=output)
        is None
    )
    assert "unavailable" in a.assessment_notice(None)
    assert "unavailable" in a.assessed_output(
        output, assessment=report, task_id="other", execution_status="failed"
    )


def test_invalid_utf8_is_not_replaced_or_given_an_invented_digest():
    from agency.agents.assessment import output_digest

    with pytest.raises(UnicodeEncodeError):
        output_digest("\ud800")


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["completeness accuracy safety", "incomplete fixture"])
async def test_mixed_classic_failure_keeps_checks_and_risk_coverage_without_success_memory(
    runtime, monkeypatch, first
):
    from pydantic import ValidationError

    from agency.agents.assessment import ExecutionAssessment
    from agency.agents.executor import ExecutionResult, ExecutionStatus
    from agency.agents.planner import Subtask, TaskGraph

    orch, _ = runtime
    agent = await orch.register_agent("fixture", "security", ["respond"])
    task = await orch.submit_task("Fixture", "Summarize fixture.", agent.id)
    graph = TaskGraph(
        task="fixture",
        subtasks=[Subtask(subtask_id="a", title="a"), Subtask(subtask_id="b", title="b")],
    )
    monkeypatch.setattr(orch._planner, "plan", Mock(return_value=graph))
    monkeypatch.setattr(
        orch._executor,
        "execute",
        AsyncMock(
            side_effect=[
                ExecutionResult(
                    task_id=task.task_id, status=ExecutionStatus.COMPLETED, output=first
                ),
                ExecutionResult(
                    task_id=task.task_id,
                    status=ExecutionStatus.FAILED,
                    output="second failure body",
                ),
            ]
        ),
    )
    memory = AsyncMock(wraps=orch._memory_store.store)
    monkeypatch.setattr(orch._memory_store, "store", memory)
    result = await orch.execute_task(task.task_id)
    assert result.status.value == task.status.value == "failed"
    assert result.output == first + "\n\nsecond failure body"
    assert result.assessment.output_sha256 == hashlib.sha256(result.output.encode()).hexdigest()
    assert [s.subtask_id for s in result.assessment.subtasks] == ["a", "b"]
    assert result.assessment.verification == (
        "needs_review" if first.startswith("completeness") else "failed"
    )
    assert result.assessment.risk_status == "partially_assessed"
    assert len(await orch._evidence_store.list_findings()) == 1
    memory.assert_not_called()
    data = result.assessment.model_dump(mode="json")
    data["execution_status"] = "completed"
    with pytest.raises(ValidationError):
        ExecutionAssessment.model_validate(data)


@pytest.mark.asyncio
async def test_memory_records_check_status_while_task_is_running(runtime, monkeypatch):
    orch, _ = runtime
    agent = await orch.register_agent("fixture", "general", ["respond"])
    task = await orch.submit_task("Fixture", "Summarize fixture.", agent.id)
    original = orch._memory_store.store
    entries = []

    async def store(item):
        assert task.status.value == "running"
        entries.append(item)
        return await original(item)

    monkeypatch.setattr(orch._memory_store, "store", store)
    await orch.execute_task(task.task_id)
    assert task.status.value == "completed"
    assert len(entries) == 1
    assert "execution=completed; checks=not_performed; risk=not_assessed" in entries[0].content
    assert "independently_verified=false" in entries[0].content


@pytest.mark.parametrize(
    "criterion,status,score",
    [
        ("completeness", "passed", 0.0),
        ("completeness", "failed", 1.0),
        ("safety", "passed", 0.0),
        ("safety", "failed", 1.0),
    ],
)
def test_heuristic_score_must_agree_with_the_named_legacy_method(criterion, status, score):
    from pydantic import ValidationError

    from agency.agents.assessment import CheckAssessment

    with pytest.raises(ValidationError):
        CheckAssessment(
            criterion=criterion,
            status=status,
            score=score,
            reason="heuristic_" + status,
            method="legacy_keyword_coverage/v1"
            if criterion == "completeness"
            else "legacy_denylist/v1",
        )


@pytest.mark.asyncio
async def test_cancellation_during_actual_model_call_marks_task_failed(runtime, monkeypatch):
    import asyncio

    orch, model = runtime
    agent = await orch.register_agent("fixture", "general", ["respond"])
    task = await orch.submit_task("Fixture", "Summarize fixture.", agent.id)
    entered = asyncio.Event()

    async def wait_model(*args, **kwargs):
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(model, "generate", wait_model)
    running = asyncio.create_task(orch.execute_task(task.task_id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert task.status.value == "failed"
        assert await orch._evidence_store.list_findings() == []
    finally:
        if not running.done():
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
