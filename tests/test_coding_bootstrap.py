"""Tests for coding agent bootstrap helper."""

from __future__ import annotations

from agency.agents.coding_bootstrap import bootstrap_coding_agent
from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.kernel.registry import AgentRegistry as KernelRegistry


def test_bootstrap_coding_agent_returns_runtime_record() -> None:
    kernels = KernelRegistry()
    runtimes = RuntimeRegistry()

    record = bootstrap_coding_agent(kernels, runtimes)

    assert record is not None
    assert record.agent.id == "coding-agent"
    assert record.agent.name == "Coding Agent"
    assert kernels.get("coding-agent") is not None
    assert runtimes.get_agent("coding-agent") is record
    assert runtimes.get_agent("coding-agent") is not None
    assert record.agent.has_capability("code-edit")
    assert record.agent.has_capability("verify")
    assert record.agent.has_capability("inspect")
