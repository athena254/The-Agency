"""Tests for the Sovereign Mind System: tiers, aging, FTS search, secrets."""

from datetime import timedelta

import pytest

from agency.memory.sms.lifecycle import TieredMemoryEngine, tier_for_age
from agency.memory.sms.models import MemoryItem, MemoryQuery, MemoryTier, TIER_ORDER
from agency.memory.sms.retrieval import RetrievalEngine, build_fts_match
from agency.memory.sms.store import MemoryStore


def test_tier_order_and_navigation():
    assert TIER_ORDER[0] is MemoryTier.COLD
    assert TIER_ORDER[-1] is MemoryTier.HOT
    assert MemoryTier.NORMAL.warmer() is MemoryTier.WARM
    assert MemoryTier.NORMAL.colder() is MemoryTier.COOL
    assert MemoryTier.HOT.warmer() is None
    assert MemoryTier.COLD.colder() is None
    assert MemoryTier.HOT.rank > MemoryTier.COLD.rank


def test_tier_for_age_boundaries():
    assert tier_for_age(timedelta(hours=1)) is MemoryTier.HOT
    assert tier_for_age(timedelta(days=1)) is MemoryTier.HOT
    assert tier_for_age(timedelta(days=2)) is MemoryTier.WARM
    assert tier_for_age(timedelta(days=5)) is MemoryTier.NORMAL
    assert tier_for_age(timedelta(days=20)) is MemoryTier.COOL
    assert tier_for_age(timedelta(days=60)) is MemoryTier.COLD


def test_memory_item_tag_normalization():
    item = MemoryItem(agent_id="a", content="hello", tags=[" Scan ", "scan", ""])
    assert item.tags == ["scan"]


async def test_store_crud(test_memory_store: MemoryStore, sample_memory_item: MemoryItem):
    stored = await test_memory_store.store(sample_memory_item)
    assert stored.id == sample_memory_item.id
    fetched = await test_memory_store.get(stored.id)
    assert fetched is not None and fetched.content == stored.content
    assert await test_memory_store.get("missing") is None
    assert await test_memory_store.count() == 1
    assert await test_memory_store.delete(stored.id) is True
    assert await test_memory_store.delete(stored.id) is False


async def test_search_and_list_filters(test_memory_store: MemoryStore):
    await test_memory_store.store(MemoryItem(agent_id="a", content="alpha", tags=["t1"]))
    await test_memory_store.store(
        MemoryItem(agent_id="b", content="beta", tags=["t2"], tier=MemoryTier.HOT))
    assert len(await test_memory_store.search(MemoryQuery(agent_id="a"))) == 1
    assert len(await test_memory_store.search(MemoryQuery(tags=["t2"]))) == 1
    assert len(await test_memory_store.search(MemoryQuery(tier=MemoryTier.HOT))) == 1
    assert len(await test_memory_store.list_by_agent("a")) == 1
    assert len(await test_memory_store.list_by_tier(MemoryTier.HOT)) == 1
    assert len(await test_memory_store.list_all()) == 2


async def test_fts_search(test_memory_store: MemoryStore):
    await test_memory_store.store(
        MemoryItem(agent_id="a", content="suspicious exfiltration channel detected"))
    await test_memory_store.store(MemoryItem(agent_id="a", content="totally unrelated weather"))
    results = await test_memory_store.search_fts("exfiltration")
    assert len(results) == 1
    assert "exfiltration" in results[0].content


async def test_touch_and_update_tier(test_memory_store: MemoryStore):
    item = await test_memory_store.store(MemoryItem(agent_id="a", content="x"))
    assert await test_memory_store.touch(item.id) is True
    assert await test_memory_store.touch("missing") is False
    updated = await test_memory_store.update_tier(item.id, MemoryTier.HOT)
    assert updated is not None and updated.tier is MemoryTier.HOT
    assert await test_memory_store.update_tier("missing", MemoryTier.HOT) is None


async def test_store_auto_age_demotes_stale(test_memory_store: MemoryStore):
    from datetime import UTC, datetime

    stale = MemoryItem(
        agent_id="a", content="old", tier=MemoryTier.HOT,
        accessed_at=datetime.now(UTC) - timedelta(days=60))
    await test_memory_store.store(stale)
    moved = await test_memory_store.auto_age()
    assert moved >= 1
    # auto_age moves one tier at a time: HOT -> WARM
    assert (await test_memory_store.get(stale.id)).tier is MemoryTier.WARM


async def test_engine_promote_demote(test_memory_store: MemoryStore):
    engine = TieredMemoryEngine(test_memory_store)
    item = await test_memory_store.store(MemoryItem(agent_id="a", content="x"))
    assert (await engine.promote(item.id)).tier is MemoryTier.WARM
    assert (await engine.demote(item.id)).tier is MemoryTier.NORMAL
    assert await engine.promote("missing") is None
    assert await engine.demote("missing") is None
    hot = await test_memory_store.store(
        MemoryItem(agent_id="a", content="y", tier=MemoryTier.HOT))
    assert (await engine.promote(hot.id)).tier is MemoryTier.HOT
    cold = await test_memory_store.store(
        MemoryItem(agent_id="a", content="z", tier=MemoryTier.COLD))
    assert (await engine.demote(cold.id)).tier is MemoryTier.COLD


async def test_engine_auto_age_only_demotion(test_memory_store: MemoryStore):
    from datetime import UTC, datetime

    engine = TieredMemoryEngine(test_memory_store)
    stale = await test_memory_store.store(MemoryItem(
        agent_id="a", content="stale", tier=MemoryTier.HOT,
        accessed_at=datetime.now(UTC) - timedelta(days=60)))
    moved = await engine.auto_age()
    assert moved == 1
    assert (await test_memory_store.get(stale.id)).tier is MemoryTier.COLD


async def test_engine_periodic_start_stop(test_memory_store: MemoryStore):
    engine = TieredMemoryEngine(test_memory_store, scan_interval=0.05)
    task = engine.start_periodic()
    assert engine.start_periodic() is task  # idempotent
    await engine.stop_periodic()
    await engine.stop_periodic()  # safe when stopped


async def test_retrieval_engines(test_memory_store: MemoryStore):
    await test_memory_store.store(
        MemoryItem(agent_id="a", content="port scan against staging host"))
    engine = RetrievalEngine(test_memory_store)
    assert build_fts_match("port scan")
    assert len(await engine.semantic_search("port scan", agent_id="a")) >= 1
    assert len(await engine.hybrid_search("staging")) >= 1
    assert await engine.graph_search("host", "connected") == []


async def test_secrets_roundtrip(test_secrets_store):
    sid = await test_secrets_store.store_secret("agent-1", "api-key", "s3cr3t")
    assert sid
    assert await test_secrets_store.get_secret("agent-1", "api-key") == "s3cr3t"
    assert await test_secrets_store.get_secret("agent-1", "missing") is None
    assert "api-key" in await test_secrets_store.list_secrets("agent-1")
    await test_secrets_store.rotate_secret("agent-1", "api-key", "n3w")
    assert await test_secrets_store.get_secret("agent-1", "api-key") == "n3w"
    assert await test_secrets_store.revoke_secret("agent-1", "api-key") is True
    assert await test_secrets_store.get_secret("agent-1", "api-key") is None
    assert await test_secrets_store.revoke_secret("agent-1", "missing") is False


async def test_secrets_acl_enforced(test_secrets_store):
    await test_secrets_store.store_secret("agent-1", "k", "v", acl=["agent-1"])
    assert await test_secrets_store.get_secret("agent-1", "k", requester="agent-1") == "v"
    assert await test_secrets_store.get_secret("agent-1", "k", requester="intruder") is None
