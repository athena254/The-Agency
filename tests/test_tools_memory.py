"""Tests for MemoryQueryTool and MemoryWriteTool (real file-backed store)."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio

from agency.memory.sms.models import MemoryItem
from agency.memory.sms.store import MemoryStore
from agency.tools.base import ToolContext
from agency.tools.builtin.memory import MemoryQueryTool, MemoryWriteTool


@pytest_asyncio.fixture
async def file_memory_store(tmp_path) -> AsyncGenerator[MemoryStore, None]:
    store = MemoryStore(str(tmp_path / "memory.db"))
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


def _ctx(store: MemoryStore | None) -> ToolContext:
    return ToolContext(agent_id="agent-1", task_id="task-1", memory_store=store)


async def test_query_with_text_finds_stored_item(file_memory_store: MemoryStore):
    await file_memory_store.store(
        MemoryItem(agent_id="agent-1", content="suspicious exfiltration channel detected")
    )
    await file_memory_store.store(MemoryItem(agent_id="agent-1", content="weather is sunny"))
    tool = MemoryQueryTool()
    result = await tool.run({"query": "exfiltration"}, _ctx(file_memory_store))
    assert result.ok is True
    assert len(result.output) == 1
    assert "exfiltration" in result.output[0]["content"]
    assert set(result.output[0]) == {"id", "content", "tier", "agent_id", "created_at"}


async def test_query_with_agent_id_lists_items(file_memory_store: MemoryStore):
    await file_memory_store.store(MemoryItem(agent_id="agent-7", content="first note"))
    await file_memory_store.store(MemoryItem(agent_id="agent-7", content="second note"))
    await file_memory_store.store(MemoryItem(agent_id="other", content="unrelated"))
    tool = MemoryQueryTool()
    result = await tool.run({"agent_id": "agent-7"}, _ctx(file_memory_store))
    assert result.ok is True
    assert len(result.output) == 2
    assert all(item["agent_id"] == "agent-7" for item in result.output)


async def test_query_requires_query_or_agent_id(file_memory_store: MemoryStore):
    tool = MemoryQueryTool()
    result = await tool.run({}, _ctx(file_memory_store))
    assert result.ok is False
    assert "query or agent_id required" in (result.error or "")


async def test_query_with_no_store_is_not_ok():
    tool = MemoryQueryTool()
    result = await tool.run({"query": "anything"}, _ctx(None))
    assert result.ok is False
    assert "no memory store" in (result.error or "")


async def test_write_stores_and_returns_id(file_memory_store: MemoryStore):
    tool = MemoryWriteTool()
    result = await tool.run({"content": "probe result: open port 443"}, _ctx(file_memory_store))
    assert result.ok is True
    assert result.output["id"]
    assert result.evidence["memory_id"] == result.output["id"]
    fetched = await file_memory_store.get(result.output["id"])
    assert fetched is not None
    assert fetched.content == "probe result: open port 443"
    assert fetched.agent_id == "agent-1"


async def test_write_with_title_prepends_title(file_memory_store: MemoryStore):
    tool = MemoryWriteTool()
    result = await tool.run(
        {"content": "body text", "title": "Scan Report"}, _ctx(file_memory_store)
    )
    assert result.ok is True
    fetched = await file_memory_store.get(result.output["id"])
    assert fetched is not None
    assert fetched.content.startswith("Scan Report")


async def test_write_with_no_store_is_not_ok():
    tool = MemoryWriteTool()
    result = await tool.run({"content": "hello"}, _ctx(None))
    assert result.ok is False
    assert "no memory store" in (result.error or "")


async def test_roundtrip_write_then_query(file_memory_store: MemoryStore):
    ctx = _ctx(file_memory_store)
    write_result = await MemoryWriteTool().run({"content": "roundtrip canary zephyrquake"}, ctx)
    assert write_result.ok is True
    query_result = await MemoryQueryTool().run({"query": "zephyrquake"}, ctx)
    assert query_result.ok is True
    ids = [item["id"] for item in query_result.output]
    assert write_result.output["id"] in ids


async def test_write_rejects_empty_content(file_memory_store: MemoryStore):
    tool = MemoryWriteTool()
    result = await tool.run({"content": "   "}, _ctx(file_memory_store))
    assert result.ok is False
