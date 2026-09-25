"""Beta orchestrator integration (brief 06, orchestrator-owned seam only).

Covers: strict beta_mode validation, persistent audit startup/reopen,
beta-only web_search registration, trusted BetaPrincipal gating,
owner-scoped memory isolation, research failure integrity, general-chat
control, and nonbeta compatibility. Offline only (scripted LLM, mock
transport); no live Telegram/network.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from agency.kernel.audit import AuditFilter, AuditLog
from agency.orchestrator import AgencyOrchestrator
from agency.tools.base import BetaPrincipal, BetaToolPolicy, ToolContext


class FakeLLM:
    """Scripted LLM: pops one response per generate() call."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.calls = 0

    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        _ = context
        self.prompts.append(prompt)
        self.calls += 1
        if not self._responses:
            return '{"final": "no more scripted responses"}'
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _beta_config(tmp_path, name: str = "audit.db") -> dict[str, Any]:
    return {"beta_mode": True, "audit_db_path": str(tmp_path / name)}


def _principal(uid: int = 111) -> BetaPrincipal:
    return BetaPrincipal(telegram_user_id=uid, private_chat=True)


def _failing_search_transport() -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(500, text="boom")

    return httpx.MockTransport(_handler)


def _deny_network(orch: AgencyOrchestrator) -> None:
    """Force the beta web_search tool to fail without live network."""
    tool = orch._tool_registry.get("web_search")
    tool._transport = _failing_search_transport()


# --- startup / config ---


async def test_beta_mode_truthy_string_rejected() -> None:
    with pytest.raises(TypeError):
        AgencyOrchestrator(config={"beta_mode": "true"}, llm=FakeLLM([]))


async def test_beta_startup_requires_explicit_persistent_audit(tmp_path) -> None:
    missing = AgencyOrchestrator(
        config={"beta_mode": True},
        llm=FakeLLM([]),
        memory_db_path=str(tmp_path / "m.db"),
    )
    with pytest.raises(ValueError, match="audit"):
        await missing.start()

    mem_path = AgencyOrchestrator(
        config={"beta_mode": True, "audit_db_path": ":memory:"},
        llm=FakeLLM([]),
        memory_db_path=str(tmp_path / "m.db"),
    )
    with pytest.raises(ValueError, match="audit"):
        await mem_path.start()


async def test_beta_audit_persists_and_reopens_without_secrets(tmp_path) -> None:
    audit_path = str(tmp_path / "audit.db")
    fake = FakeLLM(['{"final": "hello general"}'])
    orch = AgencyOrchestrator(
        config=_beta_config(tmp_path),
        llm=fake,
        memory_db_path=str(tmp_path / "mem.db"),
    )
    await orch.start()
    try:
        agent = await orch.register_agent("gen", "general", ["respond"])
        task = await orch.submit_task("Hi", "hello", agent.id)
        result = await orch.execute_task(
            task.task_id, {"beta_principal": _principal(111), "sender": "telegram:111"}
        )
        assert result.status.value == "completed"
    finally:
        await orch.stop()

    reopened = AuditLog(audit_path)
    await reopened.initialize()
    try:
        rows = await reopened.query(AuditFilter(limit=100))
        assert len(rows) >= 1
        blob = " ".join((r.evidence or {}).__str__() for r in rows)
        assert "token" not in blob.lower() or "test:token" not in blob
        assert "Bearer" not in blob
    finally:
        await reopened.close()


# --- tool registration / policy ---


async def test_beta_registers_only_web_search(tmp_path) -> None:
    orch = AgencyOrchestrator(
        config=_beta_config(tmp_path),
        llm=FakeLLM([]),
        memory_db_path=str(tmp_path / "mem.db"),
    )
    await orch.start()
    try:
        registry = orch._tool_registry
        assert registry is not None
        assert isinstance(registry._beta_policy, BetaToolPolicy)
        assert [s.name for s in registry.list_specs()] == ["web_search"]
    finally:
        await orch.stop()


async def test_beta_direct_registry_denies_unlisted_tools(tmp_path) -> None:
    orch = AgencyOrchestrator(
        config=_beta_config(tmp_path),
        llm=FakeLLM([]),
        memory_db_path=str(tmp_path / "mem.db"),
    )
    await orch.start()
    try:
        registry = orch._tool_registry
        assert registry is not None
        trusted = ToolContext(
            agent_id="a",
            task_id="t",
            beta_principal=_principal(111),
        )
        for name, args in [
            ("web_fetch", {"url": "https://example.com"}),
            ("memory_write", {"content": "x"}),
            ("sandbox_exec", {"command": "echo hi"}),
        ]:
            result = await registry.call(name, args, trusted)
            assert result.ok is False, name
    finally:
        await orch.stop()


async def test_beta_only_web_search_advertised_to_llm(tmp_path) -> None:
    fake = FakeLLM(['{"final": "hello"}'])
    orch = AgencyOrchestrator(
        config=_beta_config(tmp_path),
        llm=fake,
        memory_db_path=str(tmp_path / "mem.db"),
    )
    await orch.start()
    try:
        agent = await orch.register_agent("gen", "general", ["respond"])
        task = await orch.submit_task("Hi", "hello", agent.id)
        await orch.execute_task(task.task_id, {"beta_principal": _principal(111)})
        assert fake.prompts
        assert '"name": "web_search"' in fake.prompts[0]
        assert '"name": "web_fetch"' not in fake.prompts[0]
        assert '"name": "memory_write"' not in fake.prompts[0]
        assert '"name": "sandbox_exec"' not in fake.prompts[0]
    finally:
        await orch.stop()


# --- principal gating ---


async def test_beta_rejects_missing_principal_before_llm_or_memory(tmp_path) -> None:
    fake = FakeLLM(['{"final": "should never run"}'])
    orch = AgencyOrchestrator(
        config=_beta_config(tmp_path),
        llm=fake,
        memory_db_path=str(tmp_path / "mem.db"),
    )
    await orch.start()
    try:
        agent = await orch.register_agent("gen", "general", ["respond"])
        task = await orch.submit_task("Hi", "hello", agent.id)
        with pytest.raises(PermissionError, match="trusted identity"):
            await orch.execute_task(task.task_id)
        assert fake.calls == 0
        assert await orch._memory_store.count() == 0
    finally:
        await orch.stop()


@pytest.mark.parametrize(
    "forged",
    [
        {"sender": "telegram:111"},
        {"metadata": {"sender": "telegram:111"}},
        {"beta_principal": "telegram:111"},
        {"beta_principal": {"telegram_user_id": 111}},
        {"beta_principal": 111},
    ],
)
async def test_beta_rejects_forged_metadata(tmp_path, forged: dict[str, Any]) -> None:
    fake = FakeLLM(['{"final": "should never run"}'])
    orch = AgencyOrchestrator(
        config=_beta_config(tmp_path),
        llm=fake,
        memory_db_path=str(tmp_path / "mem.db"),
    )
    await orch.start()
    try:
        agent = await orch.register_agent("gen", "general", ["respond"])
        task = await orch.submit_task("Hi", "hello", agent.id)
        with pytest.raises(PermissionError, match="trusted identity"):
            await orch.execute_task(task.task_id, dict(forged))
        assert fake.calls == 0
    finally:
        await orch.stop()


async def test_beta_tool_context_ignores_forged_metadata(tmp_path) -> None:
    orch = AgencyOrchestrator(
        config=_beta_config(tmp_path),
        llm=FakeLLM([]),
        memory_db_path=str(tmp_path / "mem.db"),
    )
    await orch.start()
    try:
        forged = {"sender": "telegram:111", "metadata": {"telegram_user_id": 111}}
        ctx = orch._tool_context("a", "t", forged)
        assert ctx.beta_principal is None
        real = orch._tool_context("a", "t", {"beta_principal": _principal(7)})
        assert isinstance(real.beta_principal, BetaPrincipal)
        assert real.beta_principal.telegram_user_id == 7
        as_string = orch._tool_context("a", "t", {"beta_principal": "telegram:7"})
        assert as_string.beta_principal is None
    finally:
        await orch.stop()


# --- memory isolation ---


async def test_beta_two_owners_memory_isolated(tmp_path) -> None:
    mem_path = str(tmp_path / "mem.db")
    orch = AgencyOrchestrator(
        config=_beta_config(tmp_path),
        llm=FakeLLM(['{"final": "answer A"}', '{"final": "answer B"}']),
        memory_db_path=mem_path,
    )
    await orch.start()
    try:
        agent = await orch.register_agent("gen", "general", ["respond"])
        task_a = await orch.submit_task("A", "question A", agent.id)
        await orch.execute_task(task_a.task_id, {"beta_principal": _principal(111)})
        task_b = await orch.submit_task("B", "question B", agent.id)
        # Swap in second scripted answer for owner B.
        orch._llm_adapter = FakeLLM(['{"final": "answer B"}'])
        orch._executor._llm = orch._llm_adapter.generate
        if orch._tool_driver is not None:
            orch._tool_driver._llm = orch._llm_adapter
        await orch.execute_task(task_b.task_id, {"beta_principal": _principal(222)})
        scope_a = "chat-telegram:111"
        scope_b = "chat-telegram:222"
        items_a = await orch._memory_store.list_by_agent(scope_a)
        items_b = await orch._memory_store.list_by_agent(scope_b)
        assert items_a, "owner A must own at least one memory row"
        assert items_b, "owner B must own at least one memory row"
        assert all(i.agent_id == scope_a for i in items_a)
        assert all(i.agent_id == scope_b for i in items_b)
        assert not any("question B" in i.content for i in items_a)
        assert not any("question A" in i.content for i in items_b)
        # No shared fallback rows under the shared task creator.
        shared = await orch._memory_store.list_by_agent(agent.id)
        assert shared == []
    finally:
        await orch.stop()


# --- research integrity vs general control ---


async def test_beta_research_tool_failure_is_not_completed(tmp_path) -> None:
    fake = FakeLLM(
        [
            '{"action": {"name": "web_search", "args": {"query": "rust 2026"}}}',
            '{"final": "Rust summary [1]"}',
        ]
    )
    orch = AgencyOrchestrator(
        config=_beta_config(tmp_path),
        llm=fake,
        memory_db_path=str(tmp_path / "mem.db"),
    )
    await orch.start()
    try:
        _deny_network(orch)
        agent = await orch.register_agent("res", "research", ["research"])
        task = await orch.submit_task("R", "rust 2026 news", agent.id)
        result = await orch.execute_task(task.task_id, {"beta_principal": _principal(111)})
        assert result.status.value == "failed"
        assert "Rust summary" not in result.output or result.status.value != "completed"
    finally:
        await orch.stop()


async def test_beta_research_without_successful_search_is_not_completed(tmp_path) -> None:
    fake = FakeLLM(['{"final": "confident answer without tools"}'])
    orch = AgencyOrchestrator(
        config=_beta_config(tmp_path),
        llm=fake,
        memory_db_path=str(tmp_path / "mem.db"),
    )
    await orch.start()
    try:
        agent = await orch.register_agent("res", "research", ["research"])
        task = await orch.submit_task("R", "rust 2026 news", agent.id)
        result = await orch.execute_task(task.task_id, {"beta_principal": _principal(111)})
        assert result.status.value == "failed"
    finally:
        await orch.stop()


async def test_beta_research_parse_error_is_not_completed(tmp_path) -> None:
    fake = FakeLLM(["garbage one", "garbage two"])
    orch = AgencyOrchestrator(
        config=_beta_config(tmp_path),
        llm=fake,
        memory_db_path=str(tmp_path / "mem.db"),
    )
    await orch.start()
    try:
        agent = await orch.register_agent("res", "research", ["research"])
        task = await orch.submit_task("R", "rust news", agent.id)
        result = await orch.execute_task(task.task_id, {"beta_principal": _principal(111)})
        assert result.status.value == "failed"
        assert "garbage two" not in result.output
    finally:
        await orch.stop()


async def test_beta_general_chat_without_tools_still_answers(tmp_path) -> None:
    fake = FakeLLM(['{"final": "hello there"}'])
    orch = AgencyOrchestrator(
        config=_beta_config(tmp_path),
        llm=fake,
        memory_db_path=str(tmp_path / "mem.db"),
    )
    await orch.start()
    try:
        agent = await orch.register_agent("gen", "general", ["respond"])
        task = await orch.submit_task("Hi", "hello", agent.id)
        result = await orch.execute_task(task.task_id, {"beta_principal": _principal(111)})
        assert result.status.value == "completed"
        assert "hello there" in result.output
    finally:
        await orch.stop()


# --- nonbeta controls ---


async def test_nonbeta_registers_all_builtins_and_needs_no_principal(tmp_path) -> None:
    fake = FakeLLM(['{"final": "legacy ok"}'])
    orch = AgencyOrchestrator(llm=fake, memory_db_path=":memory:")
    await orch.start()
    try:
        names = sorted(s.name for s in orch._tool_registry.list_specs())
        assert names == [
            "memory_query",
            "memory_write",
            "sandbox_exec",
            "web_fetch",
            "web_search",
        ]
        agent = await orch.register_agent("gen", "general", ["respond"])
        task = await orch.submit_task("Hi", "hello", agent.id)
        result = await orch.execute_task(task.task_id)
        assert result.status.value == "completed"
        owned = await orch._memory_store.list_by_agent(agent.id)
        assert owned, "nonbeta memory stays under task.created_by"
    finally:
        await orch.stop()
