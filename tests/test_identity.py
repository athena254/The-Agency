"""Tests for agency.kernel.identity."""

import pytest
from pydantic import ValidationError

from agency.kernel.identity import Agent, Capability, TrustLevel
from agency.kernel.policies import ActionClass


def test_trust_level_ordering():
    ranks = [TrustLevel.UNKNOWN, TrustLevel.OBSERVED, TrustLevel.VERIFIED, TrustLevel.TRUSTED, TrustLevel.HIGH_TRUST]
    assert [level.rank for level in ranks] == [0, 1, 2, 3, 4]
    assert TrustLevel.HIGH_TRUST.rank >= TrustLevel.TRUSTED.rank
    assert TrustLevel.UNKNOWN.rank <= TrustLevel.UNKNOWN.rank


def test_agent_defaults():
    agent = Agent(name="scout")
    assert agent.id
    assert agent.trust_level is TrustLevel.UNKNOWN
    assert agent.revoked is False
    assert agent.capabilities == []
    assert hasattr(agent, "created_at")


def test_capabilities_coerced_from_strings():
    agent = Agent(name="x", capabilities=["inspect", "simulate"])
    assert all(isinstance(cap, Capability) for cap in agent.capabilities)
    names = {cap.name for cap in agent.capabilities}
    assert names == {"inspect", "simulate"}
    # Strings default to the least-privilege L0 ceiling.
    assert all(cap.max_level.level == 0 for cap in agent.capabilities)


def test_explicit_capability_level_kept():
    agent = Agent(
        name="x",
        capabilities=[Capability(name="simulate", max_level=ActionClass.L2_CONTROLLED_TESTING)],
    )
    assert agent.capability_level("simulate") is ActionClass.L2_CONTROLLED_TESTING


def test_has_and_revocations():
    agent = Agent(name="x", capabilities=["inspect"])
    assert agent.has_capability("INSPECT")
    assert not agent.has_capability("nope")
    agent.revoke_capability("inspect")
    assert not agent.has_capability("inspect")
    agent.grant(Capability(name="write", max_level=ActionClass.L3_HIGH_IMPACT))
    assert agent.has_capability("write")


def test_empty_name_rejected():
    with pytest.raises(ValidationError):
        Agent(name="")
    with pytest.raises(ValidationError):
        Capability(name="   ")


def test_unknown_extra_field_rejected():
    with pytest.raises(ValidationError):
        Agent(name="x", not_a_field=1)