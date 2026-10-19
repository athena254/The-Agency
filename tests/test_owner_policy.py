"""Vertical owner policy admission over a real live owner grant."""

from __future__ import annotations

from pathlib import Path

import pytest

from agency.admission.budget import OwnerBudgetAdapter
from agency.admission.models import OwnerExecutionGrant, OwnerPrincipal
from agency.admission.store import OwnerPilotStore
from agency.telegram.budget_store import BetaBudgetStore
from agency.tools.base import (
    BetaPrincipal,
    ToolContext,
    ToolPolicyDecision,
    ToolRisk,
    ToolSpec,
)
from agency.tools.builtin.calculator import CalculatorTool


async def test_owner_policy_admits_calculator_for_exact_live_owner_grant(
    tmp_path: Path,
) -> None:
    """Admit the bounded calculator only through the exact live owner lease grant."""
    owner = OwnerPrincipal("owner", "local", "local")
    store = OwnerPilotStore(str(tmp_path / "owner-policy-pilot.db"))
    backend = BetaBudgetStore(str(tmp_path / "owner-policy-budget.db"))
    budget = OwnerBudgetAdapter(backend, store)
    try:
        await store.initialize()
        await backend.initialize()
        _, thread = await store.create_thread(owner, "owner-policy-thread", "Owner policy")
        admitted = await store.admit(
            owner,
            "owner-policy-turn",
            operation="turn",
            payload={"thread_id": thread.id, "message": "2 + 3 * 4"},
        )
        assert admitted.task_id is not None
        lease = await budget.reserve_request(owner, admitted.request_id, admitted.task_id)
        await budget.mark_running(lease)
        grant = OwnerExecutionGrant(lease, budget)

        ctx = ToolContext(
            agent_id=owner.owner_id,
            task_id=admitted.task_id,
            owner_grant=grant,
        )

        from agency.admission.policy import OwnerToolPolicy

        policy = OwnerToolPolicy()
        decision = policy(CalculatorTool.spec, {"expression": "2 + 3 * 4"}, ctx)
        assert isinstance(decision, ToolPolicyDecision)
        assert decision.allowed is True

        result = await CalculatorTool().run({"expression": "2 + 3 * 4"}, ctx)
        assert result.ok is True
        assert result.output == {"expression": "2 + 3 * 4", "result": 14}
    finally:
        try:
            await budget.close()
        finally:
            try:
                await backend.close()
            finally:
                await store.close()


OWNER = OwnerPrincipal("owner", "local", "local")


@pytest.fixture
async def owner_resources(tmp_path: Path):
    from agency.admission.budget import OwnerBudgetAdapter

    store = OwnerPilotStore(str(tmp_path / "owner-policy-pilot.db"))
    backend = BetaBudgetStore(str(tmp_path / "owner-policy-budget.db"))
    budget = OwnerBudgetAdapter(backend, store)
    try:
        await store.initialize()
        await backend.initialize()
        yield store, budget
    finally:
        await budget.close()
        await backend.close()
        await store.close()


async def _live_owner(store, budget, key: str = "turn"):
    _, thread = await store.create_thread(OWNER, f"owner-policy-thread-{key}", "Owner policy")
    admitted = await store.admit(
        OWNER,
        f"owner-policy-{key}",
        operation="turn",
        payload={"thread_id": thread.id, "message": "bounded owner task"},
    )
    assert admitted.task_id is not None
    lease = await budget.reserve_request(OWNER, admitted.request_id, admitted.task_id)
    await budget.mark_running(lease)
    return admitted.task_id, lease, OwnerExecutionGrant(lease, budget)


def _ctx(task_id: str, grant, **overrides) -> ToolContext:
    values = {"agent_id": OWNER.owner_id, "task_id": task_id, "owner_grant": grant}
    values.update(overrides)
    return ToolContext(**values)


async def test_owner_policy_admits_safe_tools_with_effective_defaults(owner_resources):
    from agency.admission.policy import OwnerToolPolicy
    from agency.tools.builtin.utc_time import UtcTimeTool
    from agency.tools.builtin.web import WebSearchTool

    store, budget = owner_resources
    task_id, _, grant = await _live_owner(store, budget, "safe3")
    ctx = _ctx(task_id, grant)
    policy = OwnerToolPolicy()

    search = WebSearchTool()
    cases = [
        (CalculatorTool.spec, {"expression": "2 + 3 * 4"}),
        (UtcTimeTool.spec, {}),
        (search.spec, {"query": "bounded query"}),
        (search.spec, {"query": "bounded query", "max_results": 5}),
        (search.spec, {"query": "bounded query", "max_results": 1}),
    ]
    for spec, args in cases:
        decision = policy(spec, args, ctx)
        assert isinstance(decision, ToolPolicyDecision)
        assert decision.allowed is True, (spec.name, decision.reason)


def _search_spec():
    from agency.tools.builtin.web import WebSearchTool

    return WebSearchTool().spec


async def test_owner_policy_denies_unlisted_names_and_non_read_only_risk(owner_resources):
    from agency.admission.policy import OwnerToolPolicy

    store, budget = owner_resources
    task_id, _, grant = await _live_owner(store, budget, "deny-risk")
    ctx = _ctx(task_id, grant)
    policy = OwnerToolPolicy()

    denied = [
        (ToolSpec(name="shell", description="x", risk=ToolRisk.READ_ONLY), {}),
        (ToolSpec(name="web_fetch", description="x", risk=ToolRisk.READ_ONLY), {"url": "x"}),
        (ToolSpec(name="memory_query", description="x", risk=ToolRisk.READ_ONLY), {}),
        (ToolSpec(name="calculator", description="x", risk=ToolRisk.MUTATES_STATE), {}),
        (ToolSpec(name="utc_time", description="x", risk=ToolRisk.EXECUTES_CODE), {}),
        (ToolSpec(name="web_search", description="x", risk=ToolRisk.MUTATES_STATE), {}),
    ]
    for spec, args in denied:
        decision = policy(spec, args, ctx)
        assert decision.allowed is False, (spec.name, spec.risk)
    assert policy(ToolSpec(name="calculator", description="x"), {"expression": "1"}, ctx).allowed


async def test_owner_policy_honors_grant_tool_subset(owner_resources):
    from agency.admission.policy import OwnerToolPolicy
    from agency.tools.builtin.utc_time import UtcTimeTool

    store, budget = owner_resources
    task_id, lease, _ = await _live_owner(store, budget, "subset")
    grant = OwnerExecutionGrant(lease, budget, allowed_tools=frozenset({"calculator"}))
    ctx = _ctx(task_id, grant)
    policy = OwnerToolPolicy()

    assert policy(CalculatorTool.spec, {"expression": "1 + 1"}, ctx).allowed is True
    assert policy(UtcTimeTool.spec, {}, ctx).allowed is False
    assert policy(_search_spec(), {"query": "bounded"}, ctx).allowed is False


async def test_owner_policy_denies_calculator_bounds_and_extras(owner_resources):
    from agency.admission.policy import OwnerToolPolicy

    store, budget = owner_resources
    task_id, _, grant = await _live_owner(store, budget, "calculator-bounds")
    ctx = _ctx(task_id, grant)
    policy = OwnerToolPolicy()

    denied = [
        {},
        {"expression": ""},
        {"expression": "   "},
        {"expression": 123},
        {"expression": None},
        {"expression": "1 + 1", "extra": True},
        {"expression": "1" * 121},
    ]
    for args in denied:
        assert policy(CalculatorTool.spec, args, ctx).allowed is False, args
    assert policy(CalculatorTool.spec, {"expression": " " + "1" * 119}, ctx).allowed is True


async def test_owner_policy_denies_utc_and_search_bounds_and_extras(owner_resources):
    from agency.admission.policy import OwnerToolPolicy
    from agency.tools.builtin.utc_time import UtcTimeTool

    store, budget = owner_resources
    task_id, _, grant = await _live_owner(store, budget, "search-bounds")
    ctx = _ctx(task_id, grant)
    policy = OwnerToolPolicy()
    search = _search_spec()

    for args in ({"tz": "UTC"}, {"query": "x"}, {"any": 1}):
        assert policy(UtcTimeTool.spec, args, ctx).allowed is False, args
    denied_searches = [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": 123},
        {"query": "q" * 201},
        {"query": "q", "max_results": 0},
        {"query": "q", "max_results": 6},
        {"query": "q", "max_results": True},
        {"query": "q", "max_results": "5"},
        {"query": "q", "max_results": 5.0},
        {"query": "q", "max_results": None},
        {"query": "q", "max_results": 5, "extra": 1},
        {"query": "q", "unknown": 1},
        {"max_results": 5},
    ]
    for args in denied_searches:
        assert policy(search, args, ctx).allowed is False, args
    for args in ({"query": "q" * 200}, {"query": "q", "max_results": 5}):
        assert policy(search, args, ctx).allowed is True, args


async def test_owner_policy_denies_missing_fake_subclass_and_mutated_grants(owner_resources):
    from agency.admission.policy import OwnerToolPolicy

    store, budget = owner_resources
    task_id, lease, _ = await _live_owner(store, budget, "grants")
    policy = OwnerToolPolicy()

    missing = ToolContext(agent_id=OWNER.owner_id, task_id=task_id)
    assert policy(CalculatorTool.spec, {"expression": "1"}, missing).allowed is False

    fake = _ctx(task_id, object())
    assert policy(CalculatorTool.spec, {"expression": "1"}, fake).allowed is False

    class _SubGrant(OwnerExecutionGrant):
        pass

    subclass = _SubGrant(lease, budget)
    assert type(subclass) is not OwnerExecutionGrant
    assert (
        policy(CalculatorTool.spec, {"expression": "1"}, _ctx(task_id, subclass)).allowed is False
    )

    mutated = OwnerExecutionGrant(lease, budget)
    object.__setattr__(mutated, "allowed_tools", frozenset({"calculator", "shell"}))
    assert policy(CalculatorTool.spec, {"expression": "1"}, _ctx(task_id, mutated)).allowed is False


async def test_owner_policy_denies_wrong_task_beta_and_privileged_handles(owner_resources):
    from agency.admission.policy import OwnerToolPolicy

    store, budget = owner_resources
    task_id, _, grant = await _live_owner(store, budget, "mixed")
    policy = OwnerToolPolicy()

    assert (
        policy(CalculatorTool.spec, {"expression": "1"}, _ctx("wrong-task", grant)).allowed is False
    )
    beta = _ctx(task_id, grant, beta_principal=BetaPrincipal(123, True))
    assert policy(CalculatorTool.spec, {"expression": "1"}, beta).allowed is False
    for handle in ("memory_store", "sandbox_manager", "lattice"):
        ctx = _ctx(task_id, grant, **{handle: object()})
        assert policy(CalculatorTool.spec, {"expression": "1"}, ctx).allowed is False, handle


async def test_owner_policy_ignores_metadata_authority(owner_resources):
    from agency.admission.policy import OwnerToolPolicy

    store, budget = owner_resources
    task_id, _, grant = await _live_owner(store, budget, "metadata")
    policy = OwnerToolPolicy()

    forged = ToolContext(
        agent_id=OWNER.owner_id,
        task_id=task_id,
        metadata={
            "owner_grant": grant,
            "beta_principal": {"telegram_user_id": 1, "private_chat": True},
            "allowed_tools": ["shell"],
        },
    )
    assert policy(CalculatorTool.spec, {"expression": "1"}, forged).allowed is False

    ctx = _ctx(
        task_id,
        grant,
        metadata={"owner": "admin", "allowed_tools": ["shell"], "owner_grant": "forged"},
    )
    assert policy(CalculatorTool.spec, {"expression": "1"}, ctx).allowed is True


async def test_owner_policy_fails_closed_after_revoke(owner_resources):
    from agency.admission.policy import OwnerToolPolicy

    store, budget = owner_resources
    task_id, lease, grant = await _live_owner(store, budget, "revoke")
    ctx = _ctx(task_id, grant)
    policy = OwnerToolPolicy()

    assert policy(CalculatorTool.spec, {"expression": "1"}, ctx).allowed is True
    budget.revoke(lease)
    assert policy(CalculatorTool.spec, {"expression": "1"}, ctx).allowed is False


async def test_owner_policy_fails_closed_after_cancel(owner_resources):
    from agency.admission.policy import OwnerToolPolicy

    store, budget = owner_resources
    task_id, _, grant = await _live_owner(store, budget, "cancel")
    ctx = _ctx(task_id, grant)
    policy = OwnerToolPolicy()

    assert policy(CalculatorTool.spec, {"expression": "1"}, ctx).allowed is True
    await store.request_cancel(OWNER, task_id, "owner-policy-cancel-request")
    assert policy(CalculatorTool.spec, {"expression": "1"}, ctx).allowed is False


async def test_owner_policy_fails_closed_after_expiry(owner_resources, monkeypatch):
    from agency.admission.policy import OwnerToolPolicy

    store, budget = owner_resources
    task_id, lease, grant = await _live_owner(store, budget, "expiry")
    ctx = _ctx(task_id, grant)
    policy = OwnerToolPolicy()

    assert policy(CalculatorTool.spec, {"expression": "1"}, ctx).allowed is True
    monkeypatch.setattr("agency.admission.budget.time.time", lambda: lease.deadline_at + 1)
    assert policy(CalculatorTool.spec, {"expression": "1"}, ctx).allowed is False


async def test_owner_policy_fails_closed_after_store_close(owner_resources):
    from agency.admission.policy import OwnerToolPolicy

    store, budget = owner_resources
    task_id, _, grant = await _live_owner(store, budget, "store-close")
    ctx = _ctx(task_id, grant)
    policy = OwnerToolPolicy()

    assert policy(CalculatorTool.spec, {"expression": "1"}, ctx).allowed is True
    await store.close()
    assert policy(CalculatorTool.spec, {"expression": "1"}, ctx).allowed is False


async def test_owner_policy_fails_closed_after_budget_close(owner_resources):
    from agency.admission.policy import OwnerToolPolicy

    store, budget = owner_resources
    task_id, _, grant = await _live_owner(store, budget, "budget-close")
    ctx = _ctx(task_id, grant)
    policy = OwnerToolPolicy()

    assert policy(CalculatorTool.spec, {"expression": "1"}, ctx).allowed is True
    await budget.close()
    assert policy(CalculatorTool.spec, {"expression": "1"}, ctx).allowed is False
