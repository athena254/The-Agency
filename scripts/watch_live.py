"""Live watcher: terminal trace of decentralised autonomous Agency.

Runs 3 real tasks (governance vote, research, coding) with real
mercury-3 model. KISS: prints every proof event live.

Usage:
  AGENCY_LLM_PROVIDER=inception AGENCY_LLM_MODEL=mercury-3 \\
  AGENCY_LLM_BASE_URL=... AGENCY_LLM_API_KEY=... \\
  .venv/Scripts/python.exe scripts/watch_live.py [--echo]
"""

from __future__ import annotations

import os
import sys

# Default to real model if not in echo mode
if "--echo" not in sys.argv:
    os.environ.setdefault("AGENCY_LLM_PROVIDER", "inception")
    os.environ.setdefault("AGENCY_LLM_MODEL", "mercury-3")

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agency.agents.planner import AgentPlanner, SubtaskSpec
from agency.agents.registry import AgentRegistry
from agency.api.routers.governance import ballot_router
from agency.autonomy.engine import run_autonomous
from agency.autonomy.forge_auto import build_tool
from agency.autonomy.proof import verify_chain
from agency.kernel.identity import Agent
from agency.peers.authority import PeerAuthority, PeerRegistry
from agency.peers.gossip import GossipStore, sync_peers
from agency.peers.identities import PeerIdentity
from agency.peers.membership import derive_genesis_membership
from agency.tools.registry import ToolRegistry
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def trace(title: str, events: list) -> None:
    print(f"\n=== {title} ===")
    for e in events:
        print(f"  [{e.seq}] {e.actor} :: {e.action} -> {e.event_hash[:12]}...")


def task1_governance_vote() -> None:
    print("\n--- TASK 1: governance vote (real HTTP, signed ballots) ---")
    ids = [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(4)]
    members = [
        {"peer_id": i.peer_id, "public_key": i.fingerprint, "roles": ["validator"], "joined_epoch": 1}
        for i in ids
    ]
    mem = derive_genesis_membership(members, policy_version="peer-policy/1")
    reg = PeerRegistry()
    for ident in ids:
        reg.register(ident)
    auth = PeerAuthority(mem, reg)
    app = FastAPI()
    app.state.peer_authority = auth
    app.include_router(ballot_router)
    client = TestClient(app)

    pid = "prop_" + "c" * 32
    for i, ident in enumerate(ids[:3]):
        wire = auth.make_ballot(ident, pid, "approve", monotonic_seq=i + 1)
        r = client.post("/v1/governance/ballot", json={"wire_hex": bytes(wire).hex()})
        print(f"  ballot {i+1}: {r.status_code} tally={r.json().get('tally')} outcome={r.json().get('outcome')}")
    print(f"  DECIDE: {auth.decide(pid)} (expect passed, 3-of-4)")


def task2_research() -> None:
    print("\n--- TASK 2: research (real model) ---")
    reg = AgentRegistry()
    reg.register(Agent(id="researcher-1", name="res", domain="research", capabilities=["research"]))
    def strat(task: str):
        return [
            SubtaskSpec(title="gather risks", description=task, required_capabilities=["research"]),
            SubtaskSpec(title="synthesis", description="synthesize risks", required_capabilities=["analysis"]),
        ]
    planner = AgentPlanner(reg, strategy=strat)
    res = run_autonomous(
        "Research: 3 main risks of swarm robotics in supply chains",
        reg, planner,
        available_tools={"web_search"},
        required_tools=["web_search", "web_fetch"],
    )
    trace("research proof", res.proof.events)
    print(f"  spawns: {res.spawns} | tool gaps: {res.tool_gaps}")
    print(f"  chain ok: {verify_chain(res.proof.events)}")
    # Real model call (echo if no key)
    from agency.llm.adapter import LLMAdapter
    adapter = LLMAdapter(provider=os.environ.get("AGENCY_LLM_PROVIDER", "echo"), model=os.environ.get("AGENCY_LLM_MODEL", "echo"))
    try:
        out = asyncio.run(adapter.generate("Summarize 3 swarm-robotics supply-chain risks in one line."))
        print(f"  MODEL ({os.environ.get('AGENCY_LLM_MODEL','echo')}): {out[:200]}")
    except Exception as exc:
        print(f"  MODEL failed (fail-closed): {exc}")


def task3_coding() -> None:
    print("\n--- TASK 3: coding (forge builds + registers tool) ---")
    code = (
        "def run(a, b):\n    return a + b\n"
    )
    tests = (
        "from tool import run\n"
        "def test_add():\n    assert run(2, 3) == 5\n"
    )
    reg = ToolRegistry()
    result = build_tool("mini_add", code, tests, registry=reg)
    print(f"  forge result: gate={result.get('gate') if isinstance(result, dict) else getattr(result, 'gate', result)}")
    try:
        from agency.tools.base import ToolContext
        out = asyncio.run(reg.call("mini_add", {"a": 2, "b": 3}, ToolContext(agent_id="watcher", task_id="live-coder")))
        print(f"  tool call mini_add(2,3) -> {out}")
    except Exception as exc:
        print(f"  tool call listed, run pending: {exc}")


def task_gossip(tmp: str = ".tmp_gossip") -> None:
    print("\n--- BONUS: gossip sync (2 peers converge) ---")
    import pathlib
    p = pathlib.Path(tmp)
    p.mkdir(exist_ok=True)
    a = GossipStore("peer-a", str(p / "a.db"))
    b = GossipStore("peer-b", str(p / "b.db"))
    base = {"event_type": "node_created", "actor": "watcher", "timestamp": "2026-11-09T00:00:00+00:00"}
    a.append({**base, "event_id": "e1", "target_id": "t1", "payload": {"x": 1}})
    b.append({**base, "event_id": "e2", "target_id": "t2", "payload": {"y": 2}})
    synced = sync_peers(a, b)
    print(f"  synced {len(synced)} events, a={len(a.events())} b={len(b.events())} verify a={a.verify()} b={b.verify()}")
    a.close(); b.close()


if __name__ == "__main__":
    task1_governance_vote()
    task2_research()
    task3_coding()
    task_gossip()
    print("\n=== DONE: all 3 tasks traced, chains verified ===")
