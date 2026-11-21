from __future__ import annotations

import json

from agency.agents.bootstrap import apply_installed_agents, bootstrap_agents
from agency.agents.install import (
    InstallPlanEntry,
    PAKSyncInstallPlan,
    apply_install_plan,
    check,
    install,
)
from agency.factory.manager import AgentManager, InstalledAgent
from agency.kernel.registry import AgentRegistry


def _agent(agent_id: str) -> InstalledAgent:
    return InstalledAgent(
        name=agent_id,
        id=agent_id,
        version="1.0.0",
        domain="general",
        prompt_id=agent_id,
        description="test",
        path="docs/agents/test.md",
        modified_date="2026-11-21T00:00:00+00:00",
    )


def test_install_plan_shape_and_invert() -> None:
    plan = install([_agent("a")])
    assert plan.get() == (("a", "1.0.0"),)
    assert check(plan) == ["a"]
    inverted = plan.invert()
    assert inverted.installs == () and inverted.removals == ("a",)
    merged = plan.merge(install([_agent("b")]))
    assert merged.get() == (("a", "1.0.0"), ("b", "1.0.0"))


def test_pak_sync_plan_get_invert_merge() -> None:
    plan = PAKSyncInstallPlan(
        installs=(InstallPlanEntry(_agent("a")),),
        repo_root="/repo",
        install_from_main=True,
        all_repo_root="/repo/all",
    )
    assert plan.get().get() == (("a", "1.0.0"),)
    assert plan.invert().installs == ()
    other = PAKSyncInstallPlan(
        installs=(InstallPlanEntry(_agent("b")),),
        repo_root="/repo",
        install_from_main=False,
        all_repo_root="/repo/all",
    )
    assert len(plan.merge(other).installs) == 2


def test_apply_install_plan_is_idempotent_and_reversible() -> None:
    registry = AgentRegistry()
    plan = install([_agent("a")])
    assert apply_install_plan(plan, registry) == ["a"]
    agent = registry.get_required("a")
    assert any(cap.name == "a" for cap in agent.capabilities)
    assert apply_install_plan(plan, registry) == ["a"]
    assert apply_install_plan(plan.invert(), registry) == []
    assert registry.get("a") is None


def test_bootstrap_apply() -> None:
    registry = AgentRegistry()
    assert apply_installed_agents([_agent("a")], registry) == ["a"]
    assert registry.get("a") is not None


# --- centralized startup bootstrap (gaps.md #14) ------------------------------


def test_bootstrap_agents_installs_then_grants_manifest(tmp_path) -> None:
    """The manifest grants run after install, so refs to installed agents resolve."""
    registry = AgentRegistry()
    manifest = tmp_path / "external_skill_attachments.json"
    manifest.write_text(json.dumps({"a": [["docs-skill", "1.0.0"]]}), encoding="utf-8")
    manager = AgentManager(path=tmp_path / "agents.yaml")
    manager.save([_agent("a")])
    result = bootstrap_agents(registry, manager=manager, manifest_path=str(manifest))
    assert result.installed_agents == ("a",)
    assert result.granted_capabilities == 1
    agent = registry.get_required("a")
    assert any(cap.name == "skill-docs-skill" for cap in agent.capabilities)


def test_bootstrap_agents_missing_manifest_still_installs(tmp_path) -> None:
    registry = AgentRegistry()
    manager = AgentManager(path=tmp_path / "agents.yaml")
    manager.save([_agent("a")])
    result = bootstrap_agents(registry, manager=manager, manifest_path=str(tmp_path / "nope.json"))
    assert result.installed_agents == ("a",)
    assert result.granted_capabilities == 0


def test_bootstrap_agents_skips_unknown_manifest_agents(tmp_path) -> None:
    registry = AgentRegistry()
    manifest = tmp_path / "external_skill_attachments.json"
    manifest.write_text(json.dumps({"ghost": [["docs-skill", "1.0.0"]]}), encoding="utf-8")
    manager = AgentManager(path=tmp_path / "agents.yaml")
    manager.save([_agent("a")])
    result = bootstrap_agents(registry, manager=manager, manifest_path=str(manifest))
    assert result.installed_agents == ("a",)
    assert result.granted_capabilities == 0
