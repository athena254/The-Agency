"""Realistic end-to-end: task -> decompose -> spawn -> tool -> vote -> proof."""

from __future__ import annotations

import asyncio

from agency.agents.planner import AgentPlanner, SubtaskSpec
from agency.agents.registry import AgentRegistry
from agency.autonomy.engine import run_autonomous
from agency.autonomy.proof import verify_chain
from agency.kernel.identity import Agent
from agency.lattice.api import InMemoryLatticeBackend
from agency.lattice.governance import GovernanceEngine
from agency.lattice.reputation import ReputationEngine
from agency.peers.authority import PeerAuthority, PeerRegistry
from agency.peers.identities import PeerIdentity


def test_realistic_full_loop():
    # 1. task decompose + spawn + tool gap (autonomy engine)
    reg = AgentRegistry()
    reg.register(Agent(id="researcher", name="r", domain="research", capabilities=["research"]))

    def strat(task: str):
        return [
            SubtaskSpec(title="research", description=task, required_capabilities=["research"]),
            SubtaskSpec(title="compute", description="quantum", required_capabilities=["quantum"]),
        ]

    planner = AgentPlanner(reg, strategy=strat)
    res = run_autonomous(
        "do thing",
        reg,
        planner,
        {"web_search", "quantum_sim"},
        ["web_search", "quantum_sim", "forge_tool"],
    )
    assert res.spawns, "missing capability should spawn"
    assert any(g["action"] == "BUILD" for g in res.tool_gaps)
    assert verify_chain(res.proof.events), "proof chain must verify"

    # 2. lattice vote (decentralised eligible-set, no user override)
    async def _vote():
        backend = InMemoryLatticeBackend()
        await backend.initialize()
        gov = GovernanceEngine(backend, ReputationEngine(backend))
        pid = await gov.submit_proposal(
            "researcher",
            "spawn_agent",
            {"caps": ["quantum"]},
            quorum=0.66,
            eligible_voters=["researcher", "second", "third"],
        )
        # eligible set of 3, need 2 approvals
        await gov.cast_vote("researcher", pid, "approve")
        assert (await gov.get_proposal_status(pid)).status == "open", "1 approve insufficient"
        await gov.cast_vote("second", pid, "approve")
        assert (await gov.get_proposal_status(pid)).status == "passed", "2 approvals should pass"
        return pid

    asyncio.run(_vote())

    # 3. peer authority 3-of-4 signed ballots
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from agency.peers.membership import derive_genesis_membership

    ids = [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(4)]
    members = [
        {
            "peer_id": i.peer_id,
            "public_key": i.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for i in ids
    ]
    m = derive_genesis_membership(members, policy_version="peer-policy/1")
    reg = PeerRegistry()
    for ident in ids:
        reg.register(ident)
    auth = PeerAuthority(m, reg)
    for i, ident in enumerate(ids[:3]):
        wire = auth.make_ballot(ident, "prop_" + "a" * 32, "approve", monotonic_seq=i + 1)
        auth.submit_ballot(wire)
    assert auth.decide("prop_" + "a" * 32) == "passed"
    assert len(res.proof.events) >= 4, "proof must trace decompose/assign/spawn/tool"
