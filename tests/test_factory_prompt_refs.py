"""Tests for blueprint prompt references and REAP safety pairing."""

from __future__ import annotations

import pytest

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.factory.prompts import (
    PromptKind,
    PromptRegistry,
    PromptSpec,
    PromptStatus,
)
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.kernel.registry import AgentRegistry as KernelRegistry


def _allow(actor: str, perm: str) -> bool:
    return perm == "agent.approve" and actor in {"approver", "lead"}


def _factory(tmp_path, *, prompts: PromptRegistry | None = None, **over):
    kernels = KernelRegistry()
    runtimes = RuntimeRegistry()
    params = {
        "authorizer": _allow,
        "allowed_capabilities": {"inspect"},
        "kernel_registry": kernels,
        "runtime_registry": runtimes,
    }
    if prompts is not None:
        params["is_prompt_published"] = prompts.is_published
        params.setdefault("prompt_resolver", prompts)
    params.update(over)
    fac = AgentFactory(str(tmp_path / "factory.db"), **params)
    return fac, kernels, runtimes


def _draft(fac: AgentFactory, **over):
    args = {
        "blueprint_id": "analyst",
        "version": "1.0.0",
        "name": "Analyst",
        "domain": "research",
        "capabilities": ["inspect"],
        "creator": "creator",
    }
    args.update(over)
    return fac.create(**args)


def _published(
    reg: PromptRegistry,
    prompt_id: str = "demo",
    *,
    version: str = "1.0.0",
    body: str = "Body.",
    kind: PromptKind = PromptKind.PACK,
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


def test_unpinned_prompt_refs_rejected_at_create(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(ValueError):
            _draft(fac, prompt_refs=["demo"])
        with pytest.raises(ValueError):
            _draft(fac, prompt_refs=[{"prompt_id": "demo"}])
    finally:
        fac.close()


def test_params_or_override_require_prompt_refs(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(ValueError, match="require at least one prompt ref"):
            _draft(fac, prompt_params={"TARGET": "x"})
        with pytest.raises(ValueError, match="require at least one prompt ref"):
            _draft(fac, prompt_override="extra")
    finally:
        fac.close()


def test_prompt_param_and_override_bounds(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(ValueError, match="invalid prompt param name"):
            _draft(
                fac,
                prompt_refs=[("demo", "1.0.0")],
                prompt_params={"bad key": "x"},
            )
        with pytest.raises(ValueError, match="prompt_override exceeds"):
            _draft(fac, prompt_refs=[("demo", "1.0.0")], prompt_override="x" * 4097)
        big = {f"K{i:02d}": "x" * 512 for i in range(20)}
        with pytest.raises(ValueError, match="prompt params exceed"):
            _draft(fac, prompt_refs=[("demo", "1.0.0")], prompt_params=big)
    finally:
        fac.close()


def test_prompt_fields_round_trip(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        _draft(
            fac,
            prompt_refs=[("reap-17-malware-analysis", "1.0.0"), ("reap-safety-scope", "1.0.0")],
            prompt_params={"TARGET": "sample.exe"},
            prompt_override="Extra per-agent instruction.",
        )
        bp = fac.get("analyst", "1.0.0")
        assert bp.prompt_refs == (
            ("reap-17-malware-analysis", "1.0.0"),
            ("reap-safety-scope", "1.0.0"),
        )
        assert bp.prompt_params == {"TARGET": "sample.exe"}
        assert bp.prompt_override == "Extra per-agent instruction."
        assert bp.to_dict()["prompt_refs"] == [
            ["reap-17-malware-analysis", "1.0.0"],
            ["reap-safety-scope", "1.0.0"],
        ]
    finally:
        fac.close()


def test_missing_prompt_callback_fails_closed(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        _draft(fac, prompt_refs=[("demo", "1.0.0")])
        with pytest.raises(ValueError, match="no prompt callback"):
            fac.validate("analyst", "1.0.0")
    finally:
        fac.close()


def test_unpublished_prompt_ref_rejected(tmp_path) -> None:
    reg = PromptRegistry(str(tmp_path / "prompts.db"))
    reg.register(
        PromptSpec(
            prompt_id="draft-only",
            version="1.0.0",
            name="Draft",
            description="d",
            domain="demo",
            kind=PromptKind.SEGMENT,
            body="Body.",
            provenance="team",
        )
    )
    fac, _, _ = _factory(tmp_path, prompts=reg)
    try:
        _draft(fac, prompt_refs=[("draft-only", "1.0.0")])
        with pytest.raises(ValueError, match="is not published"):
            fac.validate("analyst", "1.0.0")
        with pytest.raises(ValueError, match="is not published"):
            fac.approve("analyst", "1.0.0", approver="approver", evidence="e")
    finally:
        fac.close()
        reg.close()


def test_published_prompt_ref_passes_validation(tmp_path) -> None:
    reg = PromptRegistry(str(tmp_path / "prompts.db"))
    _published(reg)
    fac, _, _ = _factory(tmp_path, prompts=reg)
    try:
        _draft(fac, prompt_refs=[("demo", "1.0.0")])
        assert fac.validate("analyst", "1.0.0").status is BlueprintStatus.DRAFT
        fac.approve("analyst", "1.0.0", approver="approver", evidence="e")
        assert fac.get("analyst", "1.0.0").status is BlueprintStatus.APPROVED
    finally:
        fac.close()
        reg.close()


def test_reap_packs_require_safety_segment(tmp_path) -> None:
    reg = PromptRegistry(str(tmp_path / "prompts.db"))
    ensure_builtin_prompts(reg)
    fac, _, _ = _factory(tmp_path, prompts=reg)
    try:
        _draft(fac, prompt_refs=[("reap-17-malware-analysis", "1.0.0")])
        with pytest.raises(ValueError, match="reap-safety-scope"):
            fac.validate("analyst", "1.0.0")
        with pytest.raises(ValueError, match="reap-safety-scope"):
            fac.approve("analyst", "1.0.0", approver="approver", evidence="e")
        assert fac.get("analyst", "1.0.0").status is BlueprintStatus.DRAFT
    finally:
        fac.close()
        reg.close()


def test_reap_pairing_accepts_safety_segment_and_exempt_packs(tmp_path) -> None:
    reg = PromptRegistry(str(tmp_path / "prompts.db"))
    ensure_builtin_prompts(reg)
    fac, _, _ = _factory(tmp_path, prompts=reg)
    try:
        _draft(
            fac,
            blueprint_id="paired",
            prompt_refs=[
                ("reap-17-malware-analysis", "1.0.0"),
                ("reap-safety-scope", "1.0.0"),
            ],
        )
        assert fac.validate("paired", "1.0.0").status is BlueprintStatus.DRAFT
        _draft(
            fac,
            blueprint_id="exempt",
            prompt_refs=[("reap-wildcard", "1.0.0")],
        )
        assert fac.validate("exempt", "1.0.0").status is BlueprintStatus.DRAFT
    finally:
        fac.close()
        reg.close()
