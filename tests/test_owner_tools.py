"""Owner registry/driver contracts: real durable seams, HTTP transport leaf only."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from agency.admission.budget import OwnerBudgetAdapter
from agency.admission.models import (
    OwnerExecutionGrant,
    OwnerMeteredBinding,
    OwnerPrincipal,
    PilotError,
)
from agency.admission.policy import OwnerToolPolicy
from agency.admission.store import OwnerPilotStore
from agency.kernel.audit import BetaAuditLog
from agency.llm.adapter import LLMAdapter
from agency.llm.metered import MeteredModel
from agency.telegram.budget_store import BetaBudgetStore
from agency.tools.base import ToolContext
from agency.tools.builtin.calculator import CalculatorTool
from agency.tools.registry import ToolRegistry, build_default_registry


@pytest.fixture
async def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    owner = OwnerPrincipal("PRIVATE_OWNER", "local", "PRIVATE_SUBJECT")
    store = OwnerPilotStore(str(tmp_path / "owner.db"))
    backend = BetaBudgetStore(str(tmp_path / "quota.db"))
    budget = OwnerBudgetAdapter(backend, store)
    audit = BetaAuditLog(str(tmp_path / "audit.db"))
    requests: list[httpx.Request] = []
    replies = ['{"final":"done"}']
    client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": replies.pop(0)}}]})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handle), **kw)
    )
    try:
        await store.initialize()
        await backend.initialize()
        await audit.initialize()
        _, thread = await store.create_thread(owner, "thread", "Synthetic")
        admitted = await store.admit(
            owner, "turn", operation="turn", payload={"thread_id": thread.id, "message": "prompt"}
        )
        assert admitted.task_id is not None
        lease = await budget.reserve_request(owner, admitted.request_id, admitted.task_id)
        await budget.mark_running(lease)
        grant = OwnerExecutionGrant(lease, budget)
        inner = LLMAdapter(
            provider="openai",
            model="synthetic-model",
            api_key="SYNTHETIC_PRIVATE_KEY",
            base_url="https://provider.invalid/v1",
            timeout=10.0,
        )
        binding = OwnerMeteredBinding(grant, "openai", "synthetic-model", "synthetic-config")
        model = MeteredModel(inner, binding, budget, audit)
        ctx = ToolContext("PRIVATE_AGENT", lease.task_id, audit=audit, owner_grant=grant)
        yield locals()
    finally:
        await audit.close()
        await budget.close()
        await store.close()


@pytest.mark.parametrize("factory", [False, True])
async def test_owner_registry_reserves_grant_lowered_cap_before_effect(runtime, factory) -> None:
    grant = replace(runtime["grant"], max_tool_calls=1)
    ctx = runtime["ctx"]
    ctx.owner_grant = grant
    policy = OwnerToolPolicy()
    if factory:
        registry = build_default_registry([CalculatorTool()], owner_policy=policy, audit=ctx.audit)
    else:
        registry = ToolRegistry(owner_policy=policy, audit=ctx.audit)
        registry.register(CalculatorTool())
    assert registry._audit is ctx.audit
    first = await registry.call("calculator", {"expression": "2+3"}, ctx)
    assert first.ok and first.output["result"] == 5
    with pytest.raises(PilotError) as blocked:
        await registry.call("calculator", {"expression": "2+3"}, ctx)
    assert blocked.value.code == "quota"
    row = await runtime["store"].get(runtime["owner"], ctx.task_id)
    assert row.tool_attempts == 1
    assert not await ctx.audit.has_pending_calls()
    assert runtime["requests"] == []


@pytest.mark.parametrize("boundary", ["reservation", "intent", "outcome"])
@pytest.mark.parametrize("mutation", ["revoke", "cancel", "task", "grant", "audit", "policy"])
async def test_owner_binding_rechecked_after_persistence(runtime, monkeypatch, boundary, mutation):
    from agency.tools.builtin.utc_time import UtcTimeTool

    effects = []

    def clock():
        effects.append(True)
        return datetime(2026, 1, 1, tzinfo=UTC)

    ctx = runtime["ctx"]
    registry = build_default_registry(
        [UtcTimeTool(clock)], owner_policy=OwnerToolPolicy(), audit=ctx.audit
    )

    async def mutate():
        if mutation == "revoke":
            runtime["budget"].revoke(runtime["lease"])
        elif mutation == "cancel":
            await runtime["store"].request_cancel(runtime["owner"], ctx.task_id, "cancel")
        elif mutation == "task":
            ctx.task_id = "foreign-task"
        elif mutation == "grant":
            ctx.owner_grant = replace(runtime["grant"])
        elif mutation == "audit":
            ctx.audit = object()
        else:
            registry._owner_policy = OwnerToolPolicy()

    if boundary == "reservation":
        original = runtime["budget"].reserve_tool_call

        async def reserve(*a, **kw):
            result = await original(*a, **kw)
            await mutate()
            return result

        monkeypatch.setattr(runtime["budget"], "reserve_tool_call", reserve)
    else:
        method = "claim_intent" if boundary == "intent" else "append_beta"
        original = getattr(BetaAuditLog, method)

        async def persist(self, entry):
            result = await original(self, entry)
            await mutate()
            return result

        monkeypatch.setattr(BetaAuditLog, method, persist)
    with pytest.raises(PilotError) as denied:
        await registry.call("utc_time", {}, ctx)
    assert denied.value.uncertain is (boundary == "outcome")
    assert len(effects) == (1 if boundary == "outcome" else 0)
    assert runtime["requests"] == []


@pytest.mark.parametrize("mode", ["normal", "forced", "final_only"])
async def test_real_owner_driver_uses_allowed_context_at_both_model_leaves(runtime, mode):
    from agency.tools.driver import ToolDriver

    registry = build_default_registry(
        [CalculatorTool()], owner_policy=OwnerToolPolicy(), audit=runtime["audit"]
    )
    if mode != "final_only":
        runtime["replies"][:] = [
            '{"action":{"name":"calculator","args":{"expression":"2+3"}}}',
            '{"final":"five"}',
        ]
    result = await ToolDriver(
        registry, runtime["model"], max_tool_iterations=1 if mode == "forced" else 6
    ).run("synthetic task", "synthetic system", runtime["ctx"])
    assert result.status == "completed"
    assert result.llm_calls == (1 if mode == "final_only" else 2)
    assert len(result.steps) == (0 if mode == "final_only" else 1)
    row = await runtime["store"].get(runtime["owner"], runtime["ctx"].task_id)
    assert row.model_attempts == result.llm_calls
    assert row.tool_attempts == len(result.steps)
    wire = b"".join(request.content for request in runtime["requests"]).decode()
    for private in [
        "PRIVATE_OWNER",
        "PRIVATE_SUBJECT",
        "PRIVATE_AGENT",
        runtime["lease"].capability,
        "owner_grant",
        "beta_principal",
        "SYNTHETIC_PRIVATE_KEY",
    ]:
        assert private not in wire


@pytest.mark.parametrize(
    "case",
    [
        "open_registry",
        "fake_registry",
        "subclass_registry",
        "fake_model",
        "subclass_model",
        "fake_policy",
        "subclass_policy",
        "task",
        "audit",
        "copied_grant",
        "missing_grant",
        "mixed_beta",
        "private_handle",
    ],
)
async def test_owner_final_only_cannot_bypass_exact_binding(runtime, case):
    from agency.llm.metered import MeteredDenied
    from agency.tools.base import BetaPrincipal
    from agency.tools.driver import ToolDriver

    registry = build_default_registry(
        [CalculatorTool()], owner_policy=OwnerToolPolicy(), audit=runtime["audit"]
    )
    ctx, model = runtime["ctx"], runtime["model"]
    calls = []

    class FakeModel:
        binding = model.binding

        async def generate(self, *a, **kw):
            calls.append(True)
            return '{"final":"bypass"}'

    class ModelSubclass(MeteredModel):
        async def generate(self, *a, **kw):
            calls.append(True)
            return '{"final":"bypass"}'

    class RegistrySubclass(ToolRegistry):
        pass

    class FakeRegistry:
        _owner_policy = registry._owner_policy
        _beta_policy = None
        _audit = registry._audit

        def list_specs(self):
            return registry.list_specs()

    class PolicySubclass(OwnerToolPolicy):
        pass

    if case == "open_registry":
        registry = build_default_registry([CalculatorTool()])
    elif case == "fake_registry":
        registry = FakeRegistry()
    elif case == "subclass_registry":
        registry = RegistrySubclass(owner_policy=OwnerToolPolicy(), audit=ctx.audit)
    elif case == "fake_model":
        model = FakeModel()
    elif case == "subclass_model":
        model = ModelSubclass(
            runtime["inner"], runtime["binding"], runtime["budget"], runtime["audit"]
        )
    elif case == "fake_policy":
        registry._owner_policy = lambda *args: True
    elif case == "subclass_policy":
        registry._owner_policy = PolicySubclass()
    elif case == "task":
        ctx.task_id = "wrong"
    elif case == "audit":
        ctx.audit = object()
    elif case == "copied_grant":
        ctx.owner_grant = replace(ctx.owner_grant)
    elif case == "missing_grant":
        ctx.owner_grant = None
    elif case == "mixed_beta":
        ctx.beta_principal = BetaPrincipal(7, True)
    else:
        ctx.memory_store = object()
    with pytest.raises((PilotError, MeteredDenied)):
        await ToolDriver(registry, model).run("task", "system", ctx)
    assert not calls and not runtime["requests"]
    row = await runtime["store"].get(runtime["owner"], runtime["lease"].task_id)
    assert row.model_attempts == row.tool_attempts == 0


@pytest.mark.parametrize(
    "fault",
    [
        "intent_failure",
        "outcome_failure",
        "intent_ack",
        "outcome_ack",
        "timeout",
        "cancel",
        "tool_exception",
    ],
)
async def test_owner_tool_unknown_audit_and_cancellation_are_typed(runtime, monkeypatch, fault):
    registry = build_default_registry(
        [CalculatorTool()], owner_policy=OwnerToolPolicy(), audit=runtime["audit"]
    )
    if fault.startswith(("intent", "outcome")):
        method = "claim_intent" if fault.startswith("intent") else "append_beta"

        async def broken(self, entry):
            if fault.endswith("ack"):
                return 1
            raise OSError("PRIVATE_STORAGE_ERROR")

        monkeypatch.setattr(BetaAuditLog, method, broken)
    else:

        async def broken(*a, **kw):
            if fault == "timeout":
                await asyncio.Event().wait()
            elif fault == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("PRIVATE_TOOL_ERROR")

        monkeypatch.setattr(registry.get("calculator"), "run", broken)
        registry.set_timeout("calculator", 0.5)
    with pytest.raises(PilotError) as denied:
        await registry.call("calculator", {"expression": "2+3"}, runtime["ctx"])
    assert denied.value.code == (
        "audit" if fault.startswith(("intent", "outcome")) else "uncertainty"
    )
    assert "PRIVATE_" not in str(denied.value)
    if not fault.startswith("intent"):
        assert denied.value.uncertain
        assert await runtime["audit"].has_pending_calls()


async def test_owner_known_tool_failure_cannot_become_completed_final(runtime):
    from agency.tools.driver import ToolDriver

    registry = build_default_registry(
        [CalculatorTool()], owner_policy=OwnerToolPolicy(), audit=runtime["audit"]
    )
    runtime["replies"][:] = [
        '{"action":{"name":"calculator","args":{"expression":"1/0"}}}',
        '{"final":"fabricated success"}',
    ]
    with pytest.raises(PilotError):
        await ToolDriver(registry, runtime["model"]).run("task", "system", runtime["ctx"])
    assert len(runtime["requests"]) == 1


async def test_owner_forced_final_quota_is_not_fallback(runtime):
    from agency.llm.metered import MeteredDenied
    from agency.tools.driver import ToolDriver

    grant = replace(runtime["grant"], max_model_calls=1)
    runtime["ctx"].owner_grant = grant
    model = MeteredModel(
        runtime["inner"],
        replace(runtime["binding"], grant=grant),
        runtime["budget"],
        runtime["audit"],
    )
    registry = build_default_registry(
        [CalculatorTool()], owner_policy=OwnerToolPolicy(), audit=runtime["audit"]
    )
    runtime["replies"][:] = ['{"action":{"name":"calculator","args":{"expression":"2+3"}}}']
    with pytest.raises(MeteredDenied):
        await ToolDriver(registry, model, max_tool_iterations=1).run(
            "task", "system", runtime["ctx"]
        )
    assert len(runtime["requests"]) == 1


@pytest.mark.parametrize("factory", [False, True])
async def test_owner_defaults_schema_unknown_and_redacted_audit(runtime, monkeypatch, factory):
    from agency.tools.builtin.web import WebSearchTool

    monkeypatch.setattr(httpx, "AsyncClient", runtime["client"])
    requests = []

    def handle(request):
        requests.append(request)
        html = "".join(
            f'<a class="result__a" href="https://example.invalid/{i}">PRIVATE_OUTPUT_{i}</a>'
            for i in range(6)
        )
        return httpx.Response(200, text=html)

    search = WebSearchTool(transport=httpx.MockTransport(handle))
    policy = OwnerToolPolicy()
    if factory:
        registry = build_default_registry(
            [search, CalculatorTool()], owner_policy=policy, audit=runtime["audit"]
        )
    else:
        registry = ToolRegistry(owner_policy=policy, audit=runtime["audit"])
        registry.register(search)
        registry.register(CalculatorTool())
    ctx = runtime["ctx"]
    for name, args in [
        ("PRIVATE_UNKNOWN", {}),
        ("calculator", {"PRIVATE_ARGUMENT": "secret"}),
        ("web_search", {"query": "PRIVATE_QUERY", "max_results": 6}),
    ]:
        result = await registry.call(name, args, ctx)
        assert not result.ok
    assert not requests
    assert (await runtime["store"].get(runtime["owner"], ctx.task_id)).tool_attempts == 0
    result = await registry.call("web_search", {"query": "PRIVATE_QUERY"}, ctx)
    assert result.ok and len(result.output) == 5
    assert len(requests) == 1
    records = await runtime["audit"].query()
    wire = json.dumps([entry.model_dump(mode="json") for entry in records])
    for private in ["PRIVATE_", ctx.task_id, runtime["lease"].capability, "telegram", "user_id"]:
        assert private not in wire
    tool_entries = [entry for entry in records if entry.target == "web_search"]
    assert tool_entries and all(
        entry.agent == "owner-tools" and entry.task == "owner-pilot" for entry in tool_entries
    )


def test_owner_constructor_and_factory_reject_mixed_or_nonconcrete_policy():
    from agency.tools.base import BetaToolPolicy

    class PolicySubclass(OwnerToolPolicy):
        pass

    for factory in (ToolRegistry, build_default_registry):
        for invalid in (object(), PolicySubclass()):
            with pytest.raises(TypeError):
                factory(owner_policy=invalid)
        with pytest.raises(TypeError):
            factory(owner_policy=OwnerToolPolicy(), beta_policy=BetaToolPolicy())
