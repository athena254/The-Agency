"""Owner metering contracts exercised with concrete stores and HTTP leaves."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
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
from agency.admission.store import OwnerPilotStore
from agency.kernel.audit import BetaAuditLog
from agency.llm.adapter import LLMAdapter
from agency.llm.metered import MeteredDenied, MeteredError, MeteredModel
from agency.telegram.budget_store import BetaBudgetStore, BudgetLimits


@pytest.fixture
async def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    owner = OwnerPrincipal("synthetic-owner-private", "local", "synthetic-subject-private")
    store = OwnerPilotStore(str(tmp_path / "pilot.db"))
    backend = BetaBudgetStore(str(tmp_path / "quota.db"))
    budget = OwnerBudgetAdapter(backend, store)
    audit = BetaAuditLog(str(tmp_path / "audit.db"))
    requests: list[httpx.Request] = []
    client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})

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
        yield locals()
    finally:
        await audit.close()
        await budget.close()
        await store.close()


@pytest.mark.parametrize("timeout_s", [45.0001, 60.0])
async def test_owner_timeout_cannot_exceed_45_seconds(runtime, timeout_s: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        MeteredModel(
            runtime["inner"],
            runtime["binding"],
            runtime["budget"],
            runtime["audit"],
            timeout_s=timeout_s,
        )
    assert runtime["requests"] == []


@pytest.mark.parametrize("timeout_s", [1.0, 45.0])
async def test_owner_timeout_at_or_below_ceiling_can_dispatch(runtime, timeout_s: float) -> None:
    model = MeteredModel(
        runtime["inner"],
        runtime["binding"],
        runtime["budget"],
        runtime["audit"],
        timeout_s=timeout_s,
    )
    assert await model.generate("prompt", {"owner_grant": runtime["grant"]}) == "answer"
    assert len(runtime["requests"]) == 1
    assert not await runtime["audit"].has_pending_calls()


@pytest.mark.parametrize("timeout_s", [45.0001, 60.0])
async def test_legacy_beta_timeout_above_owner_ceiling_can_dispatch(
    runtime, timeout_s: float
) -> None:
    from agency.llm.metered import MeteredBinding
    from agency.tools.base import BetaPrincipal

    backend = runtime["backend"]
    reservation = await backend.reserve_request("synthetic-beta", 1, 7)
    assert reservation.capability is not None
    await backend.mark_running("synthetic-beta", 1, 7, capability=reservation.capability)
    principal = BetaPrincipal(7, True)
    binding = MeteredBinding(
        "synthetic-beta", 1, 7, reservation.capability, principal, "openai", "synthetic-model"
    )
    model = MeteredModel(runtime["inner"], binding, backend, runtime["audit"], timeout_s=timeout_s)
    assert await model.generate("prompt", {"beta_principal": principal}) == "answer"
    assert len(runtime["requests"]) == 1
    assert not await runtime["audit"].has_pending_calls()


async def test_legacy_beta_leaf_denial_keeps_original_unknown_error_contract(
    runtime, monkeypatch
) -> None:
    from agency.llm.metered import MeteredBinding
    from agency.tools.base import BetaPrincipal

    backend = runtime["backend"]
    reservation = await backend.reserve_request("synthetic-beta", 1, 7)
    assert reservation.capability is not None
    await backend.mark_running("synthetic-beta", 1, 7, capability=reservation.capability)
    principal = BetaPrincipal(7, True)
    binding = MeteredBinding(
        "synthetic-beta", 1, 7, reservation.capability, principal, "openai", "synthetic-model"
    )
    model = MeteredModel(runtime["inner"], binding, backend, runtime["audit"])

    def deny(request):
        raise MeteredDenied("synthetic leaf denial")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: runtime["client"](transport=httpx.MockTransport(deny), **kw),
    )
    with pytest.raises(MeteredError):
        await model.generate("prompt", {"beta_principal": principal})
    assert await runtime["audit"].has_pending_calls()


async def test_replaced_binding_after_dispatch_is_typed_unknown(runtime, monkeypatch) -> None:
    original = runtime["inner"].generate_bound

    async def replace_binding(*args, **kwargs):
        result = await original(*args, **kwargs)
        runtime["model"]._binding = object()
        return result

    monkeypatch.setattr(runtime["inner"], "generate_bound", replace_binding)
    with pytest.raises(MeteredError):
        await runtime["model"].generate("prompt", {"owner_grant": runtime["grant"]})
    assert len(runtime["requests"]) == 1
    assert await runtime["audit"].has_pending_calls()


@pytest.mark.parametrize(
    "case",
    [
        "mixed_owner",
        "mixed_beta",
        "foreign_budget",
        "binding_subclass",
        "adapter_subclass",
        "audit_fake",
        "budget_subclass",
    ],
)
async def test_direct_constructor_cannot_bypass_exact_pairing(runtime, case) -> None:
    from agency.llm.metered import MeteredBinding
    from agency.tools.base import BetaPrincipal

    class BindingSubclass(OwnerMeteredBinding):
        pass

    class AdapterSubclass(LLMAdapter):
        pass

    class BudgetSubclass(OwnerBudgetAdapter):
        pass

    inner, binding, budget, audit = (runtime[k] for k in ("inner", "binding", "budget", "audit"))
    if case == "mixed_owner":
        budget = runtime["backend"]
    elif case == "mixed_beta":
        binding = MeteredBinding(
            "synthetic-bot",
            1,
            7,
            "synthetic-capability",
            BetaPrincipal(7, True),
            "openai",
            "synthetic-model",
        )
    elif case == "foreign_budget":
        budget = OwnerBudgetAdapter(runtime["backend"], runtime["store"])
    elif case == "binding_subclass":
        binding = BindingSubclass(runtime["grant"], "openai", "synthetic-model", "synthetic-config")
    elif case == "adapter_subclass":
        inner = AdapterSubclass(
            provider="openai",
            model="synthetic-model",
            api_key="SYNTHETIC_PRIVATE_KEY",
            base_url="https://provider.invalid/v1",
            timeout=10.0,
        )
    elif case == "budget_subclass":
        budget = BudgetSubclass(runtime["backend"], runtime["store"])
    else:
        audit = object()
    with pytest.raises(TypeError):
        MeteredModel(inner, binding, budget, audit)
    assert runtime["requests"] == []


@pytest.mark.parametrize(
    "prompt",
    ["x" * 65537, "🙂" * 20000, chr(0xD800)],
    ids=["ascii_oversize", "utf8_oversize", "invalid_utf8"],
)
async def test_invalid_owner_prompt_denied_without_durable_attempt(runtime, prompt) -> None:
    with pytest.raises(MeteredDenied):
        await runtime["model"].generate(prompt, {"owner_grant": runtime["grant"]})
    assert runtime["requests"] == []
    assert (
        await runtime["store"].get(runtime["owner"], runtime["lease"].task_id)
    ).model_attempts == 0
    assert not await runtime["audit"].has_pending_calls()


@pytest.mark.parametrize("kind", ["model", "tool"])
async def test_nonboolean_durable_attempt_ack_never_authorizes(runtime, monkeypatch, kind) -> None:
    async def invalid_ack(*args, **kwargs):
        return 1

    monkeypatch.setattr(runtime["store"], "reserve_attempt", invalid_ack)
    with pytest.raises(PilotError):
        if kind == "model":
            await runtime["budget"].reserve_model_call(runtime["lease"])
        else:
            await runtime["budget"].reserve_tool_call(runtime["lease"], "calculator")


@pytest.mark.parametrize("boundary", ["response", "outcome"])
async def test_revocation_after_dispatch_cannot_return_success(
    runtime, monkeypatch, boundary
) -> None:
    budget, grant = runtime["budget"], runtime["grant"]
    if boundary == "response":
        original = runtime["inner"].generate_bound

        async def revoke(*args, **kwargs):
            result = await original(*args, **kwargs)
            budget.revoke(grant.lease)
            return result

        monkeypatch.setattr(runtime["inner"], "generate_bound", revoke)
    else:
        original = runtime["audit"].append_beta

        async def revoke(entry):
            result = await original(entry)
            budget.revoke(grant.lease)
            return result

        monkeypatch.setattr(runtime["audit"], "append_beta", revoke)
    with pytest.raises(MeteredError):
        await runtime["model"].generate("prompt", {"owner_grant": grant})
    assert len(runtime["requests"]) == 1
    assert await runtime["audit"].has_pending_calls() is (boundary == "response")


@pytest.mark.parametrize(
    "change", ["task", "base_url", "max_tokens", "grant_limit", "binding_model"]
)
async def test_owner_contract_drift_denied_before_reservation(runtime, change) -> None:
    context = {"owner_grant": runtime["grant"], "task_id": runtime["lease"].task_id}
    if change == "task":
        context["task_id"] = "foreign-task"
    elif change in ("base_url", "max_tokens"):
        context[change] = "caller-override"
    elif change == "grant_limit":
        object.__setattr__(runtime["grant"], "max_model_calls", 1)
    else:
        object.__setattr__(runtime["binding"], "model", "other-model")
    with pytest.raises(MeteredDenied):
        await runtime["model"].generate("prompt", context)
    assert runtime["requests"] == []
    assert (
        await runtime["store"].get(runtime["owner"], runtime["lease"].task_id)
    ).model_attempts == 0


@pytest.mark.parametrize("boundary", ["reserve", "intent"])
@pytest.mark.parametrize("fault", ["integer_ack", "cancel"])
async def test_direct_pre_dispatch_failures_are_typed_blockers(
    runtime, monkeypatch, boundary, fault
) -> None:
    async def fail(*args, **kwargs):
        if fault == "cancel":
            raise asyncio.CancelledError()
        return 1

    target = runtime["budget"] if boundary == "reserve" else runtime["audit"]
    monkeypatch.setattr(
        target, "reserve_model_call" if boundary == "reserve" else "claim_intent", fail
    )
    with pytest.raises(MeteredDenied):
        await runtime["model"].generate("prompt", {"owner_grant": runtime["grant"]})
    assert runtime["requests"] == []


@pytest.mark.parametrize("fault", ["raise", "false_ack", "wrong_ack", "cancel"])
async def test_direct_outcome_failures_are_typed_and_remain_pending(
    runtime, monkeypatch, fault
) -> None:
    async def fail(entry):
        if fault == "raise":
            raise OSError("synthetic-private-storage-error")
        if fault == "cancel":
            raise asyncio.CancelledError()
        return False if fault == "false_ack" else "wrong-id"

    monkeypatch.setattr(runtime["audit"], "append_beta", fail)
    with pytest.raises(MeteredError, match="^metered: audit unavailable$"):
        await runtime["model"].generate("prompt", {"owner_grant": runtime["grant"]})
    assert len(runtime["requests"]) == 1
    assert await runtime["audit"].has_pending_calls()
    with pytest.raises(MeteredDenied):
        await runtime["model"].generate("prompt", {"owner_grant": runtime["grant"]})
    assert len(runtime["requests"]) == 1


@pytest.mark.parametrize("boundary", ["reserve", "intent"])
@pytest.mark.parametrize("change", ["revoke", "cancel", "grant", "context", "audit", "inner"])
async def test_owner_liveness_and_identity_rechecked_after_await(
    runtime, monkeypatch, boundary, change
) -> None:
    budget, audit, model, grant = (runtime[k] for k in ("budget", "audit", "model", "grant"))
    context = {"owner_grant": grant}
    original = budget.reserve_model_call if boundary == "reserve" else audit.claim_intent

    async def tamper(*args, **kwargs):
        result = await original(*args, **kwargs)
        if change == "revoke":
            budget.revoke(grant.lease)
        elif change == "cancel":
            await runtime["store"].request_cancel(runtime["owner"], grant.lease.task_id, "cancel")
        elif change == "grant":
            object.__setattr__(runtime["binding"], "grant", replace(grant))
        elif change == "context":
            context["owner_grant"] = replace(grant)
        elif change == "audit":
            model._audit = object()
        elif change == "inner":
            model._inner = LLMAdapter(provider="echo", model="echo", timeout=10.0)
        return result

    monkeypatch.setattr(
        budget if boundary == "reserve" else audit,
        "reserve_model_call" if boundary == "reserve" else "claim_intent",
        tamper,
    )
    with pytest.raises(MeteredDenied):
        await model.generate("prompt", context)
    assert runtime["requests"] == []
    assert await audit.has_pending_calls() is (boundary == "intent")


async def test_wrapper_enforces_grant_lower_model_cap(runtime) -> None:
    grant = replace(runtime["grant"], max_model_calls=1)
    binding = replace(runtime["binding"], grant=grant)
    model = MeteredModel(runtime["inner"], binding, runtime["budget"], runtime["audit"])
    assert await model.generate("prompt", {"owner_grant": grant}) == "answer"
    with pytest.raises(MeteredDenied):
        await model.generate("prompt", {"owner_grant": grant})
    assert len(runtime["requests"]) == 1
    assert (await runtime["store"].get(runtime["owner"], grant.lease.task_id)).model_attempts == 1


@pytest.mark.parametrize("kind", ["model", "tool"])
async def test_lower_caps_are_atomic_under_concurrent_reservations(runtime, kind: str) -> None:
    budget, lease = runtime["budget"], runtime["lease"]

    async def reserve():
        if kind == "model":
            return await budget.reserve_model_call(lease, limit=2)
        return await budget.reserve_tool_call(lease, "calculator", limit=2)

    assert (await asyncio.gather(*(reserve() for _ in range(12)))).count(True) == 2
    row = await runtime["store"].get(runtime["owner"], lease.task_id)
    assert getattr(row, kind + "_attempts") == 2


async def test_budget_initialization_exposes_actual_immutable_limits(tmp_path: Path) -> None:
    actual = BudgetLimits(model_calls_per_request=2, requests_per_hour=1)
    backend = BetaBudgetStore(str(tmp_path / "quota.db"), limits=actual)
    store = OwnerPilotStore(str(tmp_path / "pilot.db"))
    budget = OwnerBudgetAdapter(backend, store)
    try:
        await budget.initialize()
        assert budget.limits == actual
        with pytest.raises(AttributeError):
            budget.limits = BudgetLimits()
        with pytest.raises(AttributeError):
            budget.limits.model_calls_per_request = 8
        assert not store._ready
        backend._limits = object()
        with pytest.raises(PilotError):
            _ = budget.limits
    finally:
        await budget.close()
        await store.close()


@pytest.mark.parametrize("boundary", ["before", "reserve", "intent"])
async def test_actual_http_snapshot_cannot_redirect_during_await(
    runtime, monkeypatch, boundary
) -> None:
    inner, model, grant = (runtime[k] for k in ("inner", "model", "grant"))

    def mutate():
        inner._provider = "echo"
        inner._model = "other-model"
        inner._base_url = "https://other.invalid/changed"
        inner._api_key = "OTHER_PRIVATE_KEY"
        inner._max_tokens = 1
        inner._temperature = 2.0
        inner._timeout = 1.0
        monkeypatch.setenv("AGENCY_LLM_API_KEY", "ENV_PRIVATE_KEY")
        monkeypatch.setenv("AGENCY_LLM_BASE_URL", "https://env.invalid/v1")

    if boundary == "before":
        mutate()
    else:
        target = runtime["budget"] if boundary == "reserve" else runtime["audit"]
        name = "reserve_model_call" if boundary == "reserve" else "claim_intent"
        original = getattr(target, name)

        async def change(*args, **kwargs):
            result = await original(*args, **kwargs)
            mutate()
            return result

        monkeypatch.setattr(target, name, change)
    assert (
        await model.generate(
            "private prompt", {"owner_grant": grant, "task_id": grant.lease.task_id}
        )
        == "answer"
    )
    assert len(runtime["requests"]) == 1
    request = runtime["requests"][0]
    assert str(request.url) == "https://provider.invalid/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer SYNTHETIC_PRIVATE_KEY"
    assert json.loads(request.content) == {
        "model": "synthetic-model",
        "messages": [{"role": "user", "content": "private prompt"}],
        "max_tokens": 1024,
        "temperature": 0.7,
    }
    assert set(request.extensions["timeout"].values()) == {10.0}
    diagnostic = repr(model) + repr(await runtime["audit"].query())
    for private in (
        "PRIVATE_KEY",
        "private prompt",
        "provider.invalid",
        "other.invalid",
        grant.lease.capability,
        grant.lease.task_id,
        runtime["owner"].owner_id,
        runtime["owner"].subject_id,
    ):
        assert private not in diagnostic


@pytest.mark.parametrize("failure", ["http", "malformed", "empty", "timeout", "cancel"])
async def test_unknown_http_outcomes_block_after_audit_reopen(
    runtime, monkeypatch, failure
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    requests = runtime["requests"]

    async def handle(request):
        requests.append(request)
        started.set()
        if failure in ("cancel", "timeout"):
            await release.wait()
        if failure == "http":
            return httpx.Response(503, text="PRIVATE_PROVIDER_BODY")
        if failure == "malformed":
            return httpx.Response(200, json={"choices": []})
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: runtime["client"](transport=httpx.MockTransport(handle), **kw),
    )
    model = runtime["model"]
    if failure == "timeout":
        model = MeteredModel(
            runtime["inner"],
            runtime["binding"],
            runtime["budget"],
            runtime["audit"],
            timeout_s=0.05,
        )
    context = {"owner_grant": runtime["grant"]}
    task = asyncio.create_task(model.generate("private prompt", context))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        if failure == "cancel":
            task.cancel()
        with pytest.raises(MeteredError):
            await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(requests) == 1
    assert await runtime["audit"].has_pending_calls()
    await runtime["audit"].close()
    await runtime["audit"].initialize()
    assert await runtime["audit"].has_pending_calls()
    with pytest.raises(MeteredDenied):
        await model.generate("private prompt", context)
    assert len(requests) == 1
    assert "PRIVATE_PROVIDER_BODY" not in repr(await runtime["audit"].query())


async def test_pending_intent_blocks_concurrent_second_http_dispatch(runtime, monkeypatch) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def handle(request):
        runtime["requests"].append(request)
        started.set()
        await release.wait()
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: runtime["client"](transport=httpx.MockTransport(handle), **kw),
    )
    first = asyncio.create_task(
        runtime["model"].generate("prompt", {"owner_grant": runtime["grant"]})
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        with pytest.raises(MeteredDenied):
            await runtime["model"].generate("prompt", {"owner_grant": runtime["grant"]})
        assert len(runtime["requests"]) == 1
        release.set()
        assert await first == "answer"
    finally:
        release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.parametrize("kind", ["model", "tool"])
@pytest.mark.parametrize("limit", [True, False, 0, -1, 9, 4.5, "1", None])
async def test_invalid_lower_caps_never_count_or_authorize(runtime, kind, limit) -> None:
    with pytest.raises(PilotError):
        if kind == "model":
            await runtime["budget"].reserve_model_call(runtime["lease"], limit=limit)
        else:
            await runtime["budget"].reserve_tool_call(runtime["lease"], "calculator", limit=limit)
    row = await runtime["store"].get(runtime["owner"], runtime["lease"].task_id)
    assert row.model_attempts == row.tool_attempts == 0


@pytest.mark.parametrize("context_kind", ["missing", "copy", "beta", "override"])
async def test_wrong_or_mixed_owner_context_denied_without_attempt(runtime, context_kind) -> None:
    grant = runtime["grant"]
    context = {"owner_grant": grant}
    if context_kind == "missing":
        context = None
    elif context_kind == "copy":
        context["owner_grant"] = replace(grant)
    elif context_kind == "beta":
        from agency.tools.base import BetaPrincipal

        context["beta_principal"] = BetaPrincipal(7, True)
    else:
        context["provider"] = "echo"
    with pytest.raises(MeteredDenied):
        await runtime["model"].generate("prompt", context)
    assert runtime["requests"] == []
    assert (await runtime["store"].get(runtime["owner"], grant.lease.task_id)).model_attempts == 0


@pytest.mark.parametrize("scope", ["revoked", "cancelled", "expired", "closed", "foreign_lease"])
async def test_stale_owner_grant_denied_before_any_model_attempt(
    runtime, monkeypatch, scope
) -> None:
    budget, grant = runtime["budget"], runtime["grant"]
    if scope == "revoked":
        budget.revoke(grant.lease)
    elif scope == "cancelled":
        await runtime["store"].request_cancel(runtime["owner"], grant.lease.task_id, "cancel")
    elif scope == "expired":
        monkeypatch.setattr(
            "agency.admission.budget.time.time", lambda: grant.lease.deadline_at + 1
        )
    elif scope == "closed":
        await budget.close()
    else:
        object.__setattr__(grant, "lease", replace(grant.lease))
    with pytest.raises(MeteredDenied):
        await runtime["model"].generate("prompt", {"owner_grant": grant})
    assert runtime["requests"] == []
    assert (
        await runtime["store"].get(runtime["owner"], runtime["admitted"].task_id)
    ).model_attempts == 0
