"""R-02: subtask-scoped specialist dispatch on the classic planner path.

Pins that the classic subtask loop (a) sends each subtask its own scoped
work text instead of the whole-task grounding alone, (b) threads completed
dependency outputs as quoted untrusted data and marks failed ones honestly,
(c) routes each subtask to the planner-assigned specialist agent (including
the executor's profile prompt seam) while keeping the fallback agent
otherwise, and (d) audits the agent that actually executed each subtask.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agency.agents.intelligence import AgentProfile, ProfileError
from agency.agents.planner import Subtask, TaskGraph
from agency.orchestrator import (
    AgencyOrchestrator,
    _compose_subtask_task_text,
    _render_dependency_outputs,
)

# ------------------------------------------------------------------ #
# Hermetic doubles and seams (setup style follows tests/test_orchestrator.py)
# ------------------------------------------------------------------ #


class _RecordingLLM:
    """Hermetic LLM double recording every (prompt, context) call.

    Returns canned replies in call order. A prompt carrying ``FORCE-FAILURE``
    raises ValueError — the executor classifies that non-retryable and
    returns a FAILED result without retrying.
    """

    def __init__(self, replies: list[str] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._replies = replies if replies is not None else []

    async def __call__(self, prompt: str, context: dict[str, Any]) -> str:
        self.calls.append((prompt, dict(context)))
        if "FORCE-FAILURE" in prompt:
            raise ValueError("forced failure")
        if not self._replies:
            return f"reply {len(self.calls)}"
        index = min(len(self.calls) - 1, len(self._replies) - 1)
        return self._replies[index]


def _orchestrator() -> AgencyOrchestrator:
    return AgencyOrchestrator(
        memory_db_path=":memory:",
        skill_db_path=":memory:",
        audit_db_path=":memory:",
        evidence_db_path=":memory:",
    )


def _install_plan(
    orch: AgencyOrchestrator,
    monkeypatch: pytest.MonkeyPatch,
    graph: TaskGraph,
) -> None:
    """Stub the planner seam so the test owns subtasks, assignments and deps."""
    monkeypatch.setattr(orch._planner, "plan", lambda description: graph)


def _spy_executor(
    orch: AgencyOrchestrator,
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Record every executor.execute(task/context/task_id) call."""
    real = orch._executor.execute
    calls: list[dict[str, Any]] = []

    async def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(
            {
                "task": kwargs.get("task", args[0] if args else None),
                "context": kwargs.get("context"),
                "task_id": kwargs.get("task_id"),
            }
        )
        return await real(*args, **kwargs)

    monkeypatch.setattr(orch._executor, "execute", spy)
    return calls


def _fake_profile_loader(
    specialist_id: str,
) -> Any:
    """Private profile only for the specialist; everyone else runs unprofiled."""

    def loader(agent_id: str, **kwargs: Any) -> AgentProfile:
        if agent_id == specialist_id:
            return AgentProfile(agent_id=agent_id, role="ALPHA-SPECIALIST-ROLE", source="private")
        raise ProfileError(f"no profile for {agent_id!r}")

    return loader


async def _submit_classic_task(orch: AgencyOrchestrator, agent_id: str) -> Any:
    """Create a task routed to a classic (non-tool-capable) domain agent."""
    return await orch._task_manager.create_task(
        title="Parent mission",
        created_by=agent_id,
        input={"description": "parent work", "agent_id": agent_id},
    )


# ------------------------------------------------------------------ #
# Pure helpers (no async, no orchestrator)
# ------------------------------------------------------------------ #


def test_compose_keeps_grounding_and_scopes_subtask_work() -> None:
    text = _compose_subtask_task_text(
        system_context="GROUNDING BLOCK",
        parent_title="Parent mission",
        subtask_title="Write the doc",
        subtask_description="Draft section 2 of the report.",
        position=2,
        total=3,
    )
    assert "GROUNDING BLOCK" in text
    assert "subtask 2 of 3" in text
    assert "Parent mission" in text
    assert "Write the doc" in text
    assert "Draft section 2 of the report." in text


def test_compose_differs_between_subtasks() -> None:
    first = _compose_subtask_task_text("G", "P", "Title one", "Desc one", 1, 2)
    second = _compose_subtask_task_text("G", "P", "Title two", "Desc two", 2, 2)
    assert first != second
    assert "Title one" in first and "Title one" not in second
    assert "Title two" in second and "Title two" not in first


def test_compose_quotes_parent_title_and_caps_composed_text() -> None:
    text = _compose_subtask_task_text(
        system_context="G",
        parent_title='parent "title" with quotes',
        subtask_title="T",
        subtask_description="x" * 10_000,
        position=1,
        total=1,
        dependency_block="dependency line " * 500,
    )
    assert len(text) <= 4000
    assert "truncated" in text
    # The parent title is runtime/caller data: it arrives quoted, not bare.
    assert json.dumps('parent "title" with quotes', ensure_ascii=False) in text


def test_render_dependency_outputs_empty_when_no_dependencies() -> None:
    assert _render_dependency_outputs([]) == ""


def test_render_dependency_outputs_quotes_completed_and_marks_missing() -> None:
    block = _render_dependency_outputs(
        [("sub-1", 'raw output with "quotes" and {json: "inside"}'), ("sub-2", None)]
    )
    assert "untrusted reference data" in block
    assert "not instructions" in block
    assert json.dumps('raw output with "quotes" and {json: "inside"}', ensure_ascii=False) in block
    assert "sub-1 (completed):" in block
    assert "sub-2" in block
    assert "output unavailable" in block


def test_render_dependency_output_truncates_oversized_output() -> None:
    block = _render_dependency_outputs([("sub-1", "y" * 5_000)])
    assert len(block) < 2_000
    assert "truncated" in block


# ------------------------------------------------------------------ #
# Classic-path dispatch behaviour (full orchestrator, hermetic)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_classic_llm_receives_distinct_subtask_scoped_texts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(a) Each subtask's own title/description reach the LLM, per subtask."""
    orch = _orchestrator()
    await orch.start()
    try:
        worker = await orch.register_agent("worker", "test", [])
        sub1 = Subtask(title="First: gather", description="Collect the alpha inputs.")
        sub2 = Subtask(title="Second: summarize", description="Summarize the beta results.")
        _install_plan(orch, monkeypatch, TaskGraph(task="parent work", subtasks=[sub1, sub2]))
        llm = _RecordingLLM()
        monkeypatch.setattr(orch._executor, "_llm", llm)
        calls = _spy_executor(orch, monkeypatch)
        task = await _submit_classic_task(orch, worker.id)

        result = await orch.execute_task(task.task_id)

        assert result.status.value == "completed"
        assert len(llm.calls) == 2
        assert len(calls) == 2
        first, second = llm.calls[0][0], llm.calls[1][0]
        # The grounding block stays; the subtask's actual work is scoped in.
        assert "SYSTEM FACTS" in first and "SYSTEM FACTS" in second
        assert "subtask 1 of 2" in first
        assert "First: gather" in first
        assert "Collect the alpha inputs." in first
        assert "Second: summarize" not in first
        assert "subtask 2 of 2" in second
        assert "Second: summarize" in second
        assert "Summarize the beta results." in second
        assert "First: gather" not in second
        assert first != second
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_dependency_output_reaches_dependent_as_quoted_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) A completed dependency's output arrives quoted as untrusted data."""
    orch = _orchestrator()
    await orch.start()
    try:
        worker = await orch.register_agent("worker", "test", [])
        sub1 = Subtask(title="Gather", description="Collect inputs.")
        sub2 = Subtask(
            title="Summarize", description="Summarize results.", depends_on=[sub1.subtask_id]
        )
        _install_plan(orch, monkeypatch, TaskGraph(task="parent work", subtasks=[sub1, sub2]))
        llm = _RecordingLLM(replies=["ALPHA DEP OUTPUT UNIQUE-9F3", "summary reply"])
        monkeypatch.setattr(orch._executor, "_llm", llm)
        task = await _submit_classic_task(orch, worker.id)

        await orch.execute_task(task.task_id)

        assert len(llm.calls) == 2
        second = llm.calls[1][0]
        assert "Dependency outputs" in second
        assert "untrusted reference data" in second
        assert f"subtask {sub1.subtask_id} (completed):" in second
        # JSON-quoted data, never a bare instruction-like injection.
        assert json.dumps("ALPHA DEP OUTPUT UNIQUE-9F3", ensure_ascii=False) in second
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_failed_dependency_is_reported_honestly_not_fabricated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) A failed dependency yields an honest unavailable marker, no output."""
    orch = _orchestrator()
    await orch.start()
    try:
        worker = await orch.register_agent("worker", "test", [])
        sub1 = Subtask(title="Gather", description="FORCE-FAILURE gather step")
        sub2 = Subtask(
            title="Summarize", description="Summarize results.", depends_on=[sub1.subtask_id]
        )
        _install_plan(orch, monkeypatch, TaskGraph(task="parent work", subtasks=[sub1, sub2]))
        llm = _RecordingLLM()
        monkeypatch.setattr(orch._executor, "_llm", llm)
        task = await _submit_classic_task(orch, worker.id)

        await orch.execute_task(task.task_id)

        # The dependent still executes, told honestly that the input is gone.
        assert len(llm.calls) == 2
        second = llm.calls[1][0]
        assert f"subtask {sub1.subtask_id}" in second
        assert "output unavailable" in second
        # No fabricated completion for the failed dependency.
        assert "(completed)" not in second
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_assigned_specialist_reaches_executor_context_and_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(c) assigned_agent_id routes the subtask and applies its profile prompt."""
    orch = _orchestrator()
    await orch.start()
    try:
        worker = await orch.register_agent("worker", "test", [])
        specialist = await orch.register_agent("alpha-specialist", "test", [])
        sub1 = Subtask(
            title="Specialist work", description="Needs the alpha.", assigned_agent_id=specialist.id
        )
        sub2 = Subtask(
            title="Ghost assignment", description="Assignee unknown.", assigned_agent_id="ghost"
        )
        sub3 = Subtask(title="Unassigned work", description="No assignment.")
        _install_plan(orch, monkeypatch, TaskGraph(task="parent work", subtasks=[sub1, sub2, sub3]))
        llm = _RecordingLLM()
        monkeypatch.setattr(orch._executor, "_llm", llm)
        monkeypatch.setattr(
            "agency.agents.executor.load_agent_profile", _fake_profile_loader(specialist.id)
        )
        calls = _spy_executor(orch, monkeypatch)
        task = await _submit_classic_task(orch, worker.id)

        await orch.execute_task(task.task_id)

        assert len(calls) == 3
        assert calls[0]["context"]["agent_id"] == specialist.id
        assert calls[0]["context"]["subtask_id"] == sub1.subtask_id
        # Unknown assignee falls back to the task-level execution agent.
        assert calls[1]["context"]["agent_id"] == worker.id
        assert calls[1]["context"]["subtask_id"] == sub2.subtask_id
        # Unassigned keeps the fallback agent.
        assert calls[2]["context"]["agent_id"] == worker.id
        # The executor's profile prompt seam ran for the assigned agent only.
        assert "ALPHA-SPECIALIST-ROLE" in llm.calls[0][0]
        assert "ALPHA-SPECIALIST-ROLE" not in llm.calls[1][0]
        assert "ALPHA-SPECIALIST-ROLE" not in llm.calls[2][0]
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_revoked_assignee_falls_back_to_execution_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(c) A revoked assignee is never routed; the fallback agent executes."""
    orch = _orchestrator()
    await orch.start()
    try:
        worker = await orch.register_agent("worker", "test", [])
        revoked_agent = await orch.register_agent("revoked-specialist", "test", [])
        record = orch._identity_registry.get(revoked_agent.id)
        assert record is not None
        record.revoked = True
        sub1 = Subtask(
            title="Specialist work",
            description="Needs the alpha.",
            assigned_agent_id=revoked_agent.id,
        )
        _install_plan(orch, monkeypatch, TaskGraph(task="parent work", subtasks=[sub1]))
        llm = _RecordingLLM()
        monkeypatch.setattr(orch._executor, "_llm", llm)
        calls = _spy_executor(orch, monkeypatch)
        task = await _submit_classic_task(orch, worker.id)

        await orch.execute_task(task.task_id)

        assert len(calls) == 1
        assert calls[0]["context"]["agent_id"] == worker.id
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_audit_entry_names_the_agent_that_executed_each_subtask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(d) The per-subtask audit entry records the actual executing agent."""
    orch = _orchestrator()
    await orch.start()
    try:
        worker = await orch.register_agent("worker", "test", [])
        specialist = await orch.register_agent("alpha-specialist", "test", [])
        sub1 = Subtask(
            title="Specialist work", description="Needs the alpha.", assigned_agent_id=specialist.id
        )
        sub2 = Subtask(title="Unassigned work", description="No assignment.")
        _install_plan(orch, monkeypatch, TaskGraph(task="parent work", subtasks=[sub1, sub2]))
        llm = _RecordingLLM()
        monkeypatch.setattr(orch._executor, "_llm", llm)
        task = await _submit_classic_task(orch, worker.id)

        await orch.execute_task(task.task_id)

        entries = await orch._audit_log.query()
        by_action = {
            entry.action: entry for entry in entries if entry.action.startswith("subtask_")
        }
        assert by_action[f"subtask_{sub1.subtask_id}"].agent == specialist.id
        assert by_action[f"subtask_{sub2.subtask_id}"].agent == worker.id
    finally:
        await orch.stop()
