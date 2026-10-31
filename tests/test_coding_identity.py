"""Tests for coding agent identity registration."""

from __future__ import annotations

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.agents.coding_identity import register_coding_agent
from agency.kernel.identity import Capability, TrustLevel
from agency.kernel.policies import ActionClass
from agency.kernel.registry import AgentRegistry as KernelRegistry


def _allow(actor: str, perm: str) -> bool:
    return perm == "agent.approve" and actor in {"approver", "lead"}


def test_register_coding_agent_happy_path(tmp_path) -> None:
    kernels = KernelRegistry()
    runtimes = RuntimeRegistry()
    allowed_capabilities = {"code-edit", "verify", "inspect"}

    agent = register_coding_agent(kernels, runtimes, agent_id="coding-agent", name="Coding Agent")

    assert agent.id == "coding-agent"
    assert agent.name == "Coding Agent"
    assert {cap.name for cap in agent.capabilities} == allowed_capabilities
    for cap in agent.capabilities:
        assert cap.max_level is ActionClass.L0_OBSERVATION
    assert kernels.get("coding-agent") is not None
    assert runtimes.get_agent("coding-agent") is not None
    runtime_record = runtimes.get_agent("coding-agent")
    assert runtime_record is not None
    assert runtime_record.agent.has_capability("code-edit")
    assert runtime_record.agent.has_capability("verify")
    assert runtime_record.agent.has_capability("inspect")
    # authorizer sanity (fixture parity)
    assert _allow("approver", "agent.approve") is True