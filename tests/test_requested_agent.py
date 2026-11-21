"""Offline tests for the requested-agent lifecycle on the governed Agent Factory."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.capability.adapters import SkillCapabilityAdapter
from agency.capability.attestation import AttestationError, AttestationStore
from agency.capability.contract import CapabilityRef, CapabilityType
from agency.factory.prompts import PromptRegistry, PromptStatus
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.requested_agent import (
    BASE_PROMPT_REFS,
    MAX_PURPOSE_CHARS,
    RequestedAgentRequest,
    RequestedAgentService,
)
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.kernel.identity import Agent
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.selfimprove.distiller import TaskTrace
from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus


def _allow(actor: str, perm: str) -> bool:
    return perm in {"agent.approve", "agent.self_improve"}


def _service(
    tmp_path,
    *,
    attestation_store: AttestationStore | None = None,
    self_improve_authorizer: Callable[[str, str], bool] | None = None,
):
    prompts = PromptRegistry(str(tmp_path / "prompts.db"))
    ensure_builtin_prompts(prompts)
    skills = SkillRegistry(str(tmp_path / "skills.db"))
    kernels = KernelRegistry()
    factory = AgentFactory(
        str(tmp_path / "factory.db"),
        authorizer=_allow,
        allowed_capabilities={"general", "search", "forge"},
        kernel_registry=kernels,
        runtime_registry=RuntimeRegistry(),
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
        is_skill_published=lambda sid, ver: skills.get(sid, ver).status is SkillStatus.PUBLISHED,
    )
    service = RequestedAgentService(
        factory=factory,
        prompt_registry=prompts,
        skill_registry=skills,
        attestation_store=attestation_store,
        self_improve_authorizer=self_improve_authorizer,
    )
    return service, factory, prompts, skills, kernels


def _request(**over) -> RequestedAgentRequest:
    args = {
        "blueprint_id": "scout",
        "name": "Scout",
        "purpose": "Search documents by keyword.",
        "creator": "owner",
        "domain": "research",
        "capabilities": ("search",),
    }
    args.update(over)
    return RequestedAgentRequest(**args)


def _skill(skill_id: str, version: str = "1.0.0") -> SkillSpec:
    return SkillSpec(
        skill_id=skill_id,
        version=version,
        name="Document search",
        description="Search documents by keyword.",
        implementation="search.documents",
        provenance="test",
        status=SkillStatus.DRAFT,
    )


def _publish(registry: SkillRegistry, skill_id: str, version: str = "1.0.0") -> None:
    registry.transition(skill_id, version, SkillStatus.TESTING)
    registry.transition(skill_id, version, SkillStatus.APPROVED, "offline test")
    registry.publish(skill_id, version, "offline test")


def _attestation_store() -> AttestationStore:
    return AttestationStore(authorizer=lambda actor, perm: perm == "capability.publish")


def _attest(store: AttestationStore, registry: SkillRegistry, skill_id: str, version: str) -> None:
    digest = SkillCapabilityAdapter(registry).get(skill_id, version).content_digest
    store.issue(
        CapabilityRef(CapabilityType.SKILL, skill_id, version),
        digest,
        approver="lead",
        decision_ref="decision-1",
        evidence_ref="evidence-1",
    )


def test_request_agent_activates_and_pins_purpose_prompt(tmp_path) -> None:
    service, factory, prompts, skills, kernels = _service(tmp_path)
    try:
        result = service.request_agent(_request())
        assert result.blueprint_id == "scout"
        assert result.version == "1.0.0"
        assert result.agent_id
        assert result.prompt_refs == BASE_PROMPT_REFS + (("purpose-scout", "1.0.0"),)
        bp = factory.get("scout", "1.0.0")
        assert bp.status is BlueprintStatus.ACTIVE
        agent = kernels.get_required(result.agent_id)
        assert agent.metadata["factory"] == "agent-factory-v1"
        assert agent.metadata["blueprint_id"] == "scout"
        assert agent.metadata["blueprint_version"] == "1.0.0"
        purpose = prompts.get("purpose-scout", "1.0.0")
        assert purpose.status is PromptStatus.PUBLISHED
        assert "Search documents by keyword." in purpose.body
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_request_agent_attaches_only_published_skills(tmp_path) -> None:
    service, factory, prompts, skills, _ = _service(tmp_path)
    try:
        skills.register(_skill("search-docs"))
        _publish(skills, "search-docs")
        skills.register(_skill("draft-docs"))  # stays DRAFT: must not attach
        result = service.request_agent(_request())
        assert result.attached_skills == (("search-docs", "1.0.0"),)
        assert ("draft-docs", "1.0.0") not in result.attached_skills
        body = prompts.get("purpose-scout", "1.0.0").body
        assert "search-docs v1.0.0" in body
    finally:
        factory.close()
        prompts.close()
        skills.close()


@pytest.mark.parametrize(
    "over",
    [
        {"blueprint_id": "bad id!"},
        {"blueprint_id": ""},
        {"name": "   "},
        {"purpose": ""},
        {"purpose": "x" * (MAX_PURPOSE_CHARS + 1)},
        {"domain": ""},
        {"creator": ""},
    ],
)
def test_request_agent_rejects_invalid_requests(tmp_path, over) -> None:
    service, factory, prompts, skills, _ = _service(tmp_path)
    try:
        with pytest.raises(ValueError):
            service.request_agent(_request(**over))
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_request_agent_approver_must_differ_from_creator(tmp_path) -> None:
    service, factory, prompts, skills, _ = _service(tmp_path)
    try:
        with pytest.raises(ValueError, match="approver must differ"):
            service.request_agent(_request(approver="owner"))
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_request_agent_reversions_blueprint_and_prompt(tmp_path) -> None:
    service, factory, prompts, skills, _ = _service(tmp_path)
    try:
        first = service.request_agent(_request())
        second = service.request_agent(_request())
        assert first.version == "1.0.0"
        assert second.version == "1.0.1"
        assert second.prompt_refs[-1] == ("purpose-scout", "1.0.1")
        assert factory.get("scout", "1.0.1").status is BlueprintStatus.ACTIVE
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_improve_agent_refines_prompt_and_supersedes_blueprint(tmp_path) -> None:
    service, factory, prompts, skills, kernels = _service(tmp_path, self_improve_authorizer=_allow)
    try:
        skills.register(_skill("search-docs"))
        _publish(skills, "search-docs")
        result = service.request_agent(_request())
        trace = TaskTrace(
            task_id="task-1",
            title="Find docs",
            description="Search the document store for matching keywords.",
            status="completed",
            tool_steps=[{"skill_id": "search-docs", "version": "1.0.0", "ok": False}],
        )
        report = service.improve_agent(
            result.agent_id, trace, prompt_gaps=["Prefer summary answers."], approver="human-lead"
        )
        assert report.prompt_refined is True
        assert report.prompt_version == "1.0.1"
        assert report.blueprint_version == "1.0.1"
        assert factory.get("scout", "1.0.0").status is BlueprintStatus.REVOKED
        assert factory.get("scout", "1.0.1").status is BlueprintStatus.ACTIVE
        assert factory.get("scout", "1.0.1").approver == "human-lead"
        agent = kernels.get_required(report.agent_id)
        assert agent.metadata["blueprint_version"] == "1.0.1"
        refined = prompts.get("purpose-scout", "1.0.1")
        assert refined.status is PromptStatus.PUBLISHED
        assert "Prefer summary answers." in refined.body
        assert report.evolved_skills
        for sid, ver in report.evolved_skills:
            assert skills.get(sid, ver).status is SkillStatus.DRAFT
        assert report.new_skills
        for sid, ver in report.new_skills:
            assert skills.get(sid, ver).status is SkillStatus.DRAFT
        assert report.distilled_skills
        assert set(report.pending_promotions) == set(
            report.evolved_skills + report.distilled_skills + report.new_skills
        )
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_improve_agent_fails_closed_without_self_improve_authorizer(tmp_path) -> None:
    service, factory, prompts, skills, _ = _service(tmp_path)
    try:
        result = service.request_agent(_request())
        trace = TaskTrace(task_id="task-1", title="Find docs", status="completed")
        with pytest.raises(PermissionError, match="no authorizer configured"):
            service.improve_agent(
                result.agent_id, trace, prompt_gaps=["Be concise."], approver="human-lead"
            )
        assert factory.get("scout", "1.0.0").status is BlueprintStatus.ACTIVE
        with pytest.raises(KeyError):
            factory.get("scout", "1.0.1")
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_improve_agent_requires_distinct_authorized_actor(tmp_path) -> None:
    def _gate(actor: str, perm: str) -> bool:
        return perm == "agent.self_improve" and actor == "human-lead"

    service, factory, prompts, skills, _ = _service(tmp_path, self_improve_authorizer=_gate)
    try:
        result = service.request_agent(_request())
        trace = TaskTrace(task_id="task-1", title="Find docs", status="completed")
        with pytest.raises(ValueError, match="nonblank"):
            service.improve_agent(result.agent_id, trace, prompt_gaps=["Be concise."])
        with pytest.raises(ValueError, match="differ from the requester"):
            service.improve_agent(
                result.agent_id, trace, prompt_gaps=["Be concise."], approver=result.agent_id
            )
        with pytest.raises(ValueError, match="differ from the requester"):
            service.improve_agent(
                result.agent_id, trace, prompt_gaps=["Be concise."], approver="factory-improvement"
            )
        with pytest.raises(PermissionError, match="denied"):
            service.improve_agent(
                result.agent_id, trace, prompt_gaps=["Be concise."], approver="unauthorized"
            )
        assert factory.get("scout", "1.0.0").status is BlueprintStatus.ACTIVE
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_improve_agent_authorizer_failure_denies(tmp_path) -> None:
    def _boom(actor: str, perm: str) -> bool:
        raise RuntimeError("authorizer backend down")

    service, factory, prompts, skills, _ = _service(tmp_path, self_improve_authorizer=_boom)
    try:
        result = service.request_agent(_request())
        trace = TaskTrace(task_id="task-1", title="Find docs", status="completed")
        with pytest.raises(PermissionError, match="failed closed"):
            service.improve_agent(
                result.agent_id, trace, prompt_gaps=["Be concise."], approver="human-lead"
            )
        assert factory.get("scout", "1.0.0").status is BlueprintStatus.ACTIVE
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_improve_agent_without_gaps_distills_but_keeps_version(tmp_path) -> None:
    service, factory, prompts, skills, _ = _service(tmp_path)
    try:
        result = service.request_agent(_request())
        trace = TaskTrace(task_id="task-2", title="Summarize notes", status="completed")
        report = service.improve_agent(result.agent_id, trace)
        assert report.prompt_refined is False
        assert report.blueprint_version == "1.0.0"
        assert report.agent_id == result.agent_id
        assert report.distilled_skills
        for sid, ver in report.distilled_skills:
            assert skills.get(sid, ver).status is SkillStatus.DRAFT
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_improve_agent_rejects_unknown_and_non_factory_agents(tmp_path) -> None:
    service, factory, prompts, skills, kernels = _service(tmp_path)
    try:
        trace = TaskTrace(task_id="task-3", title="Anything")
        with pytest.raises(KeyError):
            service.improve_agent("ghost", trace)
        kernels.register(Agent(id="plain", name="Plain", domain="general", capabilities=[]))
        with pytest.raises(ValueError, match="not factory-issued"):
            service.improve_agent("plain", trace)
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_publish_skill_fails_closed_without_attestation_store(tmp_path) -> None:
    service, factory, prompts, skills, _ = _service(tmp_path)
    try:
        skills.register(_skill("gap-skill"))
        with pytest.raises(AttestationError, match="requires a configured attestation store"):
            service.publish_skill("gap-skill", "1.0.0", approver="lead")
        assert skills.get("gap-skill", "1.0.0").status is SkillStatus.DRAFT
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_publish_skill_lifecycle_with_recorded_attestation(tmp_path) -> None:
    store = _attestation_store()
    service, factory, prompts, skills, _ = _service(tmp_path, attestation_store=store)
    try:
        skills.register(_skill("gap-skill"))
        _attest(store, skills, "gap-skill", "1.0.0")
        published = service.publish_skill("gap-skill", "1.0.0", approver="lead")
        assert published.status is SkillStatus.PUBLISHED
        with pytest.raises(ValueError, match="already published"):
            service.publish_skill("gap-skill", "1.0.0", approver="lead")
        with pytest.raises(ValueError, match="cannot promote"):
            service.promote_skill("gap-skill", "1.0.0")
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_publish_skill_requires_attestation_when_store_configured(tmp_path) -> None:
    service, factory, prompts, skills, _ = _service(tmp_path, attestation_store=AttestationStore())
    try:
        skills.register(_skill("gap-skill"))
        with pytest.raises(AttestationError, match="requires attestation"):
            service.publish_skill("gap-skill", "1.0.0", approver="lead")
        assert skills.get("gap-skill", "1.0.0").status is SkillStatus.DRAFT
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_improve_agent_dedups_gap_and_learned_skills_across_cycles(tmp_path) -> None:
    service, factory, prompts, skills, _ = _service(tmp_path, self_improve_authorizer=_allow)
    try:
        skills.register(_skill("search-docs"))
        _publish(skills, "search-docs")
        result = service.request_agent(_request())
        first = service.improve_agent(
            result.agent_id,
            TaskTrace(
                task_id="task-1",
                title="Find docs",
                description="Search the document store for matching keywords.",
                status="completed",
                tool_steps=[{"skill_id": "search-docs", "version": "1.0.0", "ok": False}],
            ),
            approver="human-lead",
        )
        assert first.new_skills
        assert first.distilled_skills
        second = service.improve_agent(
            first.agent_id,
            TaskTrace(
                task_id="task-2",
                title="Find docs",
                description="Search the document store for matching keywords.",
                status="completed",
                tool_steps=[{"skill_id": "search-docs", "version": "1.0.0", "ok": False}],
            ),
            approver="human-lead",
        )
        assert second.new_skills == ()
        assert second.distilled_skills == ()
        for sid, _ver in first.new_skills:
            assert len(skills.list_versions(sid)) == 1
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_improve_agent_caps_lifetime_learned_skills(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("agency.factory.requested_agent.MAX_LIFETIME_LEARNED_SKILLS", 1)
    service, factory, prompts, skills, _ = _service(tmp_path, self_improve_authorizer=_allow)
    try:
        result = service.request_agent(_request())
        first = service.improve_agent(
            result.agent_id,
            TaskTrace(task_id="task-1", title="First", status="completed"),
            prompt_gaps=["Gap one."],
            approver="human-lead",
        )
        assert len(first.new_skills) == 1
        assert first.distilled_skills == ()
        second = service.improve_agent(
            first.agent_id,
            TaskTrace(task_id="task-2", title="Second", status="completed"),
            prompt_gaps=["Gap two."],
            approver="human-lead",
        )
        assert second.new_skills == ()
        assert second.distilled_skills == ()
    finally:
        factory.close()
        prompts.close()
        skills.close()
