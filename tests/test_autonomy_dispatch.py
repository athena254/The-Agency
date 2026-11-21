"""Gap #20: engine assignments materialise as delegated child tasks.

Covers the general agent->agent leg: ``run_autonomous`` assigns, then
``dispatch_assignments`` dispatches each assigned subtask through a delegate
(the orchestrator binds ``delegate_task``), records the durable lifecycle in
the A5 assignment journal, and never re-dispatches progressed records.
"""

from __future__ import annotations

import pytest

from agency.agents.planner import AgentPlanner, Subtask, SubtaskSpec
from agency.agents.registry import AgentRegistry
from agency.autonomy.assignment_journal import AssignmentJournal
from agency.autonomy.engine import (
    DispatchOutcome,
    RunResult,
    _journal_id,
    dispatch_assignments,
    run_autonomous,
)
from agency.autonomy.proof import verify_chain
from agency.kernel.identity import Agent, Capability
from agency.kernel.tasks import TaskStatus
from agency.orchestrator import AgencyOrchestrator


def _strategy(_task: str) -> list[SubtaskSpec]:
    return [
        SubtaskSpec(title="research step", required_capabilities=["research"]),
        SubtaskSpec(title="plain step", required_capabilities=[]),
    ]


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(
        Agent(id="a1", name="a1", domain="general", capabilities=[Capability(name="research")])
    )
    return reg


def _engine_run() -> tuple[AgentPlanner, RunResult]:
    reg = _registry()
    planner = AgentPlanner(reg, strategy=_strategy)
    result = run_autonomous("Step 1: research. Step 2: plain.", reg, planner, available_tools=set())
    return planner, result


class _Recorder:
    """Fake delegate: records calls, returns scripted outcomes."""

    def __init__(self, outcomes: list[DispatchOutcome]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, subtask: Subtask, agent_id: str) -> DispatchOutcome:
        self.calls.append((subtask.title, agent_id))
        if self.outcomes:
            return self.outcomes.pop(0)
        return DispatchOutcome(status="completed", child_task_id="child-x", detail="ok")


def _dispatch_actions(result: RunResult) -> list[str]:
    return [e.action for e in result.proof.events if e.actor == "dispatch"]


@pytest.mark.asyncio
async def test_dispatch_materialises_children_and_journals_terminal() -> None:
    planner, result = _engine_run()
    graph = planner.get_plan(result.plan_id)
    assert graph is not None
    targets = [s.assigned_agent_id for s in graph.subtasks]
    assert all(targets)
    delegate = _Recorder(
        [
            DispatchOutcome(status="completed", child_task_id="child-1", detail="done"),
            DispatchOutcome(status="completed", child_task_id="child-2", detail="done"),
        ]
    )
    with AssignmentJournal() as journal:
        dispatched = await dispatch_assignments(result, planner, delegate=delegate, journal=journal)
        assert [d["status"] for d in dispatched.dispatches] == ["completed", "completed"]
        assert [d["child_task_id"] for d in dispatched.dispatches] == ["child-1", "child-2"]
        assert [d["agent_id"] for d in dispatched.dispatches] == targets
        assert all(d["assignment_id"] for d in dispatched.dispatches)
        # The delegate saw one call per assigned subtask, in plan order.
        assert len(delegate.calls) == 2
        # Journal: every assignment reached COMPLETED with a result digest.
        for d in dispatched.dispatches:
            record = journal.get(str(d["assignment_id"]))
            assert record.state == "COMPLETED"
            assert record.result_digest is not None
        # Outbox recorded the full lifecycle, proof chain still verifies.
        states = [e.state for e in journal.outbox()]
        assert states.count("ASSIGNED") == 2 and states.count("COMPLETED") == 2
        assert _dispatch_actions(dispatched) == ["dispatched", "dispatched"]
        assert verify_chain(dispatched.proof.events) is True


@pytest.mark.asyncio
async def test_dispatch_failed_and_uncertain_recorded_honestly() -> None:
    planner, result = _engine_run()
    delegate = _Recorder(
        [
            DispatchOutcome(status="failed", child_task_id=None, detail="unknown target agent"),
            DispatchOutcome(status="uncertain", child_task_id="child-2", detail="wait timed out"),
        ]
    )
    with AssignmentJournal() as journal:
        dispatched = await dispatch_assignments(result, planner, delegate=delegate, journal=journal)
        assert [d["status"] for d in dispatched.dispatches] == ["failed", "uncertain"]
        failed = journal.get(str(dispatched.dispatches[0]["assignment_id"]))
        assert failed.state == "FAILED"
        uncertain = journal.get(str(dispatched.dispatches[1]["assignment_id"]))
        assert uncertain.state == "UNCERTAIN"  # never auto-resumed
        assert _dispatch_actions(dispatched) == ["dispatch_failed", "dispatch_uncertain"]
        # A second pass must not re-dispatch progressed (e.g. UNCERTAIN) records.
        again = _Recorder([])
        redone = await dispatch_assignments(dispatched, planner, delegate=again, journal=journal)
        assert again.calls == []
        assert [d["status"] for d in redone.dispatches] == ["skipped", "skipped"]
        # The proof bundle is shared, so the second pass appends after pass one.
        assert _dispatch_actions(redone)[-2:] == ["dispatch_skipped", "dispatch_skipped"]


@pytest.mark.asyncio
async def test_delegate_exception_maps_to_uncertain() -> None:
    planner, result = _engine_run()

    async def broken(subtask: Subtask, agent_id: str) -> DispatchOutcome:
        raise RuntimeError("delegate exploded")

    with AssignmentJournal() as journal:
        dispatched = await dispatch_assignments(result, planner, delegate=broken, journal=journal)
        assert all(d["status"] == "uncertain" for d in dispatched.dispatches)
        for d in dispatched.dispatches:
            assert journal.get(str(d["assignment_id"])).state == "UNCERTAIN"


@pytest.mark.asyncio
async def test_dispatch_without_journal_still_records_proof() -> None:
    planner, result = _engine_run()
    delegate = _Recorder([])
    dispatched = await dispatch_assignments(result, planner, delegate=delegate)
    assert [d["status"] for d in dispatched.dispatches] == ["completed", "completed"]
    assert all(d["assignment_id"] is None for d in dispatched.dispatches)
    assert _dispatch_actions(dispatched) == ["dispatched", "dispatched"]
    assert verify_chain(dispatched.proof.events) is True


@pytest.mark.asyncio
async def test_journal_ids_follow_journal_scheme() -> None:
    planner, result = _engine_run()
    graph = planner.get_plan(result.plan_id)
    assert graph is not None
    sub = graph.subtasks[0]
    assignment_id = _journal_id("assign", result.plan_id, sub.subtask_id)
    delegate = _Recorder(
        [DispatchOutcome(status="completed", child_task_id="child-1", detail="ok")]
    )
    with AssignmentJournal() as journal:
        await dispatch_assignments(result, planner, delegate=delegate, journal=journal)
        stored = journal.get(assignment_id)
        assert stored.task_id.startswith("task_")
        assert stored.worker_id.startswith("worker_")


@pytest.mark.asyncio
async def test_submit_autonomous_task_dispatches_children(tmp_path) -> None:
    orch = AgencyOrchestrator(
        config={"assignment_journal_path": str(tmp_path / "journal.db")},
        memory_db_path=str(tmp_path / "memory.db"),
    )
    await orch.start()
    await orch.start_dispatcher()
    try:
        await orch.register_agent("researcher", "general", ["research"])
        task_text = "Step 1: research things. Step 2: wrap up."
        result = await orch.submit_autonomous_task(task_text)
        assert result.dispatches
        for d in result.dispatches:
            assert d["status"] == "completed", d
            child = await orch.get_task(str(d["child_task_id"]))
            assert child.status is TaskStatus.COMPLETED
            assert child.parent_id
            assert child.created_by == d["agent_id"]
            parent = await orch.get_task(child.parent_id)
            assert parent.title == task_text[:80]
        journal = orch._assignment_journal
        assert journal is not None
        for d in result.dispatches:
            record = journal.get(str(d["assignment_id"]))
            assert record.state == "COMPLETED"
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_submit_autonomous_task_without_dispatcher_fails_honestly(tmp_path) -> None:
    orch = AgencyOrchestrator(memory_db_path=str(tmp_path / "memory.db"))
    await orch.start()
    try:
        await orch.register_agent("researcher", "general", ["research"])
        result = await orch.submit_autonomous_task("Step 1: research things.")
        assert result.dispatches
        assert all(d["status"] == "failed" for d in result.dispatches)
        assert all(d["child_task_id"] is None for d in result.dispatches)
        assert "no dispatcher" in str(result.dispatches[0]["detail"])
    finally:
        await orch.stop()
