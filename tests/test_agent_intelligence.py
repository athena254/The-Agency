"""Tests for per-agent intelligence profiles and agent memory isolation.

Covers the profile loader (template resolution, private override, fail-closed
frontmatter, path safety), the prompt block renderer, executor prompt
injection (fail-open), and the agent_id memory-isolation guarantees of the
agent execution write/query paths (AIS spec §10.3).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from agency.agents.executor import AgentExecutor, ExecutionStatus
from agency.agents.intelligence import (
    AgentProfile,
    ProfileError,
    load_agent_profile,
    profile_prompt_block,
)
from agency.memory.sms.models import MemoryItem
from agency.memory.sms.scope import PrincipalMemoryScope, PrincipalScope
from agency.memory.sms.store import MemoryStore
from agency.orchestrator import AgencyOrchestrator
from agency.tools.base import ToolContext
from agency.tools.builtin.memory import MemoryQueryTool, MemoryWriteTool

PROFILE_HEADER = "Agent profile (descriptive context"


def _write_private_profile(root: Path, agent_id: str, text: str) -> None:
    directory = root / "intelligence" / "agents" / agent_id
    directory.mkdir(parents=True)
    (directory / "AGENT.md").write_text(text, encoding="utf-8")


def _make_link(link: Path, target: Path) -> None:
    """Create ``link`` pointing at ``target`` via symlink or Windows junction.

    File/directory symlinks need a privilege on Windows; junctions do not,
    and ``Path.resolve()`` follows them identically — so a junction fallback
    keeps the path-safety tests runnable on stock Windows machines.
    """
    try:
        link.symlink_to(target)
        return
    except OSError:
        pass
    if sys.platform != "win32":
        pytest.skip("symlink creation unsupported on this platform")
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, OSError):
        pytest.skip("symlink and junction creation unsupported on this platform")


# ------------------------------------------------------------------ #
# Profile loader
# ------------------------------------------------------------------ #


def test_template_profile_loads(tmp_path: Path) -> None:
    profile = load_agent_profile("agent-x", private_root=tmp_path)
    assert profile.agent_id == "agent-x"
    assert profile.source == "template"
    # The committed template carries every section.
    assert profile.role
    assert profile.mission
    assert profile.responsibilities
    assert profile.constraints
    assert profile.communication
    assert profile.memory_policy


def test_private_override_wins(tmp_path: Path) -> None:
    _write_private_profile(
        tmp_path,
        "agent-p",
        "---\ntype: AGENT\nschema_version: 1\n---\n\n## Role\nLedger keeper\n",
    )
    profile = load_agent_profile("agent-p", private_root=tmp_path)
    assert profile.source == "private"
    assert profile.role == "Ledger keeper"


def test_private_missing_sections_degrade_to_empty(tmp_path: Path) -> None:
    _write_private_profile(
        tmp_path,
        "agent-s",
        "---\ntype: AGENT\nschema_version: 1\n---\n\n## Mission\nJust mission.\n",
    )
    profile = load_agent_profile("agent-s", private_root=tmp_path)
    assert profile.mission == "Just mission."
    assert profile.role == ""
    assert profile.constraints == ""


@pytest.mark.parametrize(
    ("frontmatter", "body"),
    [
        ("---\ntype: NOTAGENT\nschema_version: 1\n---\n", "## Role\nx\n"),
        ("---\ntype: AGENT\nschema_version: 2\n---\n", "## Role\nx\n"),
        ("", "## Role\nx\n"),
    ],
)
def test_invalid_frontmatter_fails_closed(tmp_path: Path, frontmatter: str, body: str) -> None:
    _write_private_profile(tmp_path, "agent-bad", frontmatter + body)
    with pytest.raises(ProfileError):
        load_agent_profile("agent-bad", private_root=tmp_path)


def test_invalid_private_frontmatter_does_not_fall_back(tmp_path: Path) -> None:
    # A corrupt private copy fails closed instead of silently degrading to
    # the template (mirrors the butler document loader).
    _write_private_profile(tmp_path, "agent-y", "---\ntype: OTHER\nschema_version: 1\n---\n")
    with pytest.raises(ProfileError):
        load_agent_profile("agent-y", private_root=tmp_path)


def test_missing_both_roots_raises(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="missing from both"):
        load_agent_profile("ghost-agent", private_root=tmp_path, template_root=tmp_path / "nope")


@pytest.mark.parametrize(
    "agent_id", ["../evil", "a/b", "..", ".", "", "UPPER-CASE", "with_underscore", "a\\b"]
)
def test_traversal_and_unsafe_ids_rejected(tmp_path: Path, agent_id: str) -> None:
    with pytest.raises(ProfileError, match="invalid agent id"):
        load_agent_profile(agent_id, private_root=tmp_path)


def test_escape_from_private_root_rejected(tmp_path: Path) -> None:
    # A valid-looking id whose resolved path escapes the root is rejected
    # (symlink/junction escape; the prefix check fails closed).
    outside = tmp_path / "outside"
    _write_private_profile(outside, "escaped", "---\ntype: AGENT\nschema_version: 1\n---\n")
    root = tmp_path / "root"
    (root / "intelligence" / "agents").mkdir(parents=True)
    _make_link(
        root / "intelligence" / "agents" / "escaped",
        outside / "intelligence" / "agents" / "escaped",
    )
    with pytest.raises(ProfileError, match="escapes the private data root"):
        load_agent_profile("escaped", private_root=root)


# ------------------------------------------------------------------ #
# Prompt block
# ------------------------------------------------------------------ #


def test_prompt_block_contains_sections_and_non_authority_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENCY_PRIVATE_DATA_DIR", str(tmp_path / "empty"))
    profile = load_agent_profile("agent-x")
    block = profile_prompt_block(profile)
    assert block.startswith(PROFILE_HEADER)
    assert "never from this text" in block
    for label in ("Role:", "Mission:", "Responsibilities:", "Constraints:", "Memory policy:"):
        assert label in block


def test_prompt_block_collapses_whitespace_and_skips_empty() -> None:
    profile = AgentProfile(
        agent_id="a",
        role="One\n\ntwo   three",
        mission="",
        memory_policy="never   guess ids",
    )
    block = profile_prompt_block(profile)
    assert "Role: One two three" in block
    assert "Memory policy: never guess ids" in block
    assert "Mission:" not in block
    assert "Communication:" not in block


# ------------------------------------------------------------------ #
# Executor injection (fail-open)
# ------------------------------------------------------------------ #


class RecordingLLM:
    def __init__(self, reply: str = "OK") -> None:
        self.prompts: list[str] = []
        self._reply = reply

    def __call__(self, prompt: str, context: dict[str, Any]) -> str:
        self.prompts.append(prompt)
        return self._reply


@pytest.mark.asyncio
async def test_executor_prepends_profile_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No private copy for this id: the committed template is injected.
    monkeypatch.setenv("AGENCY_PRIVATE_DATA_DIR", str(tmp_path / "empty"))
    llm = RecordingLLM()
    executor = AgentExecutor(llm=llm)
    result = await executor.execute("summarize the ledger", context={"agent_id": "agent-x"})
    assert result.status is ExecutionStatus.COMPLETED
    assert result.output == "OK"
    block, separator, task_text = llm.prompts[0].partition("\n\n")
    assert separator and task_text == "summarize the ledger"
    assert block.startswith(PROFILE_HEADER)
    assert "Role:" in block


@pytest.mark.asyncio
async def test_executor_uses_private_profile_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_private_profile(
        tmp_path, "agent-y", "---\ntype: AGENT\nschema_version: 1\n---\n\n## Role\nLedger keeper\n"
    )
    monkeypatch.setenv("AGENCY_PRIVATE_DATA_DIR", str(tmp_path))
    llm = RecordingLLM()
    executor = AgentExecutor(llm=llm)
    await executor.execute("do the thing", context={"agent_id": "agent-y"})
    assert "Role: Ledger keeper" in llm.prompts[0]
    assert llm.prompts[0].endswith("do the thing")


@pytest.mark.asyncio
async def test_executor_runs_unprofiled_on_profile_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_private_profile(
        tmp_path, "agent-bad", "---\ntype: OTHER\nschema_version: 1\n---\n\n## Role\nx\n"
    )
    monkeypatch.setenv("AGENCY_PRIVATE_DATA_DIR", str(tmp_path))
    llm = RecordingLLM()
    executor = AgentExecutor(llm=llm)
    with capture_logs() as logs:
        result = await executor.execute("plain task", context={"agent_id": "agent-bad"})
    assert result.status is ExecutionStatus.COMPLETED
    assert result.output == "OK"
    # Fail-open: the task prompt reached the LLM untouched.
    assert llm.prompts[0] == "plain task"
    assert any(
        event.get("event") == "executor.profile_unavailable"
        and event.get("agent_id") == "agent-bad"
        for event in logs
    )


@pytest.mark.asyncio
async def test_executor_without_agent_id_skips_profile() -> None:
    llm = RecordingLLM()
    executor = AgentExecutor(llm=llm)
    await executor.execute("raw task")
    assert llm.prompts[0] == "raw task"


@pytest.mark.asyncio
async def test_orchestrator_pipeline_still_completes_with_injection(tmp_path: Path) -> None:
    # End-to-end: registered agent ids resolve to the committed template and
    # the classic pipeline (echo LLM) completes with the profile prepended.
    orch = AgencyOrchestrator(memory_db_path=":memory:")
    await orch.start()
    try:
        agent = await orch.register_agent(name="probe", domain="general", capabilities=["test"])
        task = await orch.submit_task(
            title="Memory isolation probe",
            description="prove the pipeline runs with profile injection",
            agent_id=agent.id,
        )
        result = await orch.execute_task(task.task_id)
        assert result.status.value == "completed"
    finally:
        await orch.stop()


# ------------------------------------------------------------------ #
# Memory isolation (AIS spec §10.3)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_memory_writes_bind_executing_agent_id() -> None:
    store = MemoryStore(":memory:")
    tool = MemoryWriteTool()
    ctx = ToolContext(agent_id="agent-a", task_id="t1", memory_store=store)
    result = await tool.run({"content": "alpha secret: rotation is weekly"}, ctx)
    assert result.ok
    items = await store.list_by_agent("agent-a")
    assert len(items) == 1
    assert items[0].agent_id == "agent-a"
    assert "alpha secret" in items[0].content


@pytest.mark.asyncio
async def test_tool_context_binds_agent_id_from_orchestrator() -> None:
    # The executor's tool seam: tool calls during a task run carry the
    # resolved executing agent's id, never a caller-supplied one.
    orch = AgencyOrchestrator(memory_db_path=":memory:")
    ctx = orch._tool_context("agent-a", "task-1")
    assert ctx.agent_id == "agent-a"
    assert ctx.memory_store is orch._memory_store


@pytest.mark.asyncio
async def test_agent_a_items_never_returned_for_agent_b() -> None:
    store = MemoryStore(":memory:")
    tool = MemoryWriteTool()
    for agent_id, content in (("agent-a", "alpha note"), ("agent-b", "beta note")):
        ctx = ToolContext(agent_id=agent_id, task_id="t", memory_store=store)
        assert (await tool.run({"content": content}, ctx)).ok
    a_items = await store.list_by_agent("agent-a")
    b_items = await store.list_by_agent("agent-b")
    assert [i.content for i in a_items] == ["alpha note"]
    assert [i.content for i in b_items] == ["beta note"]


@pytest.mark.asyncio
async def test_legacy_query_tool_rejects_guessed_agent_id() -> None:
    store = MemoryStore(":memory:")
    await store.store(MemoryItem(agent_id="agent-a", content="alpha secret"))
    query = MemoryQueryTool()
    # Agent B tries to enumerate or search agent A's items by guessing the id.
    for args in ({"agent_id": "agent-a"}, {"agent_id": "agent-a", "query": "alpha"}):
        result = await query.run(
            args, ToolContext(agent_id="agent-b", task_id="t", memory_store=store)
        )
        assert not result.ok
        assert result.error is not None and "agent_id is not permitted" in result.error


@pytest.mark.asyncio
async def test_scoped_memory_is_principal_bound() -> None:
    store = MemoryStore(":memory:")
    principal_a = PrincipalScope(owner_id="owner", transport="local", subject_id="alice")
    principal_b = PrincipalScope(owner_id="owner", transport="local", subject_id="bob")
    assert principal_a.agent_id != principal_b.agent_id
    scope_a = PrincipalMemoryScope(store, principal_a)
    scope_b = PrincipalMemoryScope(store, principal_b)
    await scope_a.write("alpha secret: rotation is weekly")
    # Bob's scope cannot reach Alice's item, even with the exact text.
    assert await scope_b.query("alpha secret") == []
    assert await scope_b.list_memories() == []
    found = await scope_a.query("alpha secret")
    assert len(found) == 1 and found[0].agent_id == principal_a.agent_id


@pytest.mark.asyncio
async def test_scoped_write_ignores_spoofed_agent_id() -> None:
    store = MemoryStore(":memory:")
    principal = PrincipalScope(owner_id="owner", transport="local", subject_id="carol")
    scope = PrincipalMemoryScope(store, principal)
    tool = MemoryWriteTool(scoped=True)
    ctx = ToolContext(agent_id="spoofed-id", task_id="t", memory_scope=scope)
    result = await tool.run({"content": "carol note"}, ctx)
    assert result.ok
    stored = await store.list_by_agent(principal.agent_id)
    assert len(stored) == 1 and stored[0].content == "carol note"
    assert await store.list_by_agent("spoofed-id") == []


@pytest.mark.asyncio
async def test_scoped_query_tool_rejects_guessed_agent_id() -> None:
    store = MemoryStore(":memory:")
    principal_a = PrincipalScope(owner_id="owner", transport="local", subject_id="alice")
    principal_b = PrincipalScope(owner_id="owner", transport="local", subject_id="bob")
    scope_b = PrincipalMemoryScope(store, principal_b)
    query = MemoryQueryTool(scoped=True)
    result = await query.run(
        {"agent_id": principal_a.agent_id},
        ToolContext(agent_id=principal_b.agent_id, task_id="t", memory_scope=scope_b),
    )
    assert not result.ok
    assert result.error is not None and "principal-bound" in result.error
