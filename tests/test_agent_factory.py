"""Tests for Agent Factory v1 (bounded governed slice, no LLM/exec)."""

from __future__ import annotations

import sqlite3
from threading import Event, Thread

import pytest

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.kernel.identity import Capability, TrustLevel
from agency.kernel.policies import ActionClass, PolicyEngine
from agency.kernel.registry import AgentRegistry as KernelRegistry


def _allow(actor: str, perm: str) -> bool:
    return perm == "agent.approve" and actor in {"approver", "lead"}


def _deny(actor: str, perm: str) -> bool:
    return False


def _factory(tmp_path, **over) -> tuple[AgentFactory, KernelRegistry, RuntimeRegistry]:
    kernels = KernelRegistry()
    runtimes = RuntimeRegistry()
    params = {
        "authorizer": _allow,
        "is_skill_published": lambda sid, ver: True,
        "is_workflow_published": lambda wid, ver: True,
        "allowed_capabilities": {"inspect", "summarize"},
        "kernel_registry": kernels,
        "runtime_registry": runtimes,
    }
    params.update(over)
    fac = AgentFactory(str(tmp_path / "factory.db"), **params)
    return fac, kernels, runtimes


def _draft(fac: AgentFactory, **over):
    args = {
        "blueprint_id": "analyst", "version": "1.0.0", "name": "Analyst",
        "domain": "research", "capabilities": ["inspect"],
        "creator": "creator",
    }
    args.update(over)
    return fac.create(**args)


def test_create_validate_approve_activate_happy_path(tmp_path) -> None:
    fac, kernels, runtimes = _factory(tmp_path)
    try:
        bp = _draft(fac)
        assert bp.status is BlueprintStatus.DRAFT
        assert fac.validate("analyst", "1.0.0").status is BlueprintStatus.DRAFT
        approved = fac.approve(
            "analyst", "1.0.0", approver="approver", evidence="review-1")
        assert approved.status is BlueprintStatus.APPROVED
        assert approved.approver == "approver"
        assert approved.creator != approved.approver
        agent = fac.activate("analyst", "1.0.0")
        assert agent.trust_level is TrustLevel.UNKNOWN
        assert agent.name == "Analyst"
        assert kernels.get(agent.id) is not None
        assert runtimes.get_agent(agent.id) is not None
        assert runtimes.get_agent(agent.id).agent.has_capability("inspect")
        assert fac.get("analyst", "1.0.0").status is BlueprintStatus.ACTIVE
        assert fac.get("analyst", "1.0.0").runtime_agent_id == agent.id
    finally:
        fac.close()


def test_activation_is_l0_only_and_grants_no_permission(tmp_path) -> None:
    fac, kernels, _ = _factory(tmp_path)
    try:
        _draft(fac, capabilities=["inspect", "summarize"])
        fac.approve("analyst", "1.0.0", approver="approver", evidence="e1")
        agent = fac.activate("analyst", "1.0.0")
        for cap in agent.capabilities:
            assert cap.max_level is ActionClass.L0_OBSERVATION
        engine = PolicyEngine()
        assert engine.permissions_for(agent.id) == []
        assert kernels.get_required(agent.id).trust_level is TrustLevel.UNKNOWN
    finally:
        fac.close()


def test_capability_above_l0_rejected(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(ValueError):
            _draft(fac, capabilities=[
                Capability(name="simulate", max_level=ActionClass.L2_CONTROLLED_TESTING)
            ])
    finally:
        fac.close()


def test_restart_persistence(tmp_path) -> None:
    db = str(tmp_path / "factory.db")
    kernels, runtimes = KernelRegistry(), RuntimeRegistry()
    fac = AgentFactory(db, authorizer=_allow, allowed_capabilities={"inspect"}, kernel_registry=kernels,
                       runtime_registry=runtimes)
    fac.create(blueprint_id="analyst", version="1.0.0", name="Analyst",
               capabilities=["inspect"], creator="creator")
    fac.approve("analyst", "1.0.0", approver="approver", evidence="e1")
    agent = fac.activate("analyst", "1.0.0")
    runtime_id = agent.id
    assert fac.list_events("analyst", "1.0.0")
    fac.close()
    # Restart: new empty registries (ephemeral), same SQLite file.
    fac2 = AgentFactory(db, authorizer=_allow, allowed_capabilities={"inspect"},
                        kernel_registry=KernelRegistry(),
                        runtime_registry=RuntimeRegistry())
    try:
        reopened = fac2.get("analyst", "1.0.0")
        assert reopened.status is BlueprintStatus.ACTIVE
        assert reopened.runtime_agent_id == runtime_id
        events = fac2.list_events("analyst", "1.0.0")
        assert [e["to_status"] for e in events] == ["DRAFT", "APPROVED", "ACTIVE"]
        # Fail closed: cannot re-activate a recorded ACTIVE row after restart.
        with pytest.raises(ValueError):
            fac2.activate("analyst", "1.0.0")
    finally:
        fac2.close()


def test_duplicate_version_rejected(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        _draft(fac)
        with pytest.raises(ValueError):
            _draft(fac)
        _draft(fac, version="1.0.1")
        assert len(fac.list_versions("analyst")) == 2
    finally:
        fac.close()


@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "latest", "", "1..0", "1.0.0.0"])
def test_malformed_version_rejected(tmp_path, bad: str) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(ValueError):
            _draft(fac, version=bad)
    finally:
        fac.close()


@pytest.mark.parametrize("bad", ["", "  ", "../evil", "/bin/x", "has space"])
def test_malformed_ids_rejected(tmp_path, bad: str) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(ValueError):
            _draft(fac, blueprint_id=bad)
        if not bad.strip():
            with pytest.raises(ValueError):
                _draft(fac, blueprint_id="ok", name=bad)
    finally:
        fac.close()


def test_default_capability_allowlist_denies_claims(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path, allowed_capabilities=None)
    try:
        with pytest.raises(ValueError, match="unrecognized capability"):
            _draft(fac)
        assert _draft(fac, capabilities=[], name="Security / Analysis").name == "Security / Analysis"
    finally:
        fac.close()


def test_unpinned_refs_rejected_at_create(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(ValueError):
            _draft(fac, skill_refs=["summarize-docs"])
        with pytest.raises(ValueError):
            _draft(fac, skill_refs=[{"skill_id": "s"}])
        with pytest.raises(ValueError):
            _draft(fac, workflow_refs=["demo"])
    finally:
        fac.close()


def test_missing_callbacks_fail_closed_for_requested_refs(tmp_path) -> None:
    fac, _, _ = _factory(
        tmp_path, is_skill_published=None, is_workflow_published=None)
    try:
        fac.create(blueprint_id="a", version="1.0.0", name="A",
                   skill_refs=[("s", "1.0.0")], creator="creator")
        with pytest.raises(ValueError):
            fac.validate("a", "1.0.0")
        fac.create(blueprint_id="b", version="1.0.0", name="B",
                   workflow_refs=[("w", "1.0.0")], creator="creator")
        with pytest.raises(ValueError):
            fac.validate("b", "1.0.0")
        # No refs at all validates fine without callbacks.
        fac.create(blueprint_id="c", version="1.0.0", name="C", creator="creator")
        assert fac.validate("c", "1.0.0").status is BlueprintStatus.DRAFT
    finally:
        fac.close()


def test_unpublished_pin_rejected(tmp_path) -> None:
    fac, _, _ = _factory(
        tmp_path,
        is_skill_published=lambda sid, ver: False,
        is_workflow_published=lambda wid, ver: (wid, ver) != ("ghost", "9.9.9"),
    )
    try:
        _draft(fac, skill_refs=[("s", "1.0.0")])
        with pytest.raises(ValueError):
            fac.validate("analyst", "1.0.0")
        with pytest.raises(ValueError):
            fac.approve("analyst", "1.0.0", approver="approver", evidence="e")
    finally:
        fac.close()


def test_pinned_refs_checked_against_real_registries(tmp_path) -> None:
    from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus
    from agency.workflows.registry import WorkflowDefinition, WorkflowRegistry, WorkflowStep

    skills = SkillRegistry(str(tmp_path / "s.db"), allowed_permissions=frozenset())
    skills.register(SkillSpec(skill_id="sum", version="1.0.0", name="Sum",
                              description="d", implementation="sum.docs",
                              provenance="team"))
    skills.transition("sum", "1.0.0", SkillStatus.TESTING)
    skills.transition("sum", "1.0.0", SkillStatus.APPROVED, "tests pass")
    skills.publish("sum", "1.0.0", "review ok")
    flows = WorkflowRegistry(str(tmp_path / "w.db"))
    flows.register(WorkflowDefinition(workflow_id="demo", version="1.0.0",
                                      steps=(WorkflowStep(step_id="a",
                                                          operation="op"),)))
    fac, _, _ = _factory(
        tmp_path,
        is_skill_published=lambda sid, ver: skills.get(sid, ver).status
        is SkillStatus.PUBLISHED,
        is_workflow_published=lambda wid, ver: flows.get(wid, ver) is not None,
    )
    try:
        fac.create(blueprint_id="a", version="1.0.0", name="A",
                   skill_refs=[("sum", "1.0.0")],
                   workflow_refs=[("demo", "1.0.0")], creator="creator")
        assert fac.validate("a", "1.0.0").status is BlueprintStatus.DRAFT
        # Draft (unpublished) skill version fails closed.
        skills.register(SkillSpec(skill_id="sum", version="1.0.1", name="Sum",
                                  description="d", implementation="sum.docs",
                                  provenance="team"))
        fac.create(blueprint_id="b", version="1.0.0", name="B",
                   skill_refs=[("sum", "1.0.1")], creator="creator")
        with pytest.raises(ValueError):
            fac.validate("b", "1.0.0")
    finally:
        fac.close()
        skills.close()
        flows.close()


def test_self_approval_denied(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        _draft(fac, creator="creator")
        with pytest.raises(ValueError):
            fac.approve("analyst", "1.0.0", approver="creator", evidence="e1")
        assert fac.get("analyst", "1.0.0").status is BlueprintStatus.DRAFT
    finally:
        fac.close()


def test_unauthorized_approver_denied(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path, authorizer=_deny)
    try:
        _draft(fac)
        with pytest.raises(PermissionError):
            fac.approve("analyst", "1.0.0", approver="mallory", evidence="e1")
        assert fac.get("analyst", "1.0.0").status is BlueprintStatus.DRAFT
    finally:
        fac.close()


def test_approve_without_authorizer_fails_closed(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path, authorizer=None)
    try:
        _draft(fac)
        with pytest.raises(PermissionError):
            fac.approve("analyst", "1.0.0", approver="approver", evidence="e1")
    finally:
        fac.close()


def test_approve_requires_evidence(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        _draft(fac)
        with pytest.raises(ValueError):
            fac.approve("analyst", "1.0.0", approver="approver", evidence="  ")
    finally:
        fac.close()


def test_no_preapproval_activation(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        _draft(fac)
        with pytest.raises(ValueError):
            fac.activate("analyst", "1.0.0")
    finally:
        fac.close()


def test_double_activation_rejected(tmp_path) -> None:
    fac, kernels, _ = _factory(tmp_path)
    try:
        _draft(fac)
        fac.approve("analyst", "1.0.0", approver="approver", evidence="e1")
        fac.activate("analyst", "1.0.0")
        with pytest.raises(ValueError):
            fac.activate("analyst", "1.0.0")
        assert kernels.count(include_revoked=True) == 1
    finally:
        fac.close()


def test_activation_event_failure_rolls_back_both_registries(tmp_path) -> None:
    fac, kernels, runtimes = _factory(tmp_path)
    try:
        _draft(fac)
        fac.approve("analyst", "1.0.0", approver="approver", evidence="e1")
        fac._conn.execute(
            "CREATE TRIGGER reject_activation_event BEFORE INSERT ON factory_events "
            "WHEN NEW.to_status = 'ACTIVE' BEGIN SELECT RAISE(FAIL, 'audit unavailable'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
            fac.activate("analyst", "1.0.0")
        assert fac.get("analyst", "1.0.0").status is BlueprintStatus.APPROVED
        assert fac.get("analyst", "1.0.0").runtime_agent_id is None
        assert kernels.count() == 0
        assert not runtimes.list_agents()
        assert [e["to_status"] for e in fac.list_events("analyst", "1.0.0")] == [
            "DRAFT", "APPROVED"
        ]
    finally:
        fac.close()


def test_revoke_during_activation_cleans_up_identity(tmp_path) -> None:
    kernels = KernelRegistry()
    ready = Event()
    registered = Event()
    started = Event()
    activation_done = Event()
    completed = Event()
    errors: list[Exception] = []

    class PausingRevoker(AgentFactory):
        def get(self, blueprint_id, version):
            bp = super().get(blueprint_id, version)
            if not started.is_set():
                started.set()
                assert activation_done.wait(5), "activation did not finish"
            return bp

    def revoke_in_thread() -> None:
        revoker = None
        try:
            revoker = PausingRevoker(
                str(tmp_path / "factory.db"), kernel_registry=kernels,
                runtime_registry=racing,
            )
            ready.set()
            assert registered.wait(5), "activation did not register"
            revoker.revoke("analyst", "1.0.0", evidence="incident")
        except Exception as exc:  # noqa: BLE001 - report worker failures to test thread
            errors.append(exc)
        finally:
            if revoker is not None:
                revoker.close()
            completed.set()

    class RacingRuntimeRegistry(RuntimeRegistry):
        def register(self, agent, *, metadata=None):
            result = super().register(agent, metadata=metadata)
            registered.set()
            # Old revoke reads APPROVED and pauses; serialized revoke waits
            # until activation commits before it can read the row.
            started.wait(0.2)
            return result

    racing = RacingRuntimeRegistry()
    fac, _, _ = _factory(tmp_path, kernel_registry=kernels, runtime_registry=racing)
    try:
        _draft(fac)
        fac.approve("analyst", "1.0.0", approver="approver", evidence="e1")
        thread = Thread(target=revoke_in_thread)
        thread.start()
        assert ready.wait(5), "revoker did not initialize"
        agent = fac.activate("analyst", "1.0.0")
        activation_done.set()
        assert completed.wait(5), "revocation did not finish"
        assert not errors
        assert fac.get("analyst", "1.0.0").status is BlueprintStatus.REVOKED
        assert kernels.get_required(agent.id).revoked
        assert racing.get_agent(agent.id) is None
    finally:
        activation_done.set()
        registered.set()
        fac.close()


def test_revocation_marks_row_and_kernel_identity(tmp_path) -> None:
    fac, kernels, runtimes = _factory(tmp_path)
    try:
        _draft(fac)
        fac.approve("analyst", "1.0.0", approver="approver", evidence="e1")
        agent = fac.activate("analyst", "1.0.0")
        revoked = fac.revoke("analyst", "1.0.0", evidence="incident-7")
        assert revoked.status is BlueprintStatus.REVOKED
        assert kernels.get_required(agent.id).revoked is True
        assert agent.id not in runtimes
        # Provenance retained: events survive, row still readable.
        kinds = [e["to_status"] for e in fac.list_events("analyst", "1.0.0")]
        assert kinds == ["DRAFT", "APPROVED", "ACTIVE", "REVOKED"]
        # Idempotent second revoke.
        again = fac.revoke("analyst", "1.0.0", evidence="incident-7")
        assert again.status is BlueprintStatus.REVOKED
    finally:
        fac.close()


def test_revoke_draft_without_runtime(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        _draft(fac)
        revoked = fac.revoke("analyst", "1.0.0", evidence="withdrawn")
        assert revoked.status is BlueprintStatus.REVOKED
        with pytest.raises(ValueError):
            fac.approve("analyst", "1.0.0", approver="approver", evidence="e")
    finally:
        fac.close()


def test_version_isolation_new_version_needs_approval(tmp_path) -> None:
    fac, kernels, _ = _factory(tmp_path)
    try:
        _draft(fac, version="1.0.0")
        fac.approve("analyst", "1.0.0", approver="approver", evidence="e1")
        first = fac.activate("analyst", "1.0.0")
        _draft(fac, version="1.0.1", capabilities=["inspect", "summarize"])
        assert fac.get("analyst", "1.0.1").status is BlueprintStatus.DRAFT
        with pytest.raises(ValueError):
            fac.activate("analyst", "1.0.1")
        fac.approve("analyst", "1.0.1", approver="lead", evidence="e2")
        second = fac.activate("analyst", "1.0.1")
        assert second.id != first.id
        assert kernels.get_required(first.id).revoked is False
        assert {c.name for c in second.capabilities} == {"inspect", "summarize"}
    finally:
        fac.close()


def test_unknown_blueprint_raises_keyerror(tmp_path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(KeyError):
            fac.get("missing", "1.0.0")
        with pytest.raises(KeyError):
            fac.validate("missing", "1.0.0")
        with pytest.raises(KeyError):
            fac.activate("missing", "1.0.0")
    finally:
        fac.close()
