"""Tests for the policy engine: action classes, scopes, permission checks."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agency.kernel.identity import Agent, Capability, TrustLevel
from agency.kernel.policies import (
    ActionClass,
    FilesystemPolicy,
    NetworkPolicy,
    NetworkScope,
    Permission,
    PolicyEngine,
)


def _red_agent() -> Agent:
    return Agent(
        name="red-1",
        id="red-1",
        domain="security",
        trust_level=TrustLevel.TRUSTED,
        capabilities=[
            Capability(name="inspect", max_level=ActionClass.L0_OBSERVATION),
            Capability(name="simulate", max_level=ActionClass.L2_CONTROLLED_TESTING),
            Capability(name="test", max_level=ActionClass.L3_HIGH_IMPACT),
            Capability(name="clearance", max_level=ActionClass.L4_PRODUCTION),
            Capability(name="purge", max_level=ActionClass.L5_DESTRUCTIVE),
        ],
    )


def test_action_class_levels():
    assert ActionClass.L0_OBSERVATION.level == 0
    assert ActionClass.L1_SAFE_ANALYSIS.level == 1
    assert ActionClass.L2_CONTROLLED_TESTING.level == 2
    assert ActionClass.L3_HIGH_IMPACT.level == 3
    assert ActionClass.L4_PRODUCTION.level == 4
    assert ActionClass.L5_DESTRUCTIVE.level == 5


def test_permission_scope_glob():
    perm = Permission(agent_id="red-1", target_scope="staging/api", capabilities=["simulate"])
    assert perm.matches_scope("staging/api")
    assert not perm.matches_scope("staging/api/v2")
    wildcard = Permission(agent_id="red-1", target_scope="staging/*")
    assert wildcard.matches_scope("staging/api")
    assert not wildcard.matches_scope("prod/api")
    assert Permission(agent_id="r", target_scope="*").matches_scope("anything")


def test_permission_scope_rejects_whitespace():
    with pytest.raises(ValidationError):
        Permission(agent_id="a", target_scope="staging api")


def test_permission_expiry():
    past = datetime.now(UTC) - timedelta(minutes=5)
    assert Permission(
        agent_id="a", target_scope="x", created_at=past, time_limit=timedelta(minutes=1)
    ).is_expired()
    assert not Permission(
        agent_id="a", target_scope="x", time_limit=timedelta(seconds=3600)
    ).is_expired()
    assert Permission(
        agent_id="a", target_scope="x",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    ).is_expired()
    assert not Permission(agent_id="a", target_scope="x").is_expired()


def test_cannot_execute_without_permission():
    engine = PolicyEngine()
    assert engine.can_execute(ActionClass.L1_SAFE_ANALYSIS, _red_agent(), "staging/api") is False


def test_decision_denied_property():
    engine = PolicyEngine()
    d = engine.evaluate(ActionClass.L1_SAFE_ANALYSIS, _red_agent(), "staging/api")
    assert d.denied is True
    assert d.allowed is False
    assert d.reasons


def test_trust_boundary_blocks():
    engine = PolicyEngine()
    agent = Agent(
        id="low", name="low", trust_level=TrustLevel.OBSERVED,
        capabilities=[Capability(name="test", max_level=ActionClass.L3_HIGH_IMPACT)],
    )
    engine.issue(Permission(agent_id="low", target_scope="*", capabilities=["test"]))
    d = engine.evaluate(ActionClass.L3_HIGH_IMPACT, agent, "staging/api")
    assert d.denied
    assert any("trust" in r for r in d.reasons)


def test_l2_allowed_with_permission_and_scope_enforced():
    engine = PolicyEngine()
    agent = _red_agent()
    engine.issue(
        Permission(agent_id="red-1", target_scope="staging/*",
                   capabilities=["simulate"], time_limit=timedelta(minutes=30))
    )
    assert engine.can_execute(ActionClass.L2_CONTROLLED_TESTING, agent, "staging/api") is True
    assert engine.can_execute(ActionClass.L2_CONTROLLED_TESTING, agent, "prod/api") is False


def test_capability_ceiling_blocks_higher_action():
    engine = PolicyEngine()
    agent = _red_agent()
    engine.issue(Permission(agent_id="red-1", target_scope="staging/*", capabilities=["simulate"]))
    assert engine.can_execute(ActionClass.L2_CONTROLLED_TESTING, agent, "staging/api") is True
    assert engine.evaluate(ActionClass.L3_HIGH_IMPACT, agent, "staging/api").denied


def test_permission_granting_unclaimed_capability_denied():
    engine = PolicyEngine()
    agent = _red_agent()
    engine.issue(Permission(agent_id="red-1", target_scope="*", capabilities=["write"]))
    d = engine.evaluate(ActionClass.L1_SAFE_ANALYSIS, agent, "staging/api")
    assert d.denied
    assert any("grants none" in r for r in d.reasons)


def test_revoked_agent_denied():
    engine = PolicyEngine()
    agent = _red_agent()
    engine.issue(Permission(agent_id="red-1", target_scope="*", capabilities=["simulate"]))
    agent.revoked = True
    d = engine.evaluate(ActionClass.L0_OBSERVATION, agent, "staging/api")
    assert d.denied
    assert any("revoked" in r for r in d.reasons)


def test_l4_requires_human_approval():
    engine = PolicyEngine()
    agent = _red_agent()
    engine.issue(Permission(agent_id="red-1", target_scope="prod/api",
                            capabilities=["clearance"], human_approved=False))
    assert engine.can_execute(ActionClass.L4_PRODUCTION, agent, "prod/api") is False
    engine.issue(Permission(agent_id="red-1", target_scope="prod/api",
                            capabilities=["clearance"], human_approved=True))
    assert engine.can_execute(ActionClass.L4_PRODUCTION, agent, "prod/api") is True


def test_l5_requires_time_bound_human_approval_and_high_trust():
    engine = PolicyEngine()
    agent = Agent(id="red-1", name="r", domain="security",
                  trust_level=TrustLevel.HIGH_TRUST,
                  capabilities=[Capability(name="purge", max_level=ActionClass.L5_DESTRUCTIVE)])
    engine.issue(Permission(agent_id="red-1", target_scope="prod/api",
                            capabilities=["purge"], human_approved=True))
    d = engine.evaluate(ActionClass.L5_DESTRUCTIVE, agent, "prod/api")
    assert d.denied
    assert any("human-approved" in r for r in d.reasons)
    engine.issue(Permission(agent_id="red-1", target_scope="prod/api",
                            capabilities=["purge"], human_approved=True,
                            time_limit=timedelta(minutes=10)))
    assert engine.can_execute(ActionClass.L5_DESTRUCTIVE, agent, "prod/api") is True


def test_l5_requires_high_trust():
    engine = PolicyEngine()
    agent = Agent(id="t", name="x", trust_level=TrustLevel.TRUSTED,
                  capabilities=[Capability(name="test", max_level=ActionClass.L5_DESTRUCTIVE)])
    engine.issue(Permission(agent_id="t", target_scope="*", capabilities=["test"],
                            human_approved=True, time_limit=timedelta(minutes=1)))
    d = engine.evaluate(ActionClass.L5_DESTRUCTIVE, agent, "target")
    assert d.denied
    assert any("trust" in r for r in d.reasons)


def test_revoke_and_revoke_all_and_permissions_for():
    engine = PolicyEngine()
    agent = _red_agent()
    perm = Permission(agent_id="red-1", target_scope="staging/*", capabilities=["simulate"])
    engine.issue(perm)
    assert len(engine.permissions_for("red-1")) == 1
    assert engine.can_execute(ActionClass.L2_CONTROLLED_TESTING, agent, "staging/api")
    assert engine.revoke("red-1", "staging/*") is True
    assert engine.revoke("red-1", "staging/*") is False
    assert engine.can_execute(ActionClass.L2_CONTROLLED_TESTING, agent, "staging/api") is False
    engine.issue(perm)
    engine.issue(Permission(agent_id="red-1", target_scope="prod/*", capabilities=["simulate"]))
    assert engine.revoke_all("red-1") == 2
    assert engine.permissions_for("red-1") == []


def test_network_and_filesystem_policies():
    net = NetworkPolicy(scope=NetworkScope.LIMITED, allowed_hosts=["staging.internal"])
    assert net.allows("staging.internal", 443)
    assert not net.allows("evil.example.com", 443)
    fs = FilesystemPolicy(roots=("/workspace",), read_only=True, forbidden_suffixes=(".env",))
    assert fs.allows_read("/workspace/data.csv")
    assert not fs.allows_write("/workspace/data.csv")
    assert not fs.allows_read("/workspace/secret.env")
    assert not fs.allows_read("/etc/passwd")
