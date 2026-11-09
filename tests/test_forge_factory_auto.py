"""Integration: factory auto-activation + forge tool build/register."""

from __future__ import annotations

import pytest

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.autonomy.factory_auto import AUTO_VERSION, activate_for_caps, blueprint_id_for_caps
from agency.autonomy.forge_auto import build_tool
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.tools.base import ToolContext
from agency.tools.registry import ToolRegistry

MINI_ADD_CODE = """\
def _add(a, b):
    return a + b


async def run(args, ctx):
    return {"result": _add(int(args["a"]), int(args["b"]))}
"""

MINI_ADD_TESTS = """\
import asyncio

import tool


def test_add():
    assert asyncio.run(tool.run({"a": 2, "b": 3}, None)) == {"result": 5}
"""

UNSAFE_CODE = """\
def run(args, ctx):
    return eval(args["x"])
"""

UNSAFE_TESTS = """\
import tool


def test_unused():
    assert True
"""


def _factory(authorizer=None) -> AgentFactory:
    return AgentFactory(
        ":memory:",
        kernel_registry=KernelRegistry(),
        runtime_registry=RuntimeRegistry(),
        authorizer=authorizer,
        allowed_capabilities={"inspect", "search", "general"},
    )


def test_factory_auto_spawns_and_is_idempotent():
    factory = _factory()
    caps = ["search", "inspect"]
    blueprint_id = blueprint_id_for_caps(caps)
    first = activate_for_caps(caps, factory)
    second = activate_for_caps(list(reversed(caps)), factory)
    assert first == second
    bp = factory.get(blueprint_id, AUTO_VERSION)
    assert bp.status is BlueprintStatus.ACTIVE
    assert bp.runtime_agent_id == first
    assert tuple(sorted(bp.capabilities)) == ("inspect", "search")
    assert len(factory.list_versions(blueprint_id)) == 1
    transitions = [
        (e["from_status"], e["to_status"]) for e in factory.list_events(blueprint_id, AUTO_VERSION)
    ]
    assert ("", "DRAFT") in transitions
    assert ("DRAFT", "APPROVED") in transitions
    assert ("APPROVED", "ACTIVE") in transitions
    factory.close()


def test_factory_auto_is_deterministic_across_instances():
    caps = ["inspect", "search"]
    blueprint_id = blueprint_id_for_caps(caps)
    assert blueprint_id_for_caps(caps) == blueprint_id_for_caps(list(reversed(caps)))
    f1, f2 = _factory(), _factory()
    activate_for_caps(caps, f1)
    activate_for_caps(caps, f2)
    assert f1.get(blueprint_id, AUTO_VERSION).blueprint_id == blueprint_id
    assert f2.get(blueprint_id, AUTO_VERSION).blueprint_id == blueprint_id
    f1.close()
    f2.close()


def test_factory_auto_fails_closed_on_revoked():
    factory = _factory()
    caps = ["inspect"]
    activate_for_caps(caps, factory)
    factory.revoke(blueprint_id_for_caps(caps), AUTO_VERSION, evidence="operator revoke")
    with pytest.raises(ValueError, match="revoked"):
        activate_for_caps(caps, factory)
    factory.close()


def test_factory_human_approval_path_untouched():
    factory = _factory(authorizer=lambda actor, perm: actor == "approver")
    factory.create("human-bp", "1.0.0", "Human", creator="author", capabilities=("inspect",))
    factory.approve("human-bp", "1.0.0", approver="approver", evidence="human review")
    agent = factory.activate("human-bp", "1.0.0")
    assert agent.id
    factory.close()


def test_forge_auto_builds_and_registers_calculator_tool():
    registry = ToolRegistry()
    result = build_tool("mini_add", MINI_ADD_CODE, MINI_ADD_TESTS, registry)
    assert result.gate == "PASS"
    assert result.registered
    assert result.sha256
    assert [s.name for s in registry.list_specs()] == ["mini_add"]


async def test_forge_auto_registered_tool_runs():
    registry = ToolRegistry()
    build_tool("mini_add", MINI_ADD_CODE, MINI_ADD_TESTS, registry)
    res = await registry.call("mini_add", {"a": 2, "b": 3}, ToolContext(agent_id="t", task_id="t"))
    assert res.ok
    assert res.output == {"result": 5}


def test_forge_auto_rejects_unsafe_code():
    registry = ToolRegistry()
    result = build_tool("mini_evil", UNSAFE_CODE, UNSAFE_TESTS, registry)
    assert result.gate == "FAIL"
    assert not result.registered
    assert registry.list_specs() == []
