"""Tests for agency.kernel.policies."""

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
    assert ActionClass.L5_DESTRUCTIVE.level == 5


def test_action_class_str():
    assert str(ActionClass.L2_CONTROLLED_TESTING) == "L2_CONTROLLED_TESTING"


def test_permission_scope_glob():
    perm = Permission(agent_id="red-1", target_scope="staging/api", capabilities=["simulate"])
    assert perm.matches_scope("staging/api")
    assert not perm.matches_scope("staging/api/v2")
    assert not perm.matches_scope("other")

    wildcard = Permission(agent_id="red-1", target_scope="staging/*")
    assert wildcard.matches_scope("staging/api")
    assert wildcard.matches_scope("staging/db")
    assert not wildcard.matches_scope("prod/api")

    all_scope = Permission(agent_id="red-1", target_scope="*")
    assert all_scope.matches_scope("anything.at.all")


def test_permission_scope_rejects_whitespace():
    with pytest.raises(ValidationError):
        Permission(agent_id="a", target_scope="staging api")


def test_permission_expiry():
    past = datetime.now(UTC) - timedelta(minutes=5)
    perm = Permission(
        agent_id="a", target_scope="x", created_at=past, time_limit=timedelta(minutes=1)
    )
    assert perm.is_expired()

    fresh = Permission(agent_id="a", target_scope="x", time_limit=timedelta(seconds=3600))
    assert not fresh.is_expired()

    absolute = Permission(
        agent_id="a",
        target_scope="x",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert absolute.is_expired()


def test_cannot_execute_without_permission():
    engine = PolicyEngine()
    agent = _red_agent()
    assert engine.can_execute(ActionClass.L1_SAFE_ANALYSIS, agent, "staging/api") is False


def test_trust_boundary_blocks():
    engine = PolicyEngine()
    agent = Agent(
        id="low",
        name="low",
        trust_level=TrustLevel.OBSERVED,
        capabilities=[Capability(name="test", max_level=ActionClass.L3_HIGH_IMPACT)],
    )
    engine.issue(Permission(agent_id="low", target_scope="*", capabilities=["test"]))
    decision = engine.evaluate(ActionClass.L3_HIGH_IMPACT, agent, "staging/api")
    assert decision.denied
    assert any("trust" in reason for reason in decision.reasons)


def test_l2_execution_allowed_with_permission():
    engine = PolicyEngine()
    agent = _red_agent()
    engine.issue(
        Permission(
            agent_id="red-1",
            target_scope="staging/*",
            capabilities=["simulate"],
            time_limit=timedelta(minutes=30),
        )
    )
    assert engine.can_execute(ActionClass.L2_CONTROLLED_TESTING, agent, "staging/api") is True
    assert (
        engine.can_execute(ActionClass.L2_CONTROLLED_TESTING, agent, "prod/api") is False
    )  # scope
    assert engine.can_execute(ActionClass.L2_CONTROLLED_TESTING, agent, "staging/other") is True


def test_capability_ceiling_blocks_higher_action():
    engine = PolicyEngine()
    agent = _red_agent()  # 'simulate' ceiling is L2
    engine.issue(Permission(agent_id="red-1", target_scope="staging/*", capabilities=["simulate"]))
    assert engine.can_execute(ActionClass.L2_CONTROLLED_TESTING, agent, "staging/api") is True
    decision = engine.evaluate(ActionClass.L3_HIGH_IMPACT, agent, "staging/api")
    assert decision.denied


def test_permission_granting_unclaimed_capability_denied():
    engine = PolicyEngine()
    agent = _red_agent()  # has capability 'inspect', not 'write'
    engine.issue(Permission(agent_id="red-1", target_scope="*", capabilities=["write"]))
    decision = engine.evaluate(ActionClass.L1_SAFE_ANALYSIS, agent, "staging/api")
    assert decision.denied
    assert any("grants none" in reason for reason in decision.reasons)


def test_revoked_agent_denied():
    engine = PolicyEngine()
    agent = _red_agent()
    engine.issue(Permission(agent_id="red-1", target_scope="*", capabilities=["simulate"]))
    agent.revoked = True
    decision = engine.evaluate(ActionClass.L0_OBSERVATION, agent, "staging/api")
    assert decision.denied
    assert any("revoked" in reason for reason in decision.reasons)


def test_l4_requires_human_approval():
    engine = PolicyEngine()
    agent = _red_agent()  # TRUSTED, 'clearance' ceiling = L4
    agency_perm = Permission(
        agent_id="red-1",
        target_scope="prod/api",
        capabilities=["clearance"],
        human_approved=False,
    )
    engine.issue(agency_perm)
    assert engine.can_execute(ActionClass.L4_PRODUCTION, agent, "prod/api") is False

    approved = Permission(
        agent_id="red-1",
        target_scope="prod/api",
        capabilities=["clearance"],
        human_approved=True,
    )
    engine.issue(approved)
    assert engine.can_execute(ActionClass.L4_PRODUCTION, agent, "prod/api") is True


def test_l5_destructive_never_autonomous():
    engine = PolicyEngine()
    agent = Agent(
        id="red-1",
        name="red-1",
        domain="security",
        trust_level=TrustLevel.HIGH_TRUST,
        capabilities=[Capability(name="purge", max_level=ActionClass.L5_DESTRUCTIVE)],
    )
    # Even a matching, human-approved permission without a time bound is refused.
    engine.issue(
        Permission(
            agent_id="red-1",
            target_scope="prod/api",
            capabilities=["purge"],
            human_approved=True,
        )
    )
    decision = engine.evaluate(ActionClass.L5_DESTRUCTIVE, agent, "prod/api")
    assert decision.denied
    assert any("human-approved" in reason for reason in decision.reasons)

    # A time-bounded, human-approved permission enables the explicit override.
    engine.issue(
        Permission(
            agent_id="red-1",
            target_scope="prod/api",
            capabilities=["purge"],
            human_approved=True,
            time_limit=timedelta(minutes=10),
        )
    )
    assert engine.can_execute(ActionClass.L5_DESTRUCTIVE, agent, "prod/api") is True


def test_revoke_permission_disables_gate():
    engine = PolicyEngine()
    agent = _red_agent()
    perm = Permission(agent_id="red-1", target_scope="staging/*", capabilities=["simulate"])
    engine.issue(perm)
    assert engine.can_execute(ActionClass.L2_CONTROLLED_TESTING, agent, "staging/api")
    assert engine.revoke("red-1", "staging/*") is True
    assert engine.can_execute(ActionClass.L2_CONTROLLED_TESTING, agent, "staging/api") is False


def test_l5_requires_high_trust():
    engine = PolicyEngine()
    agent = Agent(
        id="trusted-but-not-high",
        name="x",
        trust_level=TrustLevel.TRUSTED,
        capabilities=[Capability(name="test", max_level=ActionClass.L5_DESTRUCTIVE)],
    )
    engine.issue(
        Permission(
            agent_id=agent.id,
            target_scope="*",
            capabilities=["test"],
            human_approved=True,
            time_limit=timedelta(minutes=1),
        )
    )
    decision = engine.evaluate(ActionClass.L5_DESTRUCTIVE, agent, "target")
    assert decision.denied
    assert any("trust" in reason for reason in decision.reasons)


def test_network_and_filesystem_policies():
    net = NetworkPolicy(scope=NetworkScope.LIMITED, allowed_hosts=["staging.internal"])
    assert net.allows("staging.internal", 443)
    assert not net.allows("evil.example.com", 443)

    fs = FilesystemPolicy(roots=("/workspace",), read_only=True, forbidden_suffixes=(".env",))
    assert fs.allows_read("/workspace/data.csv")
    assert not fs.allows_write("/workspace/data.csv")
    assert not fs.allows_read("/workspace/secret.env")
    assert not fs.allows_read("/etc/passwd")
