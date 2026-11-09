from agency.agents.planner import Subtask, TaskGraph
from agency.agents.registry import AgentRegistry
from agency.autonomy.decisions import Decision, decide
from agency.autonomy.proof import ProofBundle, verify_chain
from agency.autonomy.router import assign_task, detect_cycle
from agency.autonomy.spawner import decide_spawn
from agency.autonomy.toolforge import can_self_publish, decide_tool_gap
from agency.kernel.identity import Agent


def _agent(aid: str, caps: list[str], revoked: bool = False) -> Agent:
    return Agent(id=aid, name=aid, domain="general", capabilities=caps, revoked=revoked)


def test_decide_allow():
    r = decide("run", has_capability=True, has_tool=True, budget_left=5, risk_trusted=True)
    assert r.decision == Decision.ALLOW


def test_decide_spawn_and_tool():
    assert (
        decide(
            "run", has_capability=False, has_tool=True, budget_left=1, risk_trusted=True
        ).decision
        == Decision.ESCALATE
    )
    assert (
        decide("run", has_capability=True, has_tool=False, budget_left=1, risk_trusted=True).rule_id
        == "R-TOOL"
    )


def test_router_assignment():
    reg = AgentRegistry()
    reg.register(_agent("a1", ["research"]))
    reg.register(_agent("a2", ["code"]))
    g = TaskGraph(task="t", subtasks=[Subtask(title="s1", required_capabilities=["code"])])
    out = assign_task(g, reg.list_agents())
    assert out[0].agent_id == "a2" and out[0].needs_spawn is False


def test_router_cycle():
    s1 = Subtask(title="a", depends_on=["b"])
    s2 = Subtask(title="b", depends_on=["a"])
    # assign ids manually for cycle detection
    s1.subtask_id = "a"
    s2.subtask_id = "b"
    s1.depends_on = ["b"]
    s2.depends_on = ["a"]
    g = TaskGraph(task="t", subtasks=[s1, s2])
    assert detect_cycle(g) is not None


def test_spawner():
    assert decide_spawn(["code"], {"research"}).spawn is True
    assert decide_spawn(["research"], {"research"}).spawn is False
    assert decide_spawn(["code"], {"research"}, depth=4).reason == "depth limit"


def test_tool_gap():
    assert decide_tool_gap("web_search", {"web_search"}).action == "USE"
    assert decide_tool_gap("code", {"web_search"}).action == "BUILD"
    assert can_self_publish("PASS", "abc", "abc") is True
    assert can_self_publish("FAIL", "abc", "abc") is False


def test_proof_chain():
    b = ProofBundle()
    b.append("agent1", "decompose", {"task": "t"})
    b.append("agent2", "verify", {"ok": True})
    assert verify_chain(b.events) is True
    tampered = list(b.events)
    assert verify_chain(tampered) is True
    # tamper payload
    from agency.autonomy.proof import ProofEvent

    bad = ProofEvent(
        seq=0,
        timestamp=b.events[0].timestamp,
        actor="evil",
        action="x",
        payload={},
        prev_hash=b.events[0].prev_hash,
        event_hash=b.events[0].event_hash,
    )
    assert verify_chain([bad]) is False
    assert b.manifest()["count"] == 2
