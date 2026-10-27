"""Tests for deterministic prompt composition and activation metadata."""

from __future__ import annotations

import pytest

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.factory.compose import compose_system_prompt
from agency.factory.prompts import PromptKind, PromptRegistry, PromptSpec, PromptStatus
from agency.factory.service import AgentFactory
from agency.kernel.registry import AgentRegistry as KernelRegistry


def _allow(actor: str, perm: str) -> bool:
    return perm == "agent.approve" and actor in {"approver", "lead"}


def _published(
    reg: PromptRegistry,
    prompt_id: str,
    body: str,
    *,
    version: str = "1.0.0",
    kind: PromptKind = PromptKind.SEGMENT,
) -> None:
    reg.register(
        PromptSpec(
            prompt_id=prompt_id,
            version=version,
            name=prompt_id,
            description="demo",
            domain="demo",
            kind=kind,
            body=body,
            provenance="team",
        )
    )
    reg.transition(prompt_id, version, PromptStatus.TESTING)
    reg.transition(prompt_id, version, PromptStatus.APPROVED, "tests pass")
    reg.publish(prompt_id, version, "review ok")


def _registry(tmp_path) -> PromptRegistry:
    return PromptRegistry(str(tmp_path / "prompts.db"))


def _factory(tmp_path, reg: PromptRegistry, **over):
    kernels = KernelRegistry()
    runtimes = RuntimeRegistry()
    params = {
        "authorizer": _allow,
        "allowed_capabilities": {"inspect"},
        "kernel_registry": kernels,
        "runtime_registry": runtimes,
        "is_prompt_published": reg.is_published,
        "prompt_resolver": reg,
    }
    params.update(over)
    fac = AgentFactory(str(tmp_path / "factory.db"), **params)
    return fac, kernels, runtimes


def test_composition_order_and_override(tmp_path) -> None:
    reg = _registry(tmp_path)
    _published(reg, "a", "Alpha")
    _published(reg, "b", "Beta")
    _published(reg, "c", "Gamma", kind=PromptKind.PACK)
    composed = compose_system_prompt(
        [("a", "1.0.0"), ("b", "1.0.0"), ("c", "1.0.0")],
        resolver=reg,
        override="Per-agent note.",
    )
    assert composed.text == "Alpha\n\nBeta\n\nGamma\n\nPer-agent note."
    assert composed.prompt_refs == (("a", "1.0.0"), ("b", "1.0.0"), ("c", "1.0.0"))
    reg.close()


def test_slot_substitution(tmp_path) -> None:
    reg = _registry(tmp_path)
    _published(reg, "a", "Target: {{TARGET}} in {{SCOPE}}.")
    composed = compose_system_prompt(
        [("a", "1.0.0")],
        resolver=reg,
        params={"TARGET": "notes.exe", "SCOPE": "lab"},
    )
    assert composed.text == "Target: notes.exe in lab."
    reg.close()


def test_unfilled_slot_and_unused_param_fail_closed(tmp_path) -> None:
    reg = _registry(tmp_path)
    _published(reg, "a", "Target: {{TARGET}}.")
    with pytest.raises(ValueError, match="unfilled prompt slot"):
        compose_system_prompt([("a", "1.0.0")], resolver=reg)
    _published(reg, "b", "No slots here.")
    with pytest.raises(ValueError, match="unused prompt params"):
        compose_system_prompt([("b", "1.0.0")], resolver=reg, params={"EXTRA": "x"})
    reg.close()


def test_angle_bracket_placeholders_pass_through(tmp_path) -> None:
    reg = _registry(tmp_path)
    _published(reg, "a", "Use <FEATURE> in <PATH TO APPLICATION>.")
    composed = compose_system_prompt([("a", "1.0.0")], resolver=reg)
    assert composed.text == "Use <FEATURE> in <PATH TO APPLICATION>."
    reg.close()


def test_duplicate_bodies_collapse(tmp_path) -> None:
    reg = _registry(tmp_path)
    _published(reg, "a", "Shared.")
    _published(reg, "b", "Shared.")
    composed = compose_system_prompt([("a", "1.0.0"), ("b", "1.0.0")], resolver=reg)
    assert composed.text == "Shared."
    reg.close()


def test_composition_is_deterministic(tmp_path) -> None:
    reg = _registry(tmp_path)
    _published(reg, "a", "Target: {{TARGET}}.")
    first = compose_system_prompt([("a", "1.0.0")], resolver=reg, params={"TARGET": "x"})
    second = compose_system_prompt([("a", "1.0.0")], resolver=reg, params={"TARGET": "x"})
    other = compose_system_prompt([("a", "1.0.0")], resolver=reg, params={"TARGET": "y"})
    assert first.text == second.text
    assert first.sha256 == second.sha256
    assert first.sha256 != other.sha256
    reg.close()


def test_empty_refs_rejected(tmp_path) -> None:
    reg = _registry(tmp_path)
    with pytest.raises(ValueError, match="must not be empty"):
        compose_system_prompt([], resolver=reg)
    reg.close()


def test_activation_attaches_composed_prompt_metadata(tmp_path) -> None:
    reg = _registry(tmp_path)
    _published(reg, "agency-base-identity", "Base identity.")
    _published(reg, "agency-l0-scope", "Observe only.")
    fac, kernels, runtimes = _factory(tmp_path, reg)
    try:
        fac.create(
            blueprint_id="analyst",
            version="1.0.0",
            name="Analyst",
            capabilities=["inspect"],
            prompt_refs=[
                ("agency-base-identity", "1.0.0"),
                ("agency-l0-scope", "1.0.0"),
            ],
            prompt_override="Per-agent note.",
            creator="creator",
        )
        fac.approve("analyst", "1.0.0", approver="approver", evidence="e1")
        agent = fac.activate("analyst", "1.0.0")
        expected = compose_system_prompt(
            [("agency-base-identity", "1.0.0"), ("agency-l0-scope", "1.0.0")],
            resolver=reg,
            override="Per-agent note.",
        )
        assert agent.metadata["system_prompt"] == expected.text
        assert agent.metadata["prompt_composed_sha256"] == expected.sha256
        assert agent.metadata["prompt_refs"] == [
            ["agency-base-identity", "1.0.0"],
            ["agency-l0-scope", "1.0.0"],
        ]
        assert kernels.get_required(agent.id) is not None
        assert runtimes.get_agent(agent.id).agent.metadata["system_prompt"] == expected.text
    finally:
        fac.close()
        reg.close()


def test_activation_without_resolver_fails_closed(tmp_path) -> None:
    reg = _registry(tmp_path)
    _published(reg, "a", "Body.")
    fac, kernels, _ = _factory(tmp_path, reg, prompt_resolver=None)
    try:
        fac.create(
            blueprint_id="analyst",
            version="1.0.0",
            name="Analyst",
            capabilities=["inspect"],
            prompt_refs=[("a", "1.0.0")],
            creator="creator",
        )
        fac.approve("analyst", "1.0.0", approver="approver", evidence="e1")
        with pytest.raises(ValueError, match="no prompt resolver"):
            fac.activate("analyst", "1.0.0")
        assert kernels.count() == 0
        assert fac.get("analyst", "1.0.0").runtime_agent_id is None
    finally:
        fac.close()
        reg.close()


def test_activation_without_prompt_refs_omits_prompt_metadata(tmp_path) -> None:
    reg = _registry(tmp_path)
    fac, _, _ = _factory(tmp_path, reg)
    try:
        fac.create(
            blueprint_id="plain",
            version="1.0.0",
            name="Plain",
            capabilities=["inspect"],
            creator="creator",
        )
        fac.approve("plain", "1.0.0", approver="approver", evidence="e1")
        agent = fac.activate("plain", "1.0.0")
        assert "system_prompt" not in agent.metadata
        assert "prompt_refs" not in agent.metadata
    finally:
        fac.close()
        reg.close()
