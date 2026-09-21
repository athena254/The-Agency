"""Tests for agent identity: trust, capabilities, permissions surface."""

import pytest
from pydantic import ValidationError

from agency.kernel.identity import Agent, Capability, TrustLevel
from agency.kernel.policies import ActionClass


def test_trust_rank_ordering():
    order = [
        TrustLevel.UNKNOWN,
        TrustLevel.OBSERVED,
        TrustLevel.VERIFIED,
        TrustLevel.TRUSTED,
        TrustLevel.HIGH_TRUST,
    ]
    ranks = [t.rank for t in order]
    assert ranks == sorted(ranks)
    assert TrustLevel.HIGH_TRUST.rank > TrustLevel.TRUSTED.rank
    assert TrustLevel.UNKNOWN.rank == 0


def test_agent_defaults():
    agent = Agent(name="scout")
    assert agent.id
    assert agent.trust_level is TrustLevel.UNKNOWN
    assert agent.revoked is False
    assert agent.capabilities == []
    assert agent.domain == "general"
    assert hasattr(agent, "created_at")


def test_capabilities_coerced_from_strings():
    agent = Agent(name="x", capabilities=["inspect", "simulate"])
    assert all(isinstance(c, Capability) for c in agent.capabilities)
    assert {c.name for c in agent.capabilities} == {"inspect", "simulate"}
    assert all(c.max_level is ActionClass.L0_OBSERVATION for c in agent.capabilities)


def test_capability_name_normalized():
    cap = Capability(name="  INSPECT  ")
    assert cap.name == "inspect"


def test_explicit_capability_level_kept():
    agent = Agent(
        name="x",
        capabilities=[Capability(name="simulate", max_level=ActionClass.L2_CONTROLLED_TESTING)],
    )
    assert agent.capability_level("simulate") is ActionClass.L2_CONTROLLED_TESTING


def test_has_capability_case_insensitive():
    agent = Agent(name="x", capabilities=["inspect"])
    assert agent.has_capability("INSPECT")
    assert agent.has_capability(" inspect ")
    assert not agent.has_capability("nope")
    assert agent.capability_level("nope") is None


def test_grant_and_revoke():
    agent = Agent(name="x", capabilities=["inspect"])
    agent.grant("write")
    assert agent.has_capability("write")
    # granting twice is idempotent
    agent.grant("write")
    assert sum(1 for c in agent.capabilities if c.name == "write") == 1
    agent.grant(Capability(name="exec", max_level=ActionClass.L3_HIGH_IMPACT))
    assert agent.capability_level("exec") is ActionClass.L3_HIGH_IMPACT
    agent.revoke_capability("inspect")
    assert not agent.has_capability("inspect")
    # revoking absent capability is a no-op
    agent.revoke_capability("missing")


def test_empty_name_rejected():
    with pytest.raises(ValidationError):
        Agent(name="")
    with pytest.raises(ValidationError):
        Capability(name="   ")
    with pytest.raises(ValidationError):
        Agent(name="x", id="   ")


def test_unknown_extra_field_rejected():
    with pytest.raises(ValidationError):
        Agent(name="x", not_a_field=1)


def test_str_and_metadata():
    cap = Capability(name="inspect")
    assert str(cap) == "inspect"
    agent = Agent(name="x", metadata={"team": "red"})
    assert agent.metadata["team"] == "red"


def test_trust_levels_distinct():
    assert TrustLevel.TRUSTED != TrustLevel.HIGH_TRUST
    assert TrustLevel("trusted") is TrustLevel.TRUSTED


def test_revoked_flag_defaults_false_and_mutable():
    agent = Agent(name="x")
    assert agent.revoked is False
    agent.revoked = True
    assert agent.revoked is True
