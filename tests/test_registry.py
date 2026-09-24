"""Tests for agency.kernel.registry."""

import pytest

from agency.kernel.identity import Agent, Capability, TrustLevel
from agency.kernel.policies import ActionClass
from agency.kernel.registry import AgentRegistry


def test_register_and_get():
    reg = AgentRegistry()
    agent = reg.register(Agent(id="red-1", name="red", domain="security"))
    assert reg.get("red-1") is agent
    assert reg.get_required("red-1") is agent
    with pytest.raises(KeyError):
        reg.get_required("nope")


def test_duplicate_register_rejected():
    reg = AgentRegistry()
    reg.register(Agent(id="a", name="A"))
    with pytest.raises(ValueError):
        reg.register(Agent(id="a", name="B"))


def test_list_filters_domain_and_revoked():
    reg = AgentRegistry()
    reg.register(Agent(id="a", name="A", domain="security"))
    reg.register(Agent(id="b", name="B", domain="finance"))
    reg.revoke("b")

    assert len(reg.list_agents()) == 1
    assert {a.id for a in reg.list_agents(include_revoked=True)} == {"a", "b"}
    assert [a.id for a in reg.list_agents(domain="security")] == ["a"]
    assert reg.count() == 1
    assert reg.count(include_revoked=True) == 2


def test_update_capabilities_merge_vs_replace():
    reg = AgentRegistry()
    agent = reg.register(
        Agent(
            id="x",
            name="X",
            capabilities=[Capability(name="inspect", max_level=ActionClass.L0_OBSERVATION)],
        )
    )
    reg.grant_capability(
        "x", Capability(name="simulate", max_level=ActionClass.L2_CONTROLLED_TESTING)
    )
    assert agent.has_capability("inspect")
    assert agent.has_capability("simulate")

    reg.update_capabilities("x", ["other"], replace=True)
    assert not agent.has_capability("inspect")
    assert agent.has_capability("other")

    reg.revoke_capability("x", "other")
    assert not agent.has_capability("other")


def test_revoke_sets_flag():
    reg = AgentRegistry()
    agent = reg.register(Agent(id="y", name="Y"))
    reg.revoke("y")
    assert agent.revoked is True


def test_register_from_kwargs():
    reg = AgentRegistry()
    agent = reg.register(id="z", name="Z", trust_level=TrustLevel.TRUSTED)
    assert agent.id == "z"
    assert agent.trust_level is TrustLevel.TRUSTED
    assert "z" in reg


def test_modified_since(tmp_path_factory):
    reg = AgentRegistry()
    from datetime import UTC, datetime, timedelta

    reg.register(Agent(id="recent", name="R", created_at=datetime.now(UTC)))
    reg.register(Agent(id="old", name="O", created_at=datetime.now(UTC) - timedelta(days=10)))
    assert {a.id for a in reg.modified_since(datetime.now(UTC) - timedelta(days=1))} == {"recent"}
