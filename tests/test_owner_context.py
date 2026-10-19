"""Owner-local context uses committed threads, never shared-memory recall."""

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from agency.butler.context import ThreadContextEngine
from agency.butler.threads import ThreadStore
from agency.memory.sms.models import MemoryItem
from agency.memory.sms.store import MemoryStore


@pytest_asyncio.fixture
async def boundary(
    tmp_path: Path,
) -> AsyncIterator[tuple[ThreadStore, MemoryStore, ThreadContextEngine, str]]:
    threads = ThreadStore(str(tmp_path / "threads.db"))
    async with MemoryStore(str(tmp_path / "memory.db")) as memory:
        workspace = threads.create_workspace("owner", "Workspace")
        thread = threads.create_thread("owner", workspace.id, "Conversation")
        try:
            yield threads, memory, ThreadContextEngine(threads, memory, window=2), thread.id
        finally:
            threads.close()


def _observe(monkeypatch, engine, memory):
    """Wrap real methods: count attempts even when best-effort recall swallows errors."""
    calls = {}
    for target, name in (
        (engine, "_topical_recall"),
        (engine._retrieval, "semantic_search"),
        (memory, "search_fts"),
        (memory, "store"),
        (memory, "touch_many"),
        (engine, "record_turn"),
    ):
        spy = AsyncMock(wraps=getattr(target, name))
        monkeypatch.setattr(target, name, spy)
        calls[name] = spy
    return calls


@pytest.mark.asyncio
async def test_local_only_skips_actual_retrieval_of_thread_tagged_shared_memory(
    boundary, monkeypatch
) -> None:
    threads, memory, engine, thread_id = boundary
    threads.append_message("owner", thread_id, "user", "committed question")
    await memory.store(
        MemoryItem(
            agent_id=f"thread-{thread_id}",
            content="postgres legacy secret not committed to this thread",
            tags=["butler-thread", thread_id, "owner"],
        )
    )
    before = await memory.list_all()
    calls = _observe(monkeypatch, engine, memory)
    sql = []
    await memory.connection.set_trace_callback(sql.append)
    try:
        context = await engine.assemble("owner", thread_id, "postgres", local_only=True)
        assert context.topical == ""
        assert context.window == "[user] committed question"
        assert "legacy secret" not in context.render()
        assert {name: spy.call_count for name, spy in calls.items()} == dict.fromkeys(calls, 0)
        assert sql == []
    finally:
        await memory.connection.set_trace_callback(None)
    assert await memory.list_all() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("local_only", [None, False, True])
@pytest.mark.parametrize("missing", [False, True], ids=["foreign", "unknown"])
async def test_owner_is_authorized_before_any_memory_access(
    boundary, monkeypatch, local_only, missing
) -> None:
    threads, memory, engine, thread_id = boundary
    threads.append_message("owner", thread_id, "user", "private committed message")
    calls = _observe(monkeypatch, engine, memory)
    sql = []
    await memory.connection.set_trace_callback(sql.append)
    try:
        options = {} if local_only is None else {"local_only": local_only}
        denied_thread = "does-not-exist" if missing else thread_id
        with pytest.raises(KeyError) as denied:
            await engine.assemble("stranger", denied_thread, "private", **options)
        assert denied.value.args == (denied_thread,)
        assert {name: spy.call_count for name, spy in calls.items()} == dict.fromkeys(calls, 0)
        assert sql == []
    finally:
        await memory.connection.set_trace_callback(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_false", [False, True], ids=["default", "false"])
async def test_legacy_recall_still_executes_real_thread_scoped_retrieval(
    boundary, monkeypatch, explicit_false
) -> None:
    threads, memory, engine, thread_id = boundary
    workspace = threads.get_thread("owner", thread_id).workspace_id
    other = threads.create_thread("owner", workspace, "Other")
    for agent_id, content in (
        (f"thread-{thread_id}", "postgres same thread legacy recall"),
        (f"thread-{other.id}", "postgres other thread secret"),
        ("chat-owner", "postgres sender global secret"),
    ):
        await memory.store(MemoryItem(agent_id=agent_id, content=content))
    calls = _observe(monkeypatch, engine, memory)
    options = {"local_only": False} if explicit_false else {}
    context = await engine.assemble("owner", thread_id, "postgres", **options)
    assert context.topical == "- postgres same thread legacy recall"
    assert context.summary == context.window == ""
    assert calls["_topical_recall"].call_count == 1
    calls["semantic_search"].assert_awaited_once_with(
        "postgres", agent_id=f"thread-{thread_id}", limit=5
    )
    assert calls["search_fts"].call_count == calls["touch_many"].call_count == 1
    assert calls["record_turn"].call_count == calls["store"].call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("summary_cut", [None, 1, 4, 5, 6])
async def test_local_only_preserves_committed_summary_window_boundary(
    boundary, monkeypatch, summary_cut
) -> None:
    threads, memory, engine, thread_id = boundary
    contents = [f"committed {index}" for index in range(6)]
    for index, content in enumerate(contents):
        role = "user" if index % 2 == 0 else "assistant"
        threads.append_message("owner", thread_id, role, content)
    rows = threads.list_message_rows("owner", thread_id)
    if summary_cut is not None:
        threads.set_summary("owner", thread_id, "stored summary", rows[summary_cut - 1][0])
    calls = _observe(monkeypatch, engine, memory)
    before_memory = await memory.list_all()
    context = await engine.assemble("owner", thread_id, "not yet committed", local_only=True)
    assert context.owner == "owner" and context.thread_id == thread_id
    assert context.topical == ""
    assert "not yet committed" not in context.render()
    boundary_index = max(4, summary_cut or 0)
    expected_window = "\n".join(
        f"[{message.role}] {message.content}" for _, message in rows[boundary_index:]
    )
    assert context.window == expected_window
    if summary_cut is not None and summary_cut >= 4:
        assert context.summary == "stored summary"
    else:
        assert context.summary == (
            "- [user] committed 0\n- [agent] committed 1\n"
            "- [user] committed 2\n- [agent] committed 3"
        )
    stored = threads.get_summary("owner", thread_id)
    assert stored is not None
    assert stored.content == context.summary
    assert stored.up_to_rowid == rows[boundary_index - 1][0]
    assert threads.list_message_rows("owner", thread_id) == rows
    assert await memory.list_all() == before_memory
    assert {name: spy.call_count for name, spy in calls.items()} == dict.fromkeys(calls, 0)
    # Same concrete engine, same committed summary and verbatim-window rendering.
    legacy = await engine.assemble("owner", thread_id, "!!!")
    assert legacy == context


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["empty", "populated", "branch"])
async def test_local_only_never_blends_other_thread_history(boundary, monkeypatch, kind) -> None:
    threads, memory, engine, thread_id = boundary
    workspace = threads.get_thread("owner", thread_id).workspace_id
    sibling = threads.create_thread("owner", workspace, "Sibling")
    foreign_workspace = threads.create_workspace("stranger", "Foreign")
    foreign = threads.create_thread("stranger", foreign_workspace.id, "Foreign")
    for owner, other_id in (("owner", sibling.id), ("stranger", foreign.id)):
        threads.append_message(owner, other_id, "user", "other thread secret")
        rowid = threads.list_message_rows(owner, other_id)[0][0]
        threads.set_summary(owner, other_id, "other summary secret", rowid)
    if kind == "branch":
        thread_id = threads.create_thread(
            "owner", workspace, "Branch", parent_thread_id=sibling.id
        ).id
    if kind == "populated":
        threads.append_message("owner", thread_id, "assistant", "only committed local reply")
    calls = _observe(monkeypatch, engine, memory)
    context = await engine.assemble("owner", thread_id, "secret", local_only=True)
    assert context.summary == context.topical == ""
    assert context.window == (
        "[assistant] only committed local reply" if kind == "populated" else ""
    )
    assert context.is_empty is (kind != "populated")
    assert "secret" not in context.render()
    assert {name: spy.call_count for name, spy in calls.items()} == dict.fromkeys(calls, 0)
