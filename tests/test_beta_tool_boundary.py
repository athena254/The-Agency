"""Beta tool-boundary tests (brief 03): fail-closed per-call ToolPolicy.

Scope: ``ToolRegistry.call`` with optional ``beta_policy`` (legacy-open when
omitted). ``BetaToolPolicy`` allows only pre-reviewed harmless tools in
trusted Telegram context; denies absent identity, unclassified names,
MUTATES_STATE/EXECUTES_CODE, memory tools, and web_fetch (B-05 open).
``web_search`` only when query/result-count bounded and actor trusted.

Brief 04: when ``beta_policy`` is set, a usable audit and a persisted,
sanitized pre-execution intent are required before ``tool.run``, and a
persisted sanitized outcome is required before any result is returned.
Audit append failure fails the call closed with a fixed error; denials,
unknown tools and schema violations never execute regardless of audit
availability. Non-beta calls keep best-effort audit semantics.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from agency.kernel.audit import AuditEntry, AuditFilter, AuditLog, BetaAuditLog
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


class SlowTool:
    """Stub that outlives a short registry timeout to exercise timeouts."""

    def __init__(self, spec: ToolSpec, delay_s: float = 0.5) -> None:
        self.spec = spec
        self.delay_s = delay_s
        self.run_calls = 0

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.run_calls += 1
        await asyncio.sleep(self.delay_s)
        return ToolResult(tool=self.spec.name, ok=True, output="late")


class BrokenAudit:
    """Explicit fake: every append raises (durable store outage)."""

    def __init__(self, marker: str = "synthetic-storage-path") -> None:
        self.marker = marker
        self.calls = 0

    async def append(self, *args: Any, **kwargs: Any) -> None:
        self.calls += 1
        raise RuntimeError(f"{self.marker}: storage unavailable")


class FlakyAudit:
    """Explicit fake: first ``fail_after`` appends persist, later ones raise.

    Successful appends return ``None`` on purpose: a falsy return must not be
    mistaken for a failed append.
    """

    def __init__(self, fail_after: int, marker: str = "synthetic-storage-path") -> None:
        self.fail_after = fail_after
        self.marker = marker
        self.entries: list[dict[str, Any]] = []
        self.calls = 0

    async def append(
        self,
        agent: str | None = None,
        action: str | None = None,
        result: str | None = None,
        target: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.calls += 1
        if self.calls > self.fail_after:
            raise RuntimeError(f"{self.marker}: storage unavailable")
        self.entries.append(
            {
                "agent": agent,
                "action": action,
                "result": result,
                "target": target,
                "evidence": evidence or {},
            }
        )


class NoneAudit:
    """Explicit fake: persists and returns None (falsy success)."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(
        self,
        agent: str | None = None,
        action: str | None = None,
        result: str | None = None,
        target: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.entries.append(
            {
                "agent": agent,
                "action": action,
                "result": result,
                "target": target,
                "evidence": evidence or {},
            }
        )


class NoAppendAudit:
    """Audit-shaped object with no append method at all."""


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


@pytest_asyncio.fixture
async def beta_log(tmp_path) -> AsyncGenerator[BetaAuditLog, None]:
    log = BetaAuditLog(tmp_path / "audit.db")
    await log.initialize()
    try:
        yield log
    finally:
        await log.close()


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
    file_memory_store: MemoryStore, beta_log: BetaAuditLog
) -> None:
    """P-P3: memory_query denied in beta; never leaks another owner's rows."""
    await file_memory_store.store(MemoryItem(agent_id="owner-A", content="alpha secret"))
    await file_memory_store.store(MemoryItem(agent_id="owner-B", content="beta secret"))
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    registry.register(MemoryQueryTool())
    ctx = _trusted_ctx(memory_store=file_memory_store)
    result = await registry.call("memory_query", {"query": "secret", "agent_id": "owner-B"}, ctx)
    assert result.ok is False
    assert "memory" in (result.error or "").lower()
    assert (await beta_log.count(AuditFilter(target="memory_query"))) == 1


async def test_beta_denies_memory_write_no_side_effect(
    file_memory_store: MemoryStore, beta_log: BetaAuditLog
) -> None:
    """P-P3: memory_write denied in beta; store stays empty."""
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    registry.register(MemoryWriteTool())
    ctx = _trusted_ctx(memory_store=file_memory_store)
    result = await registry.call("memory_write", {"content": "hello"}, ctx)
    assert result.ok is False
    assert await beta_log.count(AuditFilter(target="memory_write")) == 1
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


async def test_beta_allows_bounded_web_search_stub(beta_log: BetaAuditLog) -> None:
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec(), output=[{"title": "t"}])
    registry.register(stub)
    result = await registry.call("web_search", {"query": "agency beta"}, _trusted_ctx())
    assert result.ok is True
    assert stub.run_calls == 1
    rows = await beta_log.query()
    assert len(rows) == 2 and any(row.action == "tool.call" for row in rows)


async def test_beta_denies_unbounded_web_search() -> None:
    registry = ToolRegistry(beta_policy=BetaToolPolicy(max_query_chars=10, max_results=5))
    stub = CountingTool(_search_spec())
    registry.register(stub)
    long_q = await registry.call("web_search", {"query": "x" * 11}, _trusted_ctx())
    assert long_q.ok is False
    many = await registry.call("web_search", {"query": "short", "max_results": 20}, _trusted_ctx())
    assert many.ok is False
    assert stub.run_calls == 0


async def test_beta_denial_is_audited_with_zero_run_calls(beta_log: BetaAuditLog) -> None:
    """P-P2: direct registry.call denial is audited; tool never runs."""
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec())
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hi"}, _untrusted_ctx())
    assert result.ok is False
    assert stub.run_calls == 0
    rows = await beta_log.query()
    assert len(rows) == 1
    assert rows[0].action == "tool.error"
    assert rows[0].target == "web_search"


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


# --------------------------------------------------------------------------- #
# Brief 04: beta-only durable-audit fail-closed gate
# --------------------------------------------------------------------------- #

_BETA_AUDIT_ERROR = "beta audit unavailable"


async def test_beta_requires_concrete_durable_store_before_running(tmp_path) -> None:
    for audit in (NoneAudit(), FakeAudit(), AuditLog(tmp_path / "ordinary.db")):
        registry = ToolRegistry(audit=audit, beta_policy=BetaToolPolicy())
        stub = CountingTool(_search_spec())
        registry.register(stub)
        result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
        assert result.ok is False
        assert result.error == _BETA_AUDIT_ERROR
        assert stub.run_calls == 0


async def test_beta_durable_store_requires_disk_and_full_sync(tmp_path) -> None:
    for path in (":memory:", "file:memory:?cache=shared", ""):
        with pytest.raises(ValueError):
            BetaAuditLog(path)
    log = BetaAuditLog(tmp_path / "durable.db")
    await log.initialize()
    try:
        conn = log._require_ready()
        cursor = await conn.execute("PRAGMA synchronous")
        assert await cursor.fetchone() == (2,)  # SQLite FULL
        registry = ToolRegistry(audit=log, beta_policy=BetaToolPolicy())
        tool = CountingTool(_search_spec())
        registry.register(tool)
        result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
        assert result.ok is True
        assert tool.run_calls == 1
    finally:
        await log.close()
    reopened = BetaAuditLog(tmp_path / "durable.db")
    await reopened.initialize()
    try:
        rows = await reopened.query(AuditFilter(target="web_search"))
        assert len(rows) == 2
    finally:
        await reopened.close()


async def test_beta_append_does_not_retry_after_typeerror(tmp_path, monkeypatch) -> None:
    log = BetaAuditLog(tmp_path / "durable.db")
    await log.initialize()
    calls = 0
    original = AuditLog.append

    async def commit_then_fail(self: AuditLog, entry: AuditEntry) -> str:
        nonlocal calls
        calls += 1
        await original(self, entry)
        raise TypeError("private-after-commit")

    monkeypatch.setattr(AuditLog, "append", commit_then_fail)
    try:
        registry = ToolRegistry(audit=log, beta_policy=BetaToolPolicy())
        tool = CountingTool(_search_spec())
        registry.register(tool)
        result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
        assert result.error == _BETA_AUDIT_ERROR
        assert tool.run_calls == 0
        assert calls == 1
        assert await log.count() == 1
    finally:
        await log.close()


async def test_beta_refuses_degraded_sync_before_execution(beta_log: BetaAuditLog) -> None:
    conn = beta_log._require_ready()
    await conn.execute("PRAGMA synchronous=NORMAL")
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    tool = CountingTool(_search_spec())
    registry.register(tool)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert result.error == _BETA_AUDIT_ERROR
    assert tool.run_calls == 0


async def test_beta_refuses_degraded_journal_before_execution(beta_log: BetaAuditLog) -> None:
    conn = beta_log._require_ready()
    await conn.execute("PRAGMA journal_mode=DELETE")
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    tool = CountingTool(_search_spec())
    registry.register(tool)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert result.error == _BETA_AUDIT_ERROR
    assert tool.run_calls == 0


async def test_beta_noop_append_returning_an_id_never_runs(tmp_path, monkeypatch) -> None:
    log = BetaAuditLog(tmp_path / "durable.db")
    await log.initialize()

    async def noop(self: AuditLog, entry: AuditEntry) -> str:
        return entry.entry_id

    monkeypatch.setattr(AuditLog, "append", noop)
    try:
        registry = ToolRegistry(audit=log, beta_policy=BetaToolPolicy())
        tool = CountingTool(_search_spec())
        registry.register(tool)
        result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
        assert result.error == _BETA_AUDIT_ERROR
        assert tool.run_calls == 0
        assert await log.count() == 0
    finally:
        await log.close()


async def test_beta_ignores_instance_append_override(beta_log: BetaAuditLog, monkeypatch) -> None:
    async def fake(entry: AuditEntry) -> str:
        return entry.entry_id

    monkeypatch.setattr(beta_log, "append_beta", fake)
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    tool = CountingTool(_search_spec())
    registry.register(tool)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert result.ok is True
    assert tool.run_calls == 1
    assert await beta_log.count() == 2


async def test_beta_rejections_and_tool_errors_never_persist_user_text(tmp_path) -> None:
    marker = "private-input-marker"
    log = BetaAuditLog(tmp_path / "audit.db")
    await log.initialize()
    try:
        registry = ToolRegistry(audit=log, beta_policy=BetaToolPolicy())
        tool = CountingTool(_search_spec())
        registry.register(tool)
        denied = await registry.call("web_search", {marker: "value"}, _trusted_ctx())
        assert denied.ok is False
        assert marker not in repr(await log.query())
        tool.spec.parameters["additionalProperties"] = False
        denied = await registry.call("web_search", {"query": "ok", marker: "value"}, _trusted_ctx())
        assert denied.ok is False
        assert marker not in repr(await log.query())

        class ErrorTool(CountingTool):
            async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
                raise RuntimeError(marker)

        failing = ErrorTool(_search_spec())
        another = ToolRegistry(audit=log, beta_policy=BetaToolPolicy())
        another.register(failing)
        result = await another.call("web_search", {"query": "ok"}, _trusted_ctx())
        assert result.ok is False
        assert marker not in repr(result)
        assert marker not in repr(await log.query())
    finally:
        await log.close()


async def test_beta_absent_audit_blocks_tool_execution() -> None:
    """Beta allowed call with no audit at all must not run the tool."""
    registry = ToolRegistry(beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec())
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert result.ok is False
    assert result.error == _BETA_AUDIT_ERROR
    assert stub.run_calls == 0


async def test_beta_audit_without_append_method_blocks_tool_execution() -> None:
    """A non-appendable audit object is not a usable durable sink."""
    registry = ToolRegistry(audit=NoAppendAudit(), beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec())
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert result.ok is False
    assert result.error == _BETA_AUDIT_ERROR
    assert stub.run_calls == 0


async def test_beta_preflight_append_exception_blocks_tool_execution(
    beta_log: BetaAuditLog, monkeypatch
) -> None:
    """Intent append failure => fixed error, no run, no exception leak."""
    calls = 0

    async def fail(self: AuditLog, entry: AuditEntry) -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("synthetic-private-value")

    monkeypatch.setattr(AuditLog, "append", fail)
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec())
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert stub.run_calls == 0
    assert result.ok is False
    assert result.error == _BETA_AUDIT_ERROR
    assert calls == 1
    assert "synthetic-private-value" not in (result.error or "")
    assert "RuntimeError" not in (result.error or "")


def _fail_outcome(log: BetaAuditLog, monkeypatch) -> None:
    original = AuditLog.append
    calls = 0

    async def append(self: AuditLog, entry: AuditEntry) -> str:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("synthetic-storage-path")
        return await original(self, entry)

    monkeypatch.setattr(AuditLog, "append", append)


async def test_beta_outcome_append_exception_returns_failure_not_success(
    beta_log: BetaAuditLog, monkeypatch
) -> None:
    """Tool ran, outcome append failed => fixed failure, never success."""
    _fail_outcome(beta_log, monkeypatch)
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec(), output=[{"title": "t"}])
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert stub.run_calls == 1
    assert result.ok is False
    assert result.error == _BETA_AUDIT_ERROR
    assert result.output is None
    assert result.evidence.get("status") == "INDETERMINATE"
    assert await beta_log.count() == 1
    assert "synthetic-storage-path" not in (result.error or "")


async def test_beta_failed_intent_append_suspends_next_call(
    beta_log: BetaAuditLog, monkeypatch
) -> None:
    original = AuditLog.append
    calls = 0

    async def fail_once(self: AuditLog, entry: AuditEntry) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("private-storage-failure")
        return await original(self, entry)

    monkeypatch.setattr(AuditLog, "append", fail_once)
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    tool = CountingTool(_search_spec())
    registry.register(tool)
    first = await registry.call("web_search", {"query": "first"}, _trusted_ctx())
    assert first.ok is False and first.error == _BETA_AUDIT_ERROR
    second = await registry.call("web_search", {"query": "second"}, _trusted_ctx())
    assert second.ok is False and second.error == _BETA_AUDIT_ERROR
    assert calls == 1 and tool.run_calls == 0


async def test_beta_failed_outcome_append_suspends_next_call(
    beta_log: BetaAuditLog, monkeypatch
) -> None:
    _fail_outcome(beta_log, monkeypatch)
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    tool = CountingTool(_search_spec())
    registry.register(tool)
    first = await registry.call("web_search", {"query": "first"}, _trusted_ctx())
    assert first.ok is False and first.evidence.get("status") == "INDETERMINATE"
    second = await registry.call("web_search", {"query": "second"}, _trusted_ctx())
    assert second.ok is False and second.error == _BETA_AUDIT_ERROR
    assert tool.run_calls == 1
    assert await beta_log.count() == 1


async def test_beta_timeout_is_audited_before_returning(beta_log: BetaAuditLog) -> None:
    """Timeout outcome still needs a persisted record, intent first."""
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy(), default_timeout_s=0.05)
    stub = SlowTool(_search_spec(), delay_s=0.5)
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert stub.run_calls == 1
    assert result.ok is False
    assert result.error == "beta tool failed"
    entries = await beta_log.query()
    assert len(entries) == 2
    assert any(e.evidence and e.evidence.get("phase") == "intent" for e in entries)
    assert any(e.evidence and e.evidence.get("phase") == "outcome" for e in entries)


async def test_beta_timeout_with_outcome_append_failure_fails_closed(
    beta_log: BetaAuditLog, monkeypatch
) -> None:
    """Timeout plus outcome append failure must not return the tool error."""
    _fail_outcome(beta_log, monkeypatch)
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy(), default_timeout_s=0.05)
    stub = SlowTool(_search_spec(), delay_s=0.5)
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert stub.run_calls == 1
    assert result.ok is False
    assert result.error == _BETA_AUDIT_ERROR
    assert result.evidence.get("status") == "INDETERMINATE"
    assert "timed out" not in (result.error or "")
    assert "synthetic-private-value" not in (result.error or "")


async def test_beta_tool_exception_outcome_append_failure_fails_closed(
    beta_log: BetaAuditLog, monkeypatch
) -> None:
    """A raising tool whose outcome cannot be persisted reports failure."""

    class _BoomTool(CountingTool):
        async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            self.run_calls += 1
            raise RuntimeError("synthetic-tool-private-value")

    _fail_outcome(beta_log, monkeypatch)
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    stub = _BoomTool(_search_spec())
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert stub.run_calls == 1
    assert result.ok is False
    assert result.error == _BETA_AUDIT_ERROR
    assert result.evidence.get("status") == "INDETERMINATE"
    assert "synthetic-tool-private-value" not in (result.error or "")


async def test_beta_denial_unknown_and_schema_errors_fail_closed_without_audit() -> None:
    """Denial/unknown/schema never execute, with or without an audit sink."""
    registry = ToolRegistry(beta_policy=BetaToolPolicy(max_query_chars=10))
    stub = CountingTool(_search_spec())
    registry.register(stub)

    denial = await registry.call("web_search", {"query": "hi"}, _untrusted_ctx())
    assert denial.ok is False
    assert "trusted" in (denial.error or "").lower()

    unknown = await registry.call("no_such_tool", {}, _trusted_ctx())
    assert unknown.ok is False
    assert "unknown tool" in (unknown.error or "")

    schema = await registry.call("web_search", {}, _trusted_ctx())
    assert schema.ok is False
    assert "invalid args" in (schema.error or "")

    assert stub.run_calls == 0


async def test_beta_denial_with_failing_audit_still_denies_without_leak() -> None:
    """Audit outage must not turn a denial into a run or leak the outage."""
    audit = BrokenAudit(marker="synthetic-private-value")
    registry = ToolRegistry(audit=audit, beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec())
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hi"}, _untrusted_ctx())
    assert result.ok is False
    assert result.error != _BETA_AUDIT_ERROR
    assert "trusted" in (result.error or "").lower()
    assert "synthetic-private-value" not in (result.error or "")
    assert stub.run_calls == 0
    assert audit.calls == 0


async def test_beta_policy_error_with_absent_audit_does_not_leak() -> None:
    """Policy exception detail stays hidden even when audit is unusable."""

    def _boom(spec: ToolSpec, args: dict[str, Any], ctx: ToolContext) -> bool:
        raise RuntimeError("synthetic-policy-private-value")

    registry = ToolRegistry(beta_policy=_boom)  # type: ignore[arg-type]
    stub = CountingTool(_search_spec())
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hi"}, _trusted_ctx())
    assert result.ok is False
    assert stub.run_calls == 0
    assert "synthetic-policy-private-value" not in (result.error or "")
    assert "synthetic-policy-private-value" not in repr(result)


async def test_beta_append_returning_none_is_not_an_acknowledgement() -> None:
    """A fake claiming success, including None, must never grant execution."""
    audit = NoneAudit()
    registry = ToolRegistry(audit=audit, beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec(), output=[{"title": "t"}])
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert result.error == _BETA_AUDIT_ERROR
    assert stub.run_calls == 0
    assert not audit.entries


async def test_beta_audit_payload_has_no_query_args_or_result_text(
    beta_log: BetaAuditLog,
) -> None:
    """Sanitized records carry ids/status only, never query or output text."""
    secret = "super-secret-query-text"
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec(), output=[{"title": "result body text"}])
    registry.register(stub)
    result = await registry.call("web_search", {"query": secret}, _trusted_ctx())
    assert result.ok is True
    rows = await beta_log.query()
    assert len(rows) == 2
    blob = repr(rows)
    assert secret not in blob
    assert "result body text" not in blob
    for entry in rows:
        assert entry.evidence is not None
        assert "args" not in entry.evidence
        assert "query" not in entry.evidence
        assert "output" not in entry.evidence


async def test_beta_calls_have_opaque_distinct_correlated_ids(beta_log: BetaAuditLog) -> None:
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec())
    registry.register(stub)
    ctx = _trusted_ctx(agent_id="agent-1", task_id="task-1")
    for _ in range(2):
        assert (await registry.call("web_search", {"query": "hello"}, ctx)).ok
    rows = await beta_log.query()
    assert len(rows) == 4
    pairs: dict[str, set[str]] = {}
    for row in rows:
        assert row.evidence is not None
        call_id = row.evidence["call_id"]
        assert isinstance(call_id, str) and len(call_id) >= 32
        pairs.setdefault(call_id, set()).add(row.evidence["phase"])
        assert row.agent is None and row.task is None
    assert len(pairs) == 2
    assert all(phases == {"intent", "outcome"} for phases in pairs.values())
    blob = repr(rows)
    for identity in ("agent-1", "task-1"):
        assert identity not in blob
        assert hashlib.sha256(identity.encode()).hexdigest() not in blob


async def test_live_pending_call_does_not_permanently_suspend_registry(
    beta_log: BetaAuditLog,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingTool(CountingTool):
        async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            self.run_calls += 1
            if self.run_calls == 1:
                started.set()
                await release.wait()
            return ToolResult(tool=self.spec.name, ok=True, output="ok")

    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    tool = BlockingTool(_search_spec())
    registry.register(tool)
    first = asyncio.create_task(registry.call("web_search", {"query": "first"}, _trusted_ctx()))
    try:
        await asyncio.wait_for(started.wait(), 2)
        overlap = await registry.call("web_search", {"query": "second"}, _trusted_ctx())
        assert overlap.ok is False
        assert tool.run_calls == 1
    finally:
        release.set()
    assert (await asyncio.wait_for(first, 2)).ok is True
    assert await beta_log.has_pending_calls() is False
    after = await registry.call("web_search", {"query": "third"}, _trusted_ctx())
    assert after.ok is True
    assert tool.run_calls == 2


async def test_beta_cancellation_keeps_pending_intent_and_suspends_work(
    beta_log: BetaAuditLog,
) -> None:
    started = asyncio.Event()

    class BlockingTool(CountingTool):
        async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            self.run_calls += 1
            started.set()
            await asyncio.Event().wait()
            return ToolResult(tool=self.spec.name, ok=True, output="should not complete")

    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    tool = BlockingTool(_search_spec())
    registry.register(tool)
    task = asyncio.create_task(registry.call("web_search", {"query": "secret"}, _trusted_ctx()))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    rows = await beta_log.query()
    assert len(rows) == 1
    assert rows[0].result == "pending"
    assert rows[0].evidence and rows[0].evidence["phase"] == "intent"
    assert rows[0].evidence["call_id"]
    again = await registry.call("web_search", {"query": "next"}, _trusted_ctx())
    assert again.ok is False and again.error == _BETA_AUDIT_ERROR
    assert tool.run_calls == 1
    assert await beta_log.count() == 1


async def test_beta_cancellation_during_outcome_append_never_claims_success(
    beta_log: BetaAuditLog, monkeypatch
) -> None:
    outcome_started = asyncio.Event()
    original = AuditLog.append

    async def stall_outcome(self: AuditLog, entry: AuditEntry) -> str:
        if entry.evidence and entry.evidence.get("phase") == "outcome":
            outcome_started.set()
            await asyncio.Event().wait()
        return await original(self, entry)

    monkeypatch.setattr(AuditLog, "append", stall_outcome)
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    tool = CountingTool(_search_spec())
    registry.register(tool)
    task = asyncio.create_task(registry.call("web_search", {"query": "first"}, _trusted_ctx()))
    await asyncio.wait_for(outcome_started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert tool.run_calls == 1
    assert [row.result for row in await beta_log.query()] == ["pending"]
    result = await registry.call("web_search", {"query": "second"}, _trusted_ctx())
    assert result.ok is False and result.error == _BETA_AUDIT_ERROR
    assert tool.run_calls == 1


async def test_beta_cancel_after_outcome_commit_suspends_same_registry(
    beta_log: BetaAuditLog, monkeypatch
) -> None:
    committed = asyncio.Event()
    original = BetaAuditLog.append_beta

    async def commit_then_stall(self: BetaAuditLog, entry: AuditEntry) -> str:
        entry_id = await original(self, entry)
        if entry.evidence and entry.evidence.get("phase") == "outcome" and not committed.is_set():
            committed.set()
            await asyncio.Event().wait()
        return entry_id

    monkeypatch.setattr(BetaAuditLog, "append_beta", commit_then_stall)
    registry = ToolRegistry(audit=beta_log, beta_policy=BetaToolPolicy())
    tool = CountingTool(_search_spec())
    registry.register(tool)
    task = asyncio.create_task(registry.call("web_search", {"query": "first"}, _trusted_ctx()))
    await asyncio.wait_for(committed.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert await beta_log.count() == 2
    result = await registry.call("web_search", {"query": "second"}, _trusted_ctx())
    assert result.ok is False and result.error == _BETA_AUDIT_ERROR
    assert tool.run_calls == 1


async def test_beta_reopened_pending_call_blocks_new_work(tmp_path) -> None:
    path = tmp_path / "audit.db"
    previous = BetaAuditLog(path)
    await previous.initialize()
    await previous.append_beta(
        AuditEntry(
            target="web_search",
            action="tool.call",
            result="pending",
            evidence={"call_id": "a" * 32, "tool": "web_search", "phase": "intent"},
        )
    )
    await previous.close()
    reopened = BetaAuditLog(path)
    await reopened.initialize()
    try:
        registry = ToolRegistry(audit=reopened, beta_policy=BetaToolPolicy())
        tool = CountingTool(_search_spec())
        registry.register(tool)
        result = await registry.call("web_search", {"query": "next"}, _trusted_ctx())
        assert result.ok is False and result.error == _BETA_AUDIT_ERROR
        assert result.evidence.get("status") == "INDETERMINATE"
        assert tool.run_calls == 0
        assert await reopened.count() == 1
    finally:
        await reopened.close()


async def test_beta_success_requires_persisted_intent_then_outcome(tmp_path) -> None:
    """Real AuditLog: intent + outcome rows persist and survive reopen."""
    db_path = tmp_path / "audit.db"
    log = BetaAuditLog(db_path)
    await log.initialize()
    registry = ToolRegistry(audit=log, beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec(), output=[{"title": "t"}])
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert result.ok is True
    assert stub.run_calls == 1
    entries = await log.query(AuditFilter(target="web_search"))
    assert len(entries) == 2
    assert {e.result for e in entries} == {"pending", "ok"}
    assert all(e.evidence and e.evidence.get("phase") for e in entries)
    assert all("hello" not in repr(e.evidence) for e in entries)
    await log.close()

    reopened = AuditLog(db_path)
    await reopened.initialize()
    persisted = await reopened.query(AuditFilter(target="web_search"))
    assert len(persisted) == 2
    await reopened.close()


async def test_beta_success_without_durable_intent_never_runs(tmp_path) -> None:
    """Uninitialized real AuditLog cannot silently look durable."""
    log = AuditLog(tmp_path / "audit.db")
    registry = ToolRegistry(audit=log, beta_policy=BetaToolPolicy())
    stub = CountingTool(_search_spec())
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert result.ok is False
    assert result.error == _BETA_AUDIT_ERROR
    assert stub.run_calls == 0


async def test_nonbeta_audit_failure_stays_best_effort() -> None:
    """Without beta_policy, an audit outage must not break tool calls."""
    registry = ToolRegistry(audit=BrokenAudit(), beta_policy=None)
    stub = CountingTool(_search_spec(), output=[{"title": "t"}])
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert result.ok is True
    assert stub.run_calls == 1


async def test_nonbeta_absent_audit_still_runs() -> None:
    """Legacy-open path: no audit object at all changes nothing."""
    registry = ToolRegistry(audit=None, beta_policy=None)
    stub = CountingTool(_search_spec(), output=[{"title": "t"}])
    registry.register(stub)
    result = await registry.call("web_search", {"query": "hello"}, _trusted_ctx())
    assert result.ok is True
    assert stub.run_calls == 1
