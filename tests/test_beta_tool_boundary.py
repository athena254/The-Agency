"""Beta tool-boundary tests (brief 03): fail-closed per-call ToolPolicy.

Scope: ``ToolRegistry.call`` with optional ``beta_policy`` (legacy-open when
omitted). ``BetaToolPolicy`` allows only pre-reviewed harmless tools in
trusted Telegram context; denies absent identity, unclassified names,
MUTATES_STATE/EXECUTES_CODE, memory tools, and web_fetch (B-05 open).
``web_search`` only when query/result-count bounded and actor trusted.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from agency.memory.sms.models import MemoryItem
from agency.memory.sms.store import MemoryStore
from agency.tools.base import (
    BetaPrincipal,
    BetaToolPolicy,
    ToolContext,
    ToolPolicyDecision,
    ToolResult,
    ToolRisk,
    ToolSpec,
)
from agency.tools.builtin.memory import MemoryQueryTool, MemoryWriteTool
from agency.tools.driver import ToolDriver
from agency.tools.registry import ToolRegistry, build_default_registry


class FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(
        self,
        agent: str | None = None,
        action: str | None = None,
        result: str | None = None,
        target: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> str:
        self.entries.append(
            {
                "agent": agent,
                "action": action,
                "result": result,
                "target": target,
                "evidence": evidence or {},
            }
        )
        return str(len(self.entries))


class CountingTool:
    """Generic stub that counts run() invocations."""

    def __init__(self, spec: ToolSpec, output: Any = "ok") -> None:
        self.spec = spec
        self.output = output
        self.run_calls = 0

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.run_calls += 1
        return ToolResult(tool=self.spec.name, ok=True, output=self.output)


def _search_spec() -> ToolSpec:
    return ToolSpec(
        name="web_search",
        description="Search the web.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        risk=ToolRisk.READ_ONLY,
    )


def _trusted_ctx(**kwargs: Any) -> ToolContext:
    base: dict[str, Any] = {
        "agent_id": "agent-1",
        "task_id": "task-1",
        "beta_principal": BetaPrincipal(telegram_user_id=12345, private_chat=True),
    }
    base.update(kwargs)
    return ToolContext(**base)


def _untrusted_ctx() -> ToolContext:
    return ToolContext(agent_id="agent-1", task_id="task-1")


@pytest_asyncio.fixture
async def file_memory_store(tmp_path) -> AsyncGenerator[MemoryStore, None]:
    store = MemoryStore(str(tmp_path / "memory.db"))
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


async def test_legacy_open_without_policy() -> None:
    """P-I1: omitting beta_policy preserves legacy behavior."""
    registry = ToolRegistry()
    echo = CountingTool(
        ToolSpec(
            name="echo",
            description="Echo.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )
    )
    registry.register(echo)
    result = await registry.call("echo", {"text": "hi"}, _untrusted_ctx())
    assert result.ok is True
    assert echo.run_calls == 1


async def test_beta_denies_absent_trusted_identity() -> None:
    registry = ToolRegistry(beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec())
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _untrusted_ctx())
    assert result.ok is False
    assert "trusted" in (result.error or "").lower()
    assert stub.run_calls == 0


async def test_beta_forged_metadata_string_does_not_grant_access() -> None:
    """P-P1/P-P3: untrusted metadata sender string alone must not authorize."""
    registry = ToolRegistry(beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec())
    registry.register(stub)
    forged = ToolContext(
        agent_id="agent-1",
        task_id="task-1",
        metadata={"sender": "telegram:12345", "telegram_user_id": 12345},
    )
    result = await registry.call("web_search", {"query": "hello"}, forged)
    assert result.ok is False
    assert stub.run_calls == 0


async def test_beta_denies_unclassified_tool_even_when_read_only() -> None:
    """P-P1: READ_ONLY label is not a grant; allowlist is explicit."""
    registry = ToolRegistry(beta_policy=BetaToolPolicy())
    echo = CountingTool(ToolSpec(name="echo", description="Echo.", risk=ToolRisk.READ_ONLY))
    registry.register(echo)
    result = await registry.call("echo", {}, _trusted_ctx())
    assert result.ok is False
    assert "allowlist" in (result.error or "").lower()
    assert echo.run_calls == 0


async def test_beta_denies_mutating_and_executes_risk() -> None:
    """P-P4: no MUTATES_STATE/EXECUTES_CODE in beta even when allowlisted."""
    policy = BetaToolPolicy(allowed_tools={"writer_tool", "exec_tool"})
    registry = ToolRegistry(beta_policy=policy)
    writer = CountingTool(
        ToolSpec(name="writer_tool", description="W.", risk=ToolRisk.MUTATES_STATE)
    )
    execer = CountingTool(ToolSpec(name="exec_tool", description="E.", risk=ToolRisk.EXECUTES_CODE))
    registry.register(writer)
    registry.register(execer)
    denied_w = await registry.call("writer_tool", {}, _trusted_ctx())
    denied_e = await registry.call("exec_tool", {}, _trusted_ctx())
    assert denied_w.ok is False
    assert denied_e.ok is False
    assert writer.run_calls == 0
    assert execer.run_calls == 0


async def test_beta_denies_memory_query_cross_user_no_leak(
    file_memory_store: MemoryStore,
) -> None:
    """P-P3: memory_query denied in beta; never leaks another owner's rows."""
    await file_memory_store.store(MemoryItem(agent_id="owner-A", content="alpha secret"))
    await file_memory_store.store(MemoryItem(agent_id="owner-B", content="beta secret"))
    audit = FakeAudit()
    registry = ToolRegistry(audit=audit, beta_policy=BetaToolPolicy())
    registry.register(MemoryQueryTool())
    ctx = _trusted_ctx(memory_store=file_memory_store)
    result = await registry.call("memory_query", {"query": "secret", "agent_id": "owner-B"}, ctx)
    assert result.ok is False
    assert "memory" in (result.error or "").lower()
    assert any(e["target"] == "memory_query" for e in audit.entries)


async def test_beta_denies_memory_write_no_side_effect(
    file_memory_store: MemoryStore,
) -> None:
    """P-P3: memory_write denied in beta; store stays empty."""
    audit = FakeAudit()
    registry = ToolRegistry(audit=audit, beta_policy=BetaToolPolicy())
    registry.register(MemoryWriteTool())
    ctx = _trusted_ctx(memory_store=file_memory_store)
    result = await registry.call("memory_write", {"content": "hello"}, ctx)
    assert result.ok is False
    assert audit.entries
    items = await file_memory_store.search_fts("hello", limit=10)
    assert items == []


async def test_beta_denies_web_fetch_until_b05_closed() -> None:
    registry = ToolRegistry(beta_policy=BetaToolPolicy())
    fetch = CountingTool(
        ToolSpec(
            name="web_fetch",
            description="Fetch.",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            risk=ToolRisk.READ_ONLY,
        )
    )
    registry.register(fetch)
    result = await registry.call("web_fetch", {"url": "https://example.com"}, _trusted_ctx())
    assert result.ok is False
    assert fetch.run_calls == 0


async def test_beta_allows_bounded_web_search_stub() -> None:
    audit = FakeAudit()
    registry = ToolRegistry(audit=audit, beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec(), output=[{"title": "t"}])
    registry.register(stub)
    result = await registry.call("web_search", {"query": "agency beta"}, _trusted_ctx())
    assert result.ok is True
    assert stub.run_calls == 1
    assert audit.entries and audit.entries[0]["action"] == "tool.call"


async def test_beta_denies_unbounded_web_search() -> None:
    registry = ToolRegistry(beta_policy=BetaToolPolicy(max_query_chars=10, max_results=5))
    stub = CountingTool(_search_spec())
    registry.register(stub)
    long_q = await registry.call("web_search", {"query": "x" * 11}, _trusted_ctx())
    assert long_q.ok is False
    many = await registry.call("web_search", {"query": "short", "max_results": 20}, _trusted_ctx())
    assert many.ok is False
    assert stub.run_calls == 0


async def test_beta_denial_is_audited_with_zero_run_calls() -> None:
    """P-P2: direct registry.call denial is audited; tool never runs."""
    audit = FakeAudit()
    registry = ToolRegistry(audit=audit, beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec())
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hi"}, _untrusted_ctx())
    assert result.ok is False
    assert stub.run_calls == 0
    assert len(audit.entries) == 1
    assert audit.entries[0]["action"] == "tool.error"
    assert audit.entries[0]["target"] == "web_search"


async def test_beta_schema_validation_runs_before_policy() -> None:
    """Invalid args surface as invalid-args; policy never grants side effects."""
    registry = ToolRegistry(beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec())
    registry.register(stub)
    result = await registry.call("web_search", {}, _trusted_ctx())
    assert result.ok is False
    assert "invalid args" in (result.error or "").lower()
    assert stub.run_calls == 0


async def test_beta_policy_error_fails_closed() -> None:
    """P-P4: a raising policy denies instead of allowing."""

    def _boom(spec: ToolSpec, args: dict[str, Any], ctx: ToolContext) -> bool:
        raise RuntimeError("boom")

    registry = ToolRegistry(beta_policy=_boom)  # type: ignore[arg-type]
    stub = CountingTool(_search_spec())
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hi"}, _trusted_ctx())
    assert result.ok is False
    assert stub.run_calls == 0


async def test_beta_driver_path_cannot_bypass_policy() -> None:
    """Driver LLM loop routes through registry.call, so policy still denies."""

    class _ScriptedLLM:
        def __init__(self, responses: list[str]) -> None:
            self._responses = list(responses)

        async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
            assert self._responses, "LLM ran out of scripted responses"
            return self._responses.pop(0)

    registry = ToolRegistry(beta_policy=BetaToolPolicy())
    fetch = CountingTool(
        ToolSpec(
            name="web_fetch",
            description="Fetch.",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            risk=ToolRisk.READ_ONLY,
        )
    )
    registry.register(fetch)
    llm = _ScriptedLLM(
        [
            '{"action": {"name": "web_fetch", "args": {"url": "https://example.com"}}}',
            '{"final": "done"}',
        ]
    )
    driver = ToolDriver(registry=registry, llm=llm)
    outcome = await driver.run("fetch this", "sys", _trusted_ctx())
    assert fetch.run_calls == 0
    assert outcome.steps and outcome.steps[0].ok is False
    assert "beta policy denied" in (outcome.steps[0].error or "").lower()


async def test_registry_factory_preserves_explicit_beta_policy() -> None:
    tool = CountingTool(_search_spec())
    registry = build_default_registry([tool], beta_policy=BetaToolPolicy())
    result = await registry.call("web_search", {"query": "hi"}, ToolContext("a", "t"))
    assert result.ok is False
    assert tool.run_calls == 0


async def test_policy_exception_does_not_leak_exception_text() -> None:
    marker = "synthetic-private-value"

    def _boom(spec: ToolSpec, args: dict[str, Any], ctx: ToolContext) -> bool:
        raise RuntimeError(marker)

    audit = FakeAudit()
    tool = CountingTool(_search_spec())
    registry = ToolRegistry(audit=audit, beta_policy=_boom)
    registry.register(tool)
    result = await registry.call("web_search", {"query": "hi"}, _trusted_ctx())
    assert result.ok is False
    assert marker not in (result.error or "")
    assert marker not in repr(audit.entries)
    assert tool.run_calls == 0


async def test_malformed_policy_verdict_fails_closed() -> None:
    tool = CountingTool(_search_spec())

    def _malformed(spec: ToolSpec, args: dict[str, Any], ctx: ToolContext) -> Any:
        return ToolPolicyDecision(allowed="yes")  # type: ignore[arg-type]

    registry = ToolRegistry(beta_policy=_malformed)
    registry.register(tool)
    result = await registry.call("web_search", {"query": "hi"}, _trusted_ctx())
    assert result.ok is False
    assert tool.run_calls == 0


def test_beta_principal_requires_explicit_private_chat() -> None:
    """Stage 1a: private-chat scope must be explicitly supplied as strict bool."""
    with pytest.raises(TypeError):
        BetaPrincipal(telegram_user_id=12345)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        BetaPrincipal(telegram_user_id=12345, private_chat="false")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        BetaPrincipal(telegram_user_id=12345, private_chat=1)  # type: ignore[arg-type]


async def test_beta_denies_omitted_max_results_when_cap_below_default() -> None:
    """Stage 1a: omitted max_results uses effective default 5; cap 4 must deny."""
    registry = ToolRegistry(beta_policy=BetaToolPolicy(max_results=4))
    stub = CountingTool(_search_spec())
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert result.ok is False
    assert "max_results" in (result.error or "").lower()
    assert stub.run_calls == 0


def test_beta_policy_denies_forged_principal_instance() -> None:
    """Stage 1a: duck-typed principal without BetaPrincipal type must deny."""

    class _Forged:
        telegram_user_id = 12345
        private_chat = True

    policy = BetaToolPolicy()
    ctx = ToolContext(agent_id="agent-1", task_id="task-1", beta_principal=_Forged())  # type: ignore[arg-type]
    decision = policy(_search_spec(), {"query": "hello"}, ctx)
    assert isinstance(decision, ToolPolicyDecision)
    assert decision.allowed is False
