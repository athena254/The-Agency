"""Integration tests: persistent memory + conversation continuity.

Parity sprint 1 — the butler must remember earlier turns (same sender)
and memory must survive a store rebuild (persistence).
"""

from __future__ import annotations

import pytest

from agency.butler.service import ButlerService
from agency.memory.sms.models import MemoryItem, MemoryTier
from agency.memory.sms.store import MemoryStore
from agency.orchestrator import AgencyOrchestrator


class TestMemoryPersistence:
    def test_default_memory_path_is_persistent(self):
        orch = AgencyOrchestrator()
        assert orch._memory_store.db_path == "data/memory.db"

    def test_memory_db_path_param(self, tmp_path):
        db = str(tmp_path / "mem.db")
        orch = AgencyOrchestrator(memory_db_path=db)
        assert orch._memory_store.db_path == db

    @pytest.mark.asyncio
    async def test_items_survive_store_rebuild(self, tmp_path):
        db = str(tmp_path / "mem.db")
        store1 = MemoryStore(db_path=db)
        await store1.initialize()
        await store1.store(
            MemoryItem(agent_id="chat-alice", content="remember the milk", tags=["conversation"])
        )
        # Simulate restart: brand-new store object, same file.
        store2 = MemoryStore(db_path=db)
        await store2.initialize()
        items = await store2.list_by_agent("chat-alice")
        assert any("remember the milk" in i.content for i in items)
        await store1.close()
        await store2.close()


class TestButlerSharesStore:
    def test_butler_uses_orchestrator_store(self):
        orch = AgencyOrchestrator(memory_db_path=":memory:")
        butler = ButlerService(orchestrator=orch)
        assert butler._memory is orch._memory_store


class TestConversationRecall:
    @pytest.mark.asyncio
    async def test_recall_returns_earlier_turn(self):
        from agency.memory.sms.retrieval import RetrievalEngine

        store = MemoryStore(db_path=":memory:")
        await store.initialize()
        await store.store(
            MemoryItem(
                agent_id="chat-danny",
                content="[danny] my favorite language is Rust\n[Agent] noted!",
                tags=["conversation", "chat-danny"],
            )
        )
        engine = RetrievalEngine(store)
        butler = ButlerService.__new__(ButlerService)
        butler._memory = store
        butler._memory_retrieval = engine
        import structlog

        butler._log = structlog.get_logger(__name__)

        context = await butler._recall_history("danny", "what is my favorite language")
        assert "Rust" in context
        assert context.startswith("Earlier conversation:")
        await store.close()

    @pytest.mark.asyncio
    async def test_recall_other_sender_excluded(self):
        from agency.memory.sms.retrieval import RetrievalEngine

        store = MemoryStore(db_path=":memory:")
        await store.initialize()
        await store.store(
            MemoryItem(
                agent_id="chat-bob",
                content="[bob] i love python\n[Agent] cool",
                tags=["conversation"],
            )
        )
        engine = RetrievalEngine(store)
        butler = ButlerService.__new__(ButlerService)
        butler._memory = store
        butler._memory_retrieval = engine
        import structlog

        butler._log = structlog.get_logger(__name__)

        context = await butler._recall_history("danny", "what about python")
        assert context == ""  # bob's history must not leak to danny
        await store.close()

    @pytest.mark.asyncio
    async def test_store_turn_uses_chat_sender_id(self):
        store = MemoryStore(db_path=":memory:")
        await store.initialize()
        butler = ButlerService.__new__(ButlerService)
        butler._memory = store
        import structlog

        butler._log = structlog.get_logger(__name__)

        from agency.kernel.identity import Agent, Capability

        agent = Agent(
            name="General Agent",
            domain="general",
            capabilities=[Capability(name="respond")],
        )
        await butler._store_turn("danny", agent, "hello there", "hi!")
        items = await store.list_by_agent("chat-danny")
        assert len(items) == 1
        assert "hello there" in items[0].content
        assert "chat-danny" in items[0].tags
        await store.close()


class TestGeneralAgentToolPath:
    @pytest.mark.asyncio
    async def test_general_domain_uses_tool_loop_with_own_prompt(self):
        from tests.test_research_integration import FakeLLM

        fake = FakeLLM(['{"final": "hi, how can I help?"}'])
        orch = AgencyOrchestrator(llm=fake, memory_db_path=":memory:")
        await orch.start()
        try:
            agent = await orch.register_agent("gen", "general", ["respond", "tools"])
            task = await orch.submit_task("Chat", "hello", agent_id=agent.id)
            result = await orch.execute_task(task.task_id)
            assert result.status.value == "completed"
            # The general prompt must be in the first LLM call
            assert "General Agent" in fake.prompts[0]
        finally:
            await orch.stop()

    @pytest.mark.asyncio
    async def test_memory_context_injected_into_tool_prompt(self):
        from tests.test_research_integration import FakeLLM

        fake = FakeLLM(['{"final": "got it"}'])
        orch = AgencyOrchestrator(llm=fake, memory_db_path=":memory:")
        await orch.start()
        try:
            agent = await orch.register_agent("gen", "general", ["respond", "tools"])
            task = await orch.submit_task("Chat", "hello", agent_id=agent.id)
            await orch.execute_task(
                task.task_id,
                context={"memory_context": "Earlier conversation:\n- [danny] I like Rust"},
            )
            assert "I like Rust" in fake.prompts[0]
        finally:
            await orch.stop()
