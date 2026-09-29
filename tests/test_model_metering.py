"""Bounded metering slice (frozen spec docs/SPEC_MODEL_METERING_V1.md).

RED then GREEN: real temp quota + audit DBs, fake transport via monkeypatch
of the wrapped adapter's provider method. No live network.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agency.kernel.audit import BetaAuditLog
from agency.llm.adapter import LLMAdapter
from agency.llm.metered import MeteredBinding, MeteredDenied, MeteredError, MeteredModel
from agency.telegram.budget_store import BetaBudgetStore, BudgetLimits
from agency.tools.base import BetaPrincipal, ToolContext, ToolResult, ToolSpec
from agency.tools.driver import ToolDriver


async def _setup(tmp_path, **limits: int):
    store = BetaBudgetStore(
        str(tmp_path / "quota.db"), limits=BudgetLimits(**limits) if limits else None
    )
    await store.initialize()
    audit = BetaAuditLog(str(tmp_path / "audit.db"))
    await audit.initialize()
    res = await store.reserve_request("bot", 1, 7)
    assert res.allowed and res.capability
    await store.mark_running("bot", 1, 7, capability=res.capability)
    principal = BetaPrincipal(telegram_user_id=7, private_chat=True)
    inner = LLMAdapter(provider="echo", model="echo")
    binding = MeteredBinding(
        bot_id="bot",
        update_id=1,
        user_id=7,
        capability=res.capability or "",
        principal=principal,
        provider=inner.provider,
        model=inner.model,
    )
    metered = MeteredModel(inner, binding, store, audit)
    return store, audit, principal, inner, binding, metered


def _ctx(principal: BetaPrincipal, audit: BetaAuditLog) -> ToolContext:
    return ToolContext(agent_id="agent-1", task_id="task-1", audit=audit, beta_principal=principal)


class _Registry:
    def __init__(self) -> None:
        self._specs = [ToolSpec(name="web_search", description="s")]
        # Strict/beta registry marker: trusted test setup uses real metering.
        self._beta_policy = object()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_specs(self) -> list[ToolSpec]:
        return list(self._specs)

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.calls.append((name, args))
        return ToolResult(tool=name, ok=True, output={"r": 1}, evidence={})


async def test_positive_loop_and_forced_final_counts(tmp_path) -> None:
    _store, audit, principal, _inner_unused, _binding, metered = await _setup(tmp_path)
    calls: list[str] = []
    orig = LLMAdapter.generate

    async def spy(self: LLMAdapter, prompt: str, context=None) -> str:
        assert context == {}
        calls.append(prompt)
        if len(calls) == 1:
            return '{"action": {"name": "web_search", "args": {}}}'
        return '{"final": "done"}'

    LLMAdapter.generate = spy  # type: ignore[method-assign]
    try:
        driver = ToolDriver(_Registry(), metered, max_tool_iterations=1)
        result = await driver.run("task", "sys", _ctx(principal, audit))
    finally:
        LLMAdapter.generate = orig  # type: ignore[method-assign]
    assert result.status == "completed"
    assert result.final_answer == "done"
    assert result.llm_calls == 2
    assert len(calls) == 2


async def test_bare_model_denied_in_beta_path(tmp_path) -> None:
    _store, audit, principal, inner, _binding, _metered = await _setup(tmp_path)
    driver = ToolDriver(_Registry(), inner)
    result = await driver.run("task", "sys", _ctx(principal, audit))
    assert result.status == "tool_error"
    assert result.final_answer == "Beta tool unavailable."


async def test_quota_denial_zero_calls(tmp_path, monkeypatch) -> None:
    _store, _audit_unused, principal, _inner_unused, _binding_unused, metered = await _setup(
        tmp_path, model_calls_per_request=1
    )
    ctx = {"task_id": "t", "strict": True, "beta_principal": principal}
    calls: list[str] = []

    async def counting(self: LLMAdapter, prompt: str, context=None) -> str:
        calls.append(prompt)
        return '{"final": "one"}'

    monkeypatch.setattr(LLMAdapter, "generate", counting)
    await metered.generate('{"final": "one"}', ctx)
    assert len(calls) == 1
    try:
        await metered.generate('{"final": "two"}', ctx)
    except MeteredDenied:
        pass
    else:
        raise AssertionError("second generate must deny")
    assert len(calls) == 1  # quota denial made zero provider calls


async def test_missing_wrong_capability_principal(tmp_path) -> None:
    store, audit, principal, inner, _binding_unused, metered = await _setup(tmp_path)
    good = {"task_id": "t", "strict": True, "beta_principal": principal}
    other = BetaPrincipal(telegram_user_id=9, private_chat=True)
    wrong_ctx = {"task_id": "t", "strict": True, "beta_principal": other}
    for bad_ctx in ({"task_id": "t"}, wrong_ctx):
        try:
            await metered.generate("hi", bad_ctx)
        except MeteredDenied:
            pass
        else:
            raise AssertionError("must deny")
    foreign = MeteredBinding(
        bot_id="bot",
        update_id=1,
        user_id=7,
        capability="foreign-capability",
        principal=principal,
        provider=inner.provider,
        model=inner.model,
    )
    foreign_metered = MeteredModel(inner, foreign, store, audit)
    try:
        await foreign_metered.generate("hi", good)
    except MeteredDenied:
        pass
    else:
        raise AssertionError("foreign capability must deny")


async def test_context_override_denied(tmp_path) -> None:
    _store, _audit, principal, _inner, _binding, metered = await _setup(tmp_path)
    for override in (
        {"beta_principal": principal, "provider": "x"},
        {"beta_principal": principal, "model": "y"},
    ):
        try:
            await metered.generate("hi", override)
        except MeteredDenied:
            pass
        else:
            raise AssertionError("override must deny")


async def test_no_secret_in_audit_errors_repr(tmp_path, monkeypatch) -> None:
    _store, _audit_unused, principal, _inner_unused, binding, metered = await _setup(tmp_path)
    secret = "super-secret-capability-value"
    assert secret not in repr(binding) and secret not in repr(metered)
    ctx = {"task_id": "t", "strict": True, "beta_principal": principal}

    async def boom(self: LLMAdapter, prompt: str, context=None) -> str:
        raise RuntimeError(f"leak {secret} prompt={prompt}")

    monkeypatch.setattr(LLMAdapter, "generate", boom)
    try:
        await metered.generate(f"private prompt {secret}", ctx)
    except MeteredError as exc:
        assert secret not in str(exc)


async def test_durability_failure_before_and_after(tmp_path, monkeypatch) -> None:
    store, audit, principal, _inner_unused, _binding_unused, metered = await _setup(tmp_path)
    ctx = {"task_id": "t", "strict": True, "beta_principal": principal}
    monkeypatch.setattr(
        LLMAdapter,
        "generate",
        lambda self, p, c=None: (_ for _ in ()).throw(
            AssertionError("no provider on reserve failure")
        ),
    )

    async def fail_reserve(*a: object, **k: object) -> bool:
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "reserve_model_call", fail_reserve)
    try:
        await metered.generate("hi", ctx)
    except MeteredDenied:
        pass
    else:
        raise AssertionError("reserve failure must deny")

    async def ok_reserve(*a: object, **k: object) -> bool:
        return True

    async def fail_claim(entry: object) -> bool:
        raise RuntimeError("audit down")

    monkeypatch.setattr(store, "reserve_model_call", ok_reserve)
    monkeypatch.setattr(audit, "claim_intent", fail_claim)
    try:
        await metered.generate("hi", ctx)
    except MeteredDenied:
        pass
    else:
        raise AssertionError("claim failure must deny")

    async def ok_claim(entry: object) -> bool:
        return True

    async def fail_outcome(entry: object) -> str:
        raise RuntimeError("outcome down")

    async def ok_generate(self: LLMAdapter, prompt: str, context=None) -> str:
        return "hello"

    monkeypatch.setattr(audit, "claim_intent", ok_claim)
    monkeypatch.setattr(audit, "append_beta", fail_outcome)
    monkeypatch.setattr(LLMAdapter, "generate", ok_generate)
    try:
        await metered.generate("hi", ctx)
    except RuntimeError:
        pass
    else:
        raise AssertionError("outcome failure must never return success")


async def test_timeout_pending_and_closed_store(tmp_path, monkeypatch) -> None:
    store, audit, principal, inner, binding, metered = await _setup(tmp_path)
    ctx = {"task_id": "t", "strict": True, "beta_principal": principal}

    async def slow(self: LLMAdapter, prompt: str, context=None) -> str:
        await asyncio.sleep(5)
        return "late"

    monkeypatch.setattr(LLMAdapter, "generate", slow)
    fast = MeteredModel(inner, binding, store, audit, timeout_s=0.05)
    try:
        await fast.generate("hi", ctx)
    except MeteredError:
        pass
    else:
        raise AssertionError("timeout must raise MeteredError")
    assert await audit.has_pending_calls() is True
    await store.close()
    try:
        await metered.generate("hi", ctx)
    except MeteredDenied:
        pass
    else:
        raise AssertionError("closed store must deny")


async def test_concurrent_second_blocked(tmp_path, monkeypatch) -> None:
    _store, audit, principal, _inner_unused, binding, metered = await _setup(tmp_path)
    ctx = {"task_id": "t", "strict": True, "beta_principal": principal}
    secret = binding.capability  # real request secret: must never reach audit
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking(self: LLMAdapter, prompt: str, context=None) -> str:
        started.set()
        await release.wait()
        return "hello"

    monkeypatch.setattr(LLMAdapter, "generate", blocking)
    first = asyncio.create_task(metered.generate("hi", ctx))
    await asyncio.wait_for(started.wait(), timeout=10)
    second = asyncio.create_task(metered.generate("hi-again", ctx))
    await asyncio.sleep(0.2)
    assert not second.done() or isinstance(second.exception(), MeteredDenied)
    release.set()
    assert await first == "hello"
    second.cancel()
    try:
        await second
    except (asyncio.CancelledError, MeteredDenied, MeteredError):
        pass

    else:
        raise AssertionError("must raise MeteredError")
    rows = await audit.query()
    blob = repr([(r.result, r.evidence) for r in rows])
    assert secret not in blob and "private prompt" not in blob
