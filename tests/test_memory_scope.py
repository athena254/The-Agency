"""R-04 principal-bound memory scope tests (hermetic, no live services)."""

from __future__ import annotations

import pytest

from agency.memory.sms.models import MemoryItem
from agency.memory.sms.scope import PrincipalMemoryScope, PrincipalScope
from agency.memory.sms.store import MemoryStore
from agency.tools.base import ToolContext
from agency.tools.builtin.memory import MemoryQueryTool, MemoryWriteTool


def _principal(owner: str, subject: str = "subject") -> PrincipalScope:
    return PrincipalScope(owner_id=owner, transport="local", subject_id=subject)


def _scope(store: MemoryStore, owner: str, subject: str = "subject") -> PrincipalMemoryScope:
    return PrincipalMemoryScope(store, _principal(owner, subject))


def _ctx(scope: PrincipalMemoryScope | None) -> ToolContext:
    return ToolContext(agent_id="owner-pilot", task_id="task-1", memory_scope=scope)


def test_principal_scope_rejects_invalid_identity() -> None:
    with pytest.raises(ValueError):
        PrincipalScope(owner_id="", transport="local", subject_id="subject")
    with pytest.raises(ValueError):
        PrincipalScope(owner_id="owner", transport="remote", subject_id="subject")
    with pytest.raises(ValueError):
        PrincipalScope(owner_id="owner", transport="local", subject_id="")
    assert _principal("owner").matches(owner_id="owner", transport="local", subject_id="subject")
    assert not _principal("owner").matches(
        owner_id="other", transport="local", subject_id="subject"
    )


def test_distinct_principals_get_distinct_memory_agent_ids() -> None:
    first = _principal("owner-a")
    second = _principal("owner-b")
    assert first.agent_id != second.agent_id
    assert first.agent_id.startswith("principal:")
    assert first.agent_id != second.agent_id


async def test_scope_write_and_query_is_isolated_per_principal(
    test_memory_store: MemoryStore,
) -> None:
    alice = _scope(test_memory_store, "alice")
    bob = _scope(test_memory_store, "bob")
    await alice.write("alice private alpha", title="alpha")
    await bob.write("bob private beta", title="beta")

    alice_items = await alice.list_memories()
    bob_items = await bob.list_memories()
    assert [item.content for item in alice_items] == ["alpha\nalice private alpha"]
    assert [item.content for item in bob_items] == ["beta\nbob private beta"]
    assert {item.agent_id for item in alice_items}.isdisjoint({item.agent_id for item in bob_items})

    assert [item.content for item in await alice.query("beta")] == []
    assert [item.content for item in await bob.query("alpha")] == []
    assert len(await alice.query("alpha")) == 1
    assert len(await bob.query("beta")) == 1


async def test_scope_query_fallback_stays_bound(test_memory_store: MemoryStore) -> None:
    """A substring fallback over one principal's rows never widens the scope."""
    alice = _scope(test_memory_store, "alice")
    bob = _scope(test_memory_store, "bob")
    await alice.write("needle in alice haystack")
    await bob.write("needle in bob haystack")
    # Force the fallback path by using a token that FTS ranks differently; the
    # substring scan must still only consider the caller's own rows.
    assert [item.content for item in await alice.query("needle")] == ["needle in alice haystack"]
    assert [item.content for item in await bob.query("needle")] == ["needle in bob haystack"]


async def test_scope_write_cannot_choose_another_owner(
    test_memory_store: MemoryStore,
) -> None:
    alice = _scope(test_memory_store, "alice")
    item = await alice.write("owned by alice")
    assert item.agent_id == alice.agent_id
    assert await test_memory_store.list_by_agent(item.agent_id) != []
    assert await test_memory_store.list_by_agent(_principal("bob").agent_id) == []


async def test_scoped_query_tool_rejects_caller_supplied_agent_id(
    test_memory_store: MemoryStore,
) -> None:
    alice = _scope(test_memory_store, "alice")
    bob_agent = _principal("bob").agent_id
    await test_memory_store.store(MemoryItem(agent_id=bob_agent, content="bob secret"))

    result = await MemoryQueryTool(scoped=True).run(
        {"query": "bob secret", "agent_id": bob_agent}, _ctx(alice)
    )
    assert result.ok is False
    assert "agent_id" in (result.error or "")


async def test_scoped_tools_never_expose_other_principal_rows(
    test_memory_store: MemoryStore,
) -> None:
    alice = _scope(test_memory_store, "alice")
    bob = _scope(test_memory_store, "bob")
    await bob.write("bob confidential zephyr")

    listed = await MemoryQueryTool(scoped=True).run({}, _ctx(alice))
    assert listed.ok is True
    assert listed.output == []

    written = await MemoryWriteTool(scoped=True).run({"content": "alice note"}, _ctx(alice))
    assert written.ok is True
    assert [item.content for item in await bob.list_memories()] == ["bob confidential zephyr"]
    assert [item.content for item in await alice.list_memories()] == ["alice note"]


async def test_scoped_tools_require_a_scope(test_memory_store: MemoryStore) -> None:
    await test_memory_store.store(MemoryItem(agent_id="x", content="anything"))
    query = await MemoryQueryTool(scoped=True).run({"query": "anything"}, _ctx(None))
    write = await MemoryWriteTool(scoped=True).run({"content": "anything"}, _ctx(None))
    assert query.ok is False and "scope" in (query.error or "")
    assert write.ok is False and "scope" in (write.error or "")


async def test_legacy_unscoped_tool_path_is_unchanged(
    test_memory_store: MemoryStore,
) -> None:
    await test_memory_store.store(MemoryItem(agent_id="agent-1", content="legacy note"))
    ctx = ToolContext(agent_id="agent-1", task_id="task-1", memory_store=test_memory_store)
    listed = await MemoryQueryTool().run({"agent_id": "agent-1"}, ctx)
    assert listed.ok is True and len(listed.output) == 1
    written = await MemoryWriteTool().run({"content": "another"}, ctx)
    assert written.ok is True
