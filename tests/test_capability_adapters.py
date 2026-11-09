"""Tests for agency.capability.adapters against real native registries."""

from __future__ import annotations

import pytest

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.capability.adapters import (
    AgentBlueprintCapabilityAdapter,
    PromptCapabilityAdapter,
    SkillCapabilityAdapter,
    WorkflowCapabilityAdapter,
    _attested,
)
from agency.capability.contract import (
    CapabilityNotFoundError,
    CapabilityRef,
    CapabilityType,
    DependencyRelation,
    PublicationAttestation,
    verify_digest,
)
from agency.factory.prompts import PromptKind, PromptRegistry, PromptSpec, PromptStatus
from agency.factory.service import AgentFactory
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus
from agency.workflows.registry import (
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowStep,
)

ALLOWED = frozenset({"read", "write"})


def _skill_registry(tmp_path) -> SkillRegistry:
    return SkillRegistry(str(tmp_path / "skills.db"), allowed_permissions=ALLOWED)


def _publish_skill(reg: SkillRegistry, skill_id: str = "summarize-docs") -> None:
    reg.register(
        SkillSpec(
            skill_id=skill_id,
            version="1.0.0",
            name="Summarize docs",
            description="Summarize a document deterministically.",
            inputs={"doc": {"type": "string"}},
            outputs={"summary": {"type": "string"}},
            permissions=("read",),
            implementation="summarize.docs",
            provenance="agency-team",
            status=SkillStatus.DRAFT,
        )
    )
    reg.transition(skill_id, "1.0.0", SkillStatus.TESTING)
    reg.transition(skill_id, "1.0.0", SkillStatus.APPROVED, "tests passed")
    reg.publish(skill_id, "1.0.0", "human review")


def _attestation_for(adapter, capability_id: str, version: str) -> PublicationAttestation:
    contract = adapter.get(capability_id, version)
    return PublicationAttestation(
        ref=CapabilityRef(contract.ref.capability_type, capability_id, version),
        content_digest=contract.content_digest,
        approver="approver",
        decision_ref="decision-1",
        evidence_ref="evidence-1",
    )


def test_skill_published_unattested(tmp_path) -> None:
    reg = _skill_registry(tmp_path)
    try:
        _publish_skill(reg)
        adapter = SkillCapabilityAdapter(reg)
        contract = adapter.get("summarize-docs", "1.0.0")
        assert contract.lifecycle_state == "PUBLISHED_UNATTESTED"
        assert contract.native_state == "PUBLISHED"
        assert contract.requested_permissions == ("read",)
        assert contract.implementation_ref == "summarize.docs"
        assert contract.inputs == {"doc": {"type": "string"}}
        assert contract.provenance.source_id == "agency-team"
        verify_digest(contract)
    finally:
        reg.close()


def test_skill_attested_and_wrong_digest(tmp_path) -> None:
    reg = _skill_registry(tmp_path)
    try:
        _publish_skill(reg)
        base = SkillCapabilityAdapter(reg)
        attestation = _attestation_for(base, "summarize-docs", "1.0.0")
        adapter = SkillCapabilityAdapter(reg, lambda ref, d: attestation)
        contract = adapter.get("summarize-docs", "1.0.0")
        assert contract.lifecycle_state == "PUBLISHED"
        verify_digest(contract)

        wrong = PublicationAttestation(
            ref=attestation.ref,
            content_digest="sha256:" + "0" * 64,
            approver="approver",
            decision_ref="decision-1",
            evidence_ref="evidence-1",
        )
        adapter_wrong = SkillCapabilityAdapter(reg, lambda ref, d: wrong)
        assert adapter_wrong.get("summarize-docs", "1.0.0").lifecycle_state == (
            "PUBLISHED_UNATTESTED"
        )
        assert _attested(lambda ref, d: wrong, attestation.ref, attestation.content_digest) is False
    finally:
        reg.close()


def test_prompt_builtin_unattested_and_body_in_extra(tmp_path) -> None:
    reg = PromptRegistry(str(tmp_path / "prompts.db"))
    try:
        reg.seed_builtin(
            PromptSpec(
                prompt_id="base",
                version="1.0.0",
                name="Base",
                description="demo",
                domain="demo",
                kind=PromptKind.BASE,
                body="Hello body",
                provenance="builtin:seed",
                status=PromptStatus.PUBLISHED,
            ),
            evidence="seed",
        )
        adapter = PromptCapabilityAdapter(reg)
        contract = adapter.get("base", "1.0.0")
        assert contract.lifecycle_state == "PUBLISHED_UNATTESTED"
        assert contract.native_state == "PUBLISHED"
        assert contract.extra == {"domain": "demo", "kind": "BASE", "body": "Hello body"}
        assert contract.inputs is None
        verify_digest(contract)

        attestation = _attestation_for(adapter, "base", "1.0.0")
        attested = PromptCapabilityAdapter(reg, lambda ref, d: attestation).get("base", "1.0.0")
        assert attested.lifecycle_state == "PUBLISHED"
    finally:
        reg.close()


def test_workflow_registered_unverified_permissions_union(tmp_path) -> None:
    reg = WorkflowRegistry(str(tmp_path / "workflows.db"))
    try:
        reg.register(
            WorkflowDefinition(
                workflow_id="demo",
                version="1.0.0",
                steps=(
                    WorkflowStep("a", "op.a", (), ("write", "read")),
                    WorkflowStep("b", "op.b", ("a",), ("read",)),
                ),
            )
        )
        adapter = WorkflowCapabilityAdapter(reg)
        contract = adapter.get("demo", "1.0.0")
        assert contract.lifecycle_state == "REGISTERED_UNVERIFIED"
        assert contract.native_state == "REGISTERED_UNVERIFIED"
        assert contract.requested_permissions == ("read", "write")
        assert contract.extra == {
            "steps": [
                {
                    "step_id": "a",
                    "operation": "op.a",
                    "depends_on": [],
                    "permissions": ["write", "read"],
                },
                {"step_id": "b", "operation": "op.b", "depends_on": ["a"], "permissions": ["read"]},
            ]
        }
        verify_digest(contract)
    finally:
        reg.close()


class _StubPromptResolver:
    def get(self, prompt_id: str, version: str) -> PromptSpec:
        return PromptSpec(
            prompt_id=prompt_id,
            version=version,
            name=prompt_id,
            description="stub",
            domain="demo",
            kind=PromptKind.SEGMENT,
            body="Target: {{TARGET}}",
            provenance="stub",
        )


def _factory(tmp_path, **over):
    kernels = KernelRegistry()
    runtimes = RuntimeRegistry()
    params = {
        "prompt_resolver": _StubPromptResolver(),
        "authorizer": lambda actor, perm: perm == "agent.approve" and actor in {"approver"},
        "is_skill_published": lambda sid, ver: True,
        "is_workflow_published": lambda wid, ver: True,
        "is_prompt_published": lambda pid, ver: True,
        "allowed_capabilities": {"inspect", "summarize"},
        "kernel_registry": kernels,
        "runtime_registry": runtimes,
    }
    params.update(over)
    return AgentFactory(str(tmp_path / "factory.db"), **params), kernels, runtimes


def test_blueprint_states_composes_edges_and_no_runtime_id(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        bp = fac.create(
            blueprint_id="analyst",
            version="1.0.0",
            name="Analyst",
            domain="research",
            capabilities=["inspect"],
            skill_refs=[("s1", "1.0.0")],
            workflow_refs=[("w1", "2.0.0")],
            prompt_refs=[("p1", "1.0.0")],
            prompt_params={"TARGET": "docs"},
            creator="creator",
        )
        adapter = AgentBlueprintCapabilityAdapter(fac)
        contract = adapter.get("analyst", "1.0.0")
        assert contract.lifecycle_state == "DRAFT"
        assert contract.native_state == "DRAFT"
        assert contract.requested_permissions == ("inspect",)
        assert contract.implementation_ref is None
        assert [
            (e.relation, e.target.capability_type, e.target.capability_id, e.target.version)
            for e in contract.dependencies
        ] == [
            (DependencyRelation.COMPOSES, CapabilityType.SKILL, "s1", "1.0.0"),
            (DependencyRelation.COMPOSES, CapabilityType.WORKFLOW, "w1", "2.0.0"),
            (DependencyRelation.COMPOSES, CapabilityType.PROMPT, "p1", "1.0.0"),
        ]
        assert contract.extra == {
            "domain": "research",
            "prompt_params": {"TARGET": "docs"},
            "prompt_override": "",
        }
        assert bp.runtime_agent_id is None
        assert "runtime_agent_id" not in str(contract.to_dict())

        fac.approve("analyst", "1.0.0", approver="approver", evidence="review")
        agent = fac.activate("analyst", "1.0.0")
        active = adapter.get("analyst", "1.0.0")
        assert active.lifecycle_state == "ACTIVE"
        assert active.native_state == "ACTIVE"
        assert "runtime_agent_id" not in str(active.to_dict())
        assert agent.id not in str(active.to_dict())
        verify_digest(active)
    finally:
        fac.close()


@pytest.mark.parametrize(
    "setup,adapter_cls,unknown",
    [
        ("skill", SkillCapabilityAdapter, ("nope", "1.0.0")),
        ("prompt", PromptCapabilityAdapter, ("nope", "1.0.0")),
        ("workflow", WorkflowCapabilityAdapter, ("nope", "1.0.0")),
        ("blueprint", AgentBlueprintCapabilityAdapter, ("nope", "1.0.0")),
    ],
)
def test_unknown_id_not_found(tmp_path, setup, adapter_cls, unknown) -> None:
    if setup == "skill":
        reg = _skill_registry(tmp_path)
    elif setup == "prompt":
        reg = PromptRegistry(str(tmp_path / "prompts.db"))
    elif setup == "workflow":
        reg = WorkflowRegistry(str(tmp_path / "workflows.db"))
    else:
        reg, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(CapabilityNotFoundError):
            adapter_cls(reg).get(*unknown)
    finally:
        reg.close()


def test_native_preservation_and_list_refs(tmp_path) -> None:
    reg = _skill_registry(tmp_path)
    try:
        _publish_skill(reg)
        adapter = SkillCapabilityAdapter(reg, list_ids=lambda: ["summarize-docs"])
        contract = adapter.get("summarize-docs", "1.0.0")
        assert contract.lifecycle_state == "PUBLISHED_UNATTESTED"
        native_after = reg.get("summarize-docs", "1.0.0")
        assert native_after.status is SkillStatus.PUBLISHED
        assert adapter.list_refs() == [
            CapabilityRef(CapabilityType.SKILL, "summarize-docs", "1.0.0")
        ]
        assert SkillCapabilityAdapter(reg).list_refs() == []
    finally:
        reg.close()
