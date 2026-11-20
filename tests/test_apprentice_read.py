"""Tests for A1 apprentice promotion and the bounded forge_read tool."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.factory.apprentice import A1_CAPABILITIES, A1_CEILINGS, promote_apprentice_a1
from agency.factory.prompts import PromptRegistry
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.kernel.policies import ActionClass
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.tools.base import ToolContext
from agency.tools.builtin.forge_read import ForgeReadTool


def _factory(
    tmp_path: Path,
    *,
    prompts: PromptRegistry | None = None,
    allowed_capabilities: set[str] | None = None,
) -> tuple[AgentFactory, KernelRegistry, RuntimeRegistry]:
    kernels = KernelRegistry()
    runtimes = RuntimeRegistry()
    params: dict[str, Any] = {
        "authorizer": lambda actor, perm: perm == "agent.approve" and actor == "lead",
        "allowed_capabilities": allowed_capabilities or set(A1_CAPABILITIES) | {"inspect"},
        "kernel_registry": kernels,
        "runtime_registry": runtimes,
    }
    if prompts is not None:
        params["is_prompt_published"] = prompts.is_published
        params["prompt_resolver"] = prompts
    factory = AgentFactory(str(tmp_path / "factory.db"), **params)
    return factory, kernels, runtimes


def _base_blueprint(factory: AgentFactory) -> None:
    factory.create(
        "apprentice",
        "1.0.0",
        "Apprentice",
        capabilities=["forge"],
        creator="owner",
    )
    factory.approve("apprentice", "1.0.0", approver="lead", evidence="base review")
    factory.activate("apprentice", "1.0.0")


def test_promotion_creates_active_a1_and_revokes_previous(tmp_path: Path) -> None:
    prompts = PromptRegistry(str(tmp_path / "prompts.db"))
    ensure_builtin_prompts(prompts)
    factory, kernels, _ = _factory(tmp_path, prompts=prompts)
    try:
        _base_blueprint(factory)
        new_id = promote_apprentice_a1(
            factory, "apprentice", approver="lead", evidence="A1 promotion"
        )
        versions = factory.list_versions("apprentice")
        assert [bp.version for bp in versions] == ["1.0.0", "1.0.1"]
        assert versions[0].status is BlueprintStatus.REVOKED
        assert versions[1].status is BlueprintStatus.ACTIVE
        assert versions[1].runtime_agent_id == new_id
        assert versions[1].name == versions[0].name
        assert versions[1].domain == versions[0].domain
        assert versions[1].capabilities == A1_CAPABILITIES
        assert versions[1].capability_ceilings == A1_CEILINGS
        agent = kernels.get_required(new_id)
        assert agent.capability_level("forge.read") is ActionClass.L1_SAFE_ANALYSIS
        assert agent.capability_level("forge.inspect") is ActionClass.L1_SAFE_ANALYSIS
        assert agent.capability_level("forge") is ActionClass.L0_OBSERVATION
    finally:
        factory.close()
        prompts.close()


def test_promotion_rejects_self_approval(tmp_path: Path) -> None:
    prompts = PromptRegistry(str(tmp_path / "prompts.db"))
    ensure_builtin_prompts(prompts)
    factory, _, _ = _factory(tmp_path, prompts=prompts)
    try:
        _base_blueprint(factory)
        with pytest.raises(ValueError):
            promote_apprentice_a1(
                factory,
                "apprentice",
                approver="apprentice-promotion",
                evidence="self approval",
            )
    finally:
        factory.close()
        prompts.close()


def test_promotion_without_prompt_resolver_fails_closed(tmp_path: Path) -> None:
    factory, _, _ = _factory(tmp_path)
    try:
        _base_blueprint(factory)
        with pytest.raises(ValueError):
            promote_apprentice_a1(factory, "apprentice", approver="lead", evidence="A1")
        assert len(factory.list_versions("apprentice")) == 1
    finally:
        factory.close()


def test_default_blueprint_activates_l0_only(tmp_path: Path) -> None:
    factory, _, _ = _factory(tmp_path)
    try:
        factory.create("plain", "1.0.0", "Plain", capabilities=["inspect"], creator="owner")
        factory.approve("plain", "1.0.0", approver="lead", evidence="e1")
        agent = factory.activate("plain", "1.0.0")
        assert all(cap.max_level is ActionClass.L0_OBSERVATION for cap in agent.capabilities)
        assert factory.get("plain", "1.0.0").capability_ceilings == ()
    finally:
        factory.close()


def test_ceiling_above_l2_rejected(tmp_path: Path) -> None:
    factory, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(ValueError):
            factory.create(
                "high",
                "1.0.0",
                "High",
                capabilities=["forge"],
                capability_ceilings=[("forge", "L3_HIGH_IMPACT")],
                creator="owner",
            )
    finally:
        factory.close()


def test_ceiling_name_outside_capabilities_rejected(tmp_path: Path) -> None:
    factory, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(ValueError):
            factory.create(
                "mismatch",
                "1.0.0",
                "Mismatch",
                capabilities=["forge"],
                capability_ceilings=[("forge.read", "L1_SAFE_ANALYSIS")],
                creator="owner",
            )
    finally:
        factory.close()


def _worktree_ctx(tmp_path: Path) -> tuple[ForgeReadTool, ToolContext, Path]:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    tool = ForgeReadTool()
    ctx = ToolContext(
        agent_id="apprentice-a1",
        task_id="task-1",
        metadata={"worktree_root": str(worktree)},
    )
    return tool, ctx, worktree


async def test_forge_read_reads_file_inside_worktree(tmp_path: Path) -> None:
    tool, ctx, worktree = _worktree_ctx(tmp_path)
    payload = b"hello from the forge\n"
    (worktree / "note.txt").write_bytes(payload)
    result = await tool.run({"path": "note.txt"}, ctx)
    assert result.ok is True, result.error
    assert result.output == {
        "content": payload.decode("utf-8"),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


async def test_forge_read_rejects_traversal_and_external_absolute(tmp_path: Path) -> None:
    tool, ctx, _ = _worktree_ctx(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    for candidate in ("../outside.txt", str(outside)):
        result = await tool.run({"path": candidate}, ctx)
        assert result.ok is False
        assert result.error


async def test_forge_read_rejects_protected_path(tmp_path: Path) -> None:
    tool, ctx, worktree = _worktree_ctx(tmp_path)
    (worktree / ".env").write_text("SECRET=1", encoding="utf-8")
    result = await tool.run({"path": ".env"}, ctx)
    assert result.ok is False
    assert "protected" in (result.error or "")


async def test_forge_read_rejects_oversized_file(tmp_path: Path) -> None:
    tool, ctx, worktree = _worktree_ctx(tmp_path)
    (worktree / "big.txt").write_bytes(b"x" * 65537)
    result = await tool.run({"path": "big.txt"}, ctx)
    assert result.ok is False
    assert "large" in (result.error or "")


async def test_forge_read_requires_worktree_root(tmp_path: Path) -> None:
    tool = ForgeReadTool()
    ctx = ToolContext(agent_id="apprentice-a1", task_id="task-1")
    result = await tool.run({"path": "anything.txt"}, ctx)
    assert result.ok is False
    assert result.error == "worktree_root required"
