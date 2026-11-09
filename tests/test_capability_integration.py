"""End-to-end: real SkillRegistry -> attestation -> adapter -> resolver -> evaluation -> composition."""

from __future__ import annotations

import pytest

from agency.capability.adapters import SkillCapabilityAdapter
from agency.capability.attestation import AttestationStore
from agency.capability.composition import AgentCompositionRequest, CompositionEngine
from agency.capability.contract import CapabilityRef, CapabilityType
from agency.capability.evaluation import EvaluationRunner, EvaluationSuite
from agency.capability.resolver import CapabilityResolver, ResolverError
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus


def _published_skill(tmp_path):
    reg = SkillRegistry(str(tmp_path / "skills.db"), allowed_permissions=frozenset({"read"}))
    reg.register(
        SkillSpec(
            skill_id="summarize-docs",
            version="1.0.0",
            name="Summarize docs",
            description="Deterministic summary.",
            inputs={"doc": {"type": "string"}},
            outputs={"summary": {"type": "string"}},
            permissions=(),
            implementation="summarize.docs",
            provenance="agency-team",
            status=SkillStatus.DRAFT,
        )
    )
    reg.transition("summarize-docs", "1.0.0", SkillStatus.TESTING)
    reg.transition("summarize-docs", "1.0.0", SkillStatus.APPROVED, "tests passed")
    reg.publish("summarize-docs", "1.0.0", "human review")
    return reg


def test_unattested_published_skill_is_not_governed(tmp_path) -> None:
    reg = _published_skill(tmp_path)
    adapter = SkillCapabilityAdapter(reg)
    contract = adapter.get("summarize-docs", "1.0.0")
    assert contract.lifecycle_state == "PUBLISHED_UNATTESTED"
    resolver = CapabilityResolver([adapter])
    ref = CapabilityRef(CapabilityType.SKILL, "summarize-docs", "1.0.0")
    with pytest.raises(ResolverError):
        resolver.resolve(ref, purpose="governed_composition")
    run = EvaluationRunner(resolver).run(ref, EvaluationSuite("suite-1", "1.0.0"), actor="alice")
    assert run.status == "FAILED"
    assert run.eligible_for_review is False
    reg.close()


def test_attested_skill_flows_to_composed_draft(tmp_path) -> None:
    reg = _published_skill(tmp_path)
    store = AttestationStore(authorizer=lambda actor, perm: perm == "capability.publish")
    plain = SkillCapabilityAdapter(reg)
    digest = plain.get("summarize-docs", "1.0.0").content_digest
    ref = CapabilityRef(CapabilityType.SKILL, "summarize-docs", "1.0.0")
    store.issue(ref, digest, approver="governor", decision_ref="dec-1", evidence_ref="ev-1")
    adapter = SkillCapabilityAdapter(reg, attestation_lookup=store.lookup)
    contract = adapter.get("summarize-docs", "1.0.0")
    assert contract.lifecycle_state == "PUBLISHED"
    resolver = CapabilityResolver([adapter])
    graph = resolver.resolve(ref, purpose="governed_composition")
    assert graph.nodes[0].ref.digest == digest
    run = EvaluationRunner(resolver).run(ref, EvaluationSuite("suite-1", "1.0.0"), actor="alice")
    assert run.status == "PASSED"
    assert run.eligible_for_review is True
    factory = AgentFactory(
        ":memory:",
        authorizer=lambda *a: True,
        is_skill_published=lambda *a: True,
        is_workflow_published=lambda *a: True,
        allowed_capabilities={"inspect"},
    )
    draft = CompositionEngine(resolver, factory).compose_agent(
        AgentCompositionRequest(
            blueprint_id="analyst",
            version="1.0.0",
            name="Analyst",
            domain="research",
            creator="creator",
            skill_refs=(ref,),
            capabilities=("inspect",),
        )
    )
    assert draft.blueprint.status is BlueprintStatus.DRAFT
    assert draft.blueprint.runtime_agent_id is None
    assert draft.requested_only is True
    reg.close()
