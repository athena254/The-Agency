"""Tests for the Butler context engine, thread commands, and wiring.

Covers the long-session context layers (summary + window + topical
recall), cross-thread and cross-owner pollution guarantees, the
deterministic /new /threads /switch /current commands, and the
service-level integration (active-thread fallback, one-context-source
rule, summary triggering).
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from agency.butler.commands import CommandError, ThreadCommands
from agency.butler.context import ThreadContextEngine
from agency.butler.service import ButlerService
from agency.butler.threads import ThreadStore
from agency.orchestrator import AgencyOrchestrator


@pytest.fixture
def store() -> ThreadStore:
    s = ThreadStore(":memory:")
    yield s
    s.close()


@pytest_asyncio.fixture
async def memory(tmp_path):
    from agency.memory.sms.store import MemoryStore

    m = MemoryStore(str(tmp_path / "memory.db"))
    await m.initialize()
    yield m
    await m.close()


@pytest_asyncio.fixture
async def engine(store, memory):
    return ThreadContextEngine(store, memory, window=4)


def _seed_thread(store: ThreadStore, owner: str, title: str = "Thread"):
    ws = store.create_workspace(owner, "WS")
    return store.create_thread(owner, ws.id, title)


# ---------------------------------------------------------------------- #
# ThreadStore: summaries + active pointer
# ---------------------------------------------------------------------- #


def test_set_and_get_summary_roundtrip(store: ThreadStore) -> None:
    thread = _seed_thread(store, "alice")
    store.append_message("alice", thread.id, "user", "hello")
    store.append_message("alice", thread.id, "assistant", "hi there")
    rows = store.list_message_rows("alice", thread.id)
    summary = store.set_summary("alice", thread.id, "older stuff", rows[-1][0])
    assert summary.up_to_rowid == rows[-1][0]
    fetched = store.get_summary("alice", thread.id)
    assert fetched is not None and fetched.content == "older stuff"


def test_summary_replaced_atomically(store: ThreadStore) -> None:
    thread = _seed_thread(store, "alice")
    store.append_message("alice", thread.id, "user", "one")
    rowid = store.list_message_rows("alice", thread.id)[0][0]
    store.set_summary("alice", thread.id, "v1", rowid)
    store.set_summary("alice", thread.id, "v2", rowid)
    fetched = store.get_summary("alice", thread.id)
    assert fetched is not None and fetched.content == "v2"


def test_summary_up_to_rowid_must_reference_message(store: ThreadStore) -> None:
    thread = _seed_thread(store, "alice")
    store.append_message("alice", thread.id, "user", "one")
    with pytest.raises(ValueError):
        store.set_summary("alice", thread.id, "bogus", 99999)


def test_summary_wrong_owner_raises(store: ThreadStore) -> None:
    thread = _seed_thread(store, "alice")
    store.append_message("alice", thread.id, "user", "one")
    rowid = store.list_message_rows("alice", thread.id)[0][0]
    with pytest.raises(KeyError):
        store.set_summary("bob", thread.id, "stolen", rowid)
    with pytest.raises(KeyError):
        store.get_summary("bob", thread.id)


def test_active_thread_roundtrip_and_clear(store: ThreadStore) -> None:
    thread = _seed_thread(store, "alice")
    other = _seed_thread(store, "alice", "Other")
    assert store.get_active_thread("alice") is None
    store.set_active_thread("alice", thread.id)
    active = store.get_active_thread("alice")
    assert active is not None and active.id == thread.id
    store.set_active_thread("alice", other.id)
    active = store.get_active_thread("alice")
    assert active is not None and active.id == other.id
    assert store.clear_active_thread("alice") is True
    assert store.get_active_thread("alice") is None
    assert store.clear_active_thread("alice") is False


def test_active_thread_cannot_point_at_foreign_thread(store: ThreadStore) -> None:
    mine = _seed_thread(store, "alice")
    foreign = _seed_thread(store, "bob")
    with pytest.raises(KeyError):
        store.set_active_thread("alice", foreign.id)
    store.set_active_thread("alice", mine.id)
    assert store.get_active_thread("alice").id == mine.id  # type: ignore[union-attr]


def test_active_thread_isolated_per_owner(store: ThreadStore) -> None:
    a = _seed_thread(store, "alice")
    b = _seed_thread(store, "bob")
    store.set_active_thread("alice", a.id)
    store.set_active_thread("bob", b.id)
    assert store.get_active_thread("alice").id == a.id  # type: ignore[union-attr]
    assert store.get_active_thread("bob").id == b.id  # type: ignore[union-attr]


# ---------------------------------------------------------------------- #
# Context engine: layered assembly + pollution guarantees
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_assemble_window_only(engine: ThreadContextEngine, store: ThreadStore) -> None:
    thread = _seed_thread(store, "alice")
    for i in range(3):
        store.append_message("alice", thread.id, "user", f"question {i}")
        store.append_message("alice", thread.id, "assistant", f"answer {i}")
    ctx = await engine.assemble("alice", thread.id, "next question")
    # window=4 with 6 stored messages: the oldest pair is auto-folded
    # into a summary so nothing between summary and window is lost.
    assert "[user] question 0" in ctx.summary
    assert "[user] question 0" not in ctx.window
    assert "[user] question 1" in ctx.window
    assert "[assistant] answer 2" in ctx.window
    assert ctx.render().startswith("Thread summary (older conversation):")


@pytest.mark.asyncio
async def test_summary_and_window_split(engine: ThreadContextEngine, store: ThreadStore) -> None:
    thread = _seed_thread(store, "alice")
    rows: list[tuple[int, object]] = []
    for i in range(8):
        store.append_message("alice", thread.id, "user", f"q{i}")
        store.append_message("alice", thread.id, "assistant", f"a{i}")
        rows = store.list_message_rows("alice", thread.id)
    # Deliberately stale summary: it covers only the first 4 messages
    # while 16 exist. assemble must extend it forward — nothing may fall
    # between the summary point and the 4-message window.
    cut = rows[3][0]
    engine.summarize_prefix("alice", thread.id, up_to_rowid=cut)
    ctx = await engine.assemble("alice", thread.id, "q7")
    # Everything behind the window is in the summary…
    for i in (0, 1, 2, 3, 4, 5):
        assert f"[user] q{i}" in ctx.summary
    assert "[user] q6" not in ctx.summary
    # …and the last 4 messages are verbatim in the window…
    assert "[user] q6" in ctx.window
    assert "[assistant] a6" in ctx.window
    assert "[user] q7" in ctx.window
    assert "[assistant] a7" in ctx.window
    # …with zero overlap between the layers.
    assert "[user] q5" not in ctx.window
    assert ctx.render().startswith("Thread summary (older conversation):")


@pytest.mark.asyncio
async def test_default_summarize_keeps_last_window_verbatim(
    engine: ThreadContextEngine, store: ThreadStore
) -> None:
    thread = _seed_thread(store, "alice")
    for i in range(10):
        store.append_message("alice", thread.id, "user", f"msg {i}")
    engine.summarize_prefix("alice", thread.id)
    ctx = await engine.assemble("alice", thread.id, "current thing")
    # Older turns are summarized…
    assert "[user] msg 0" in ctx.summary
    assert "[user] msg 5" in ctx.summary
    # …the last window stays verbatim…
    assert "[user] msg 6" in ctx.window
    assert "[user] msg 9" in ctx.window
    # …and nothing appears in both layers.
    assert "[user] msg 6" not in ctx.summary
    assert "[user] msg 5" not in ctx.window
    assert "[user] msg 9" not in ctx.summary


@pytest.mark.asyncio
async def test_stale_summary_never_drops_messages(
    engine: ThreadContextEngine, store: ThreadStore
) -> None:
    """No-gap guarantee: summary + window must cover the FULL history."""
    thread = _seed_thread(store, "alice")
    for i in range(10):
        store.append_message("alice", thread.id, "user", f"item {i}")
        store.append_message("alice", thread.id, "assistant", f"ack {i}")
    rows = store.list_message_rows("alice", thread.id)
    # Summary stuck after message 1 only — 18 messages lie behind the
    # window unsummarized. Nothing may be silently dropped.
    engine.summarize_prefix("alice", thread.id, up_to_rowid=rows[0][0])
    ctx = await engine.assemble("alice", thread.id, "item 9")
    covered = ctx.summary + "\n" + ctx.window
    for i in range(10):
        assert f"[user] item {i}" in covered, f"item {i} fell into the gap"
    assert "[user] item 9" in ctx.window
    assert "[user] item 0" not in ctx.window


@pytest.mark.asyncio
async def test_empty_thread_yields_empty_context(
    engine: ThreadContextEngine, store: ThreadStore
) -> None:
    thread = _seed_thread(store, "alice")
    ctx = await engine.assemble("alice", thread.id, "anything")
    assert ctx.is_empty
    assert ctx.render() == ""


@pytest.mark.asyncio
async def test_thread_context_cannot_cross_threads(
    engine: ThreadContextEngine, store: ThreadStore, memory
) -> None:
    t1 = _seed_thread(store, "alice", "Alpha")
    t2 = _seed_thread(store, "alice", "Beta")
    store.append_message("alice", t1.id, "user", "the omega protocol secret")
    store.append_message("alice", t1.id, "assistant", "noted the omega protocol")
    await engine.record_turn("alice", t1.id, "the omega protocol secret", "noted")
    ctx2 = await engine.assemble("alice", t2.id, "omega protocol")
    assert ctx2.is_empty
    # And the wrong-owner case is a hard KeyError, not empty context.
    with pytest.raises(KeyError):
        await engine.assemble("bob", t1.id, "omega protocol")


@pytest.mark.asyncio
async def test_topical_recall_scoped_to_own_thread(
    engine: ThreadContextEngine, store: ThreadStore, memory
) -> None:
    thread = _seed_thread(store, "alice")
    # Push the turn out of the 4-message window via a long tail.
    await engine.record_turn("alice", thread.id, "we chose postgres for storage", "agreed")
    for i in range(6):
        store.append_message("alice", thread.id, "user", f"filler {i}")
        store.append_message("alice", thread.id, "assistant", f"ok {i}")
    ctx = await engine.assemble("alice", thread.id, "postgres")
    assert "postgres" in ctx.topical.lower()
    assert "filler" not in ctx.topical


# ---------------------------------------------------------------------- #
# Commands
# ---------------------------------------------------------------------- #


def test_parse_commands() -> None:
    assert ThreadCommands.parse("/new Backend") == ("new", "Backend")
    assert ThreadCommands.parse("/SWITCH 2") == ("switch", "2")
    assert ThreadCommands.parse("/threads") == ("threads", "")
    assert ThreadCommands.parse("/current ") == ("current", "")
    assert ThreadCommands.parse("// not a command") is None
    assert ThreadCommands.parse("hello /new") is None
    assert ThreadCommands.parse("/") is None
    assert ThreadCommands.parse("") is None


def test_unknown_command_rejected(store: ThreadStore) -> None:
    cmds = ThreadCommands(store)
    with pytest.raises(CommandError, match="Unknown command"):
        cmds.execute("alice", "deploy", "")


def test_new_creates_switches_and_lists(store: ThreadStore) -> None:
    cmds = ThreadCommands(store)
    result = cmds.execute("alice", "new", "Backend")
    assert result.switched and result.thread_id is not None
    active = store.get_active_thread("alice")
    assert active is not None and active.id == result.thread_id
    listing = cmds.execute("alice", "threads", "")
    assert "Backend" in listing.text and "*active*" in listing.text


def test_new_requires_title(store: ThreadStore) -> None:
    cmds = ThreadCommands(store)
    with pytest.raises(CommandError, match="title is required"):
        cmds.execute("alice", "new", "")


def test_switch_by_title_and_id(store: ThreadStore) -> None:
    cmds = ThreadCommands(store)
    first = cmds.execute("alice", "new", "Alpha")
    second = cmds.execute("alice", "new", "Beta")
    assert second.switched
    back = cmds.execute("alice", "switch", "Alpha")
    assert back.thread_id == first.thread_id
    by_id = cmds.execute("alice", "switch", str(first.thread_id))
    assert by_id.thread_id == first.thread_id


def test_switch_ambiguous_fails_closed(store: ThreadStore) -> None:
    cmds = ThreadCommands(store)
    cmds.execute("alice", "new", "backend")
    cmds.execute("alice", "new", "Backend")
    with pytest.raises(CommandError, match="matches 2 threads"):
        cmds.execute("alice", "switch", "backend")


def test_switch_unknown_thread_fails(store: ThreadStore) -> None:
    cmds = ThreadCommands(store)
    cmds.execute("alice", "new", "Alpha")
    with pytest.raises(CommandError, match="No thread titled"):
        cmds.execute("alice", "switch", "Nonexistent")


def test_commands_are_owner_scoped(store: ThreadStore) -> None:
    cmds = ThreadCommands(store)
    cmds.execute("alice", "new", "Private")
    listing_bob = cmds.execute("bob", "threads", "")
    assert "Private" not in listing_bob.text
    with pytest.raises(CommandError, match="No thread titled"):
        cmds.execute("bob", "switch", "Private")
    current_bob = cmds.execute("bob", "current", "")
    assert "No active thread" in current_bob.text


def test_current_reports_state(store: ThreadStore) -> None:
    cmds = ThreadCommands(store)
    made = cmds.execute("alice", "new", "Alpha")
    store.append_message("alice", str(made.thread_id), "user", "hi")
    current = cmds.execute("alice", "current", "")
    assert "Alpha" in current.text and "Messages: 1" in current.text


# ---------------------------------------------------------------------- #
# Service integration
# ---------------------------------------------------------------------- #


def _make_service(tmp_path) -> ButlerService:
    store = ThreadStore(str(tmp_path / "threads.db"))
    orch = AgencyOrchestrator(memory_db_path=str(tmp_path / "memory.db"))
    return ButlerService(orchestrator=orch, thread_store=store), store


@pytest.mark.asyncio
async def test_service_commands_opt_in_and_active_thread_fallback(tmp_path) -> None:
    service, store = _make_service(tmp_path)
    await service.start()
    try:
        # Commands disabled: /threads is an ordinary message.
        await service.handle_message("/threads", "alice", {}, commands_enabled=False)
        assert store.get_active_thread("alice") is None
        # Enabled: creates a thread and sets the active pointer.
        reply = await service.handle_message("/new Research", "alice", {}, commands_enabled=True)
        assert "Research" in reply
        active = store.get_active_thread("alice")
        assert active is not None and active.title == "Research"
        # Active-thread fallback: no thread_id supplied, still lands in thread.
        await service.handle_message("hello thread", "alice", {}, commands_enabled=True)
        assert any(
            m.content == "hello thread"
            for m in store.list_messages("alice", active.id)
        )
        # Without commands_enabled, no active-thread fallback occurs.
        await service.handle_message("outside thread", "alice", {})
        contents = [m.content for m in store.list_messages("alice", active.id)]
        assert "outside thread" not in contents
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_service_thread_context_one_source_rule(tmp_path) -> None:
    service, store = _make_service(tmp_path)
    await service.start()
    try:
        ws = service.create_workspace("alice", "WS")
        t1 = service.create_thread("alice", ws.id, "Alpha")
        t2 = service.create_thread("alice", ws.id, "Beta")
        await service.handle_message("the zeta cipher", "alice", {"thread_id": t1.id})
        # Thread context assembled for t2 must not include t1 content.
        ctx = await service.thread_context_engine.assemble("alice", t2.id, "zeta cipher")
        assert ctx.is_empty
        # Explicit thread_id still wins over the active pointer.
        reply = await service.handle_message("beta note", "alice", {"thread_id": t2.id})
        assert reply
        contents = [m.content for m in store.list_messages("alice", t2.id)]
        assert "beta note" in contents
        assert contents == ["beta note", reply] or len(contents) == 2
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_service_triggers_summary_on_long_threads(tmp_path) -> None:
    service, store = _make_service(tmp_path)
    await service.start()
    try:
        ws = service.create_workspace("alice", "WS")
        thread = service.create_thread("alice", ws.id, "Long")
        for i in range(11):
            await service.handle_message(
                f"long session msg {i}", "alice", {"thread_id": thread.id}
            )
        summary = store.get_summary("alice", thread.id)
        assert summary is not None
        ctx = await service.thread_context_engine.assemble(
            "alice", thread.id, "long session msg 8"
        )
        assert ctx.summary
        assert "[user] long session msg 0" in ctx.summary
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_service_command_error_is_user_visible(tmp_path) -> None:
    service, store = _make_service(tmp_path)
    await service.start()
    try:
        reply = await service.handle_message("/switch nope", "alice", {}, commands_enabled=True)
        assert "No thread titled" in reply
        reply = await service.handle_message("/bogus", "alice", {}, commands_enabled=True)
        assert "Unknown command" in reply
    finally:
        await service.stop()
        store.close()
