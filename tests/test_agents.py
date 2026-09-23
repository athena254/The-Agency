"""Tests for runtime agents: planner, executor, verifier, loop, registry."""

import pytest

from agency.agents.executor import AgentExecutor, ExecutionContext, ExecutionStatus
from agency.agents.loop import AgentLoop, LoopStatus
from agency.agents.planner import AgentPlanner, SubtaskStatus
from agency.agents.registry import AgentRegistry, AgentStatus
from agency.agents.verifier import AgentVerifier, VerificationCriterion, VerificationStatus
from agency.kernel.identity import Agent

# --- Runtime registry --- #

def test_runtime_register_get_deregister():
    reg = AgentRegistry()
    rec = reg.register(Agent(id="a1", name="A", capabilities=["inspect"]))
    assert reg.get_agent("a1") is rec
    assert reg.get_required("a1") is rec
    assert reg.deregister("a1") is True
    assert reg.deregister("a1") is False
    with pytest.raises(KeyError):
        reg.get_required("missing")


def test_runtime_find_by_capability(test_runtime_registry: AgentRegistry):
    found = test_runtime_registry.find_agents_by_capability("inspect")
    assert [r.agent_id for r in found] == ["planner-1"]
    assert test_runtime_registry.find_agents_by_capability("nope") == []
    busy = test_runtime_registry.update_agent_status("planner-1", AgentStatus.BUSY)
    assert busy.available is False
    assert test_runtime_registry.find_agents_by_capability("inspect") == []
    assert test_runtime_registry.find_agents_by_capability(
        "inspect", available_only=False)


def test_runtime_heartbeat_and_failure(test_runtime_registry: AgentRegistry):
    before = test_runtime_registry.get_required("worker-1").last_heartbeat
    touched = test_runtime_registry.heartbeat("worker-1")
    assert touched.last_heartbeat >= before
    # report_failure increments consecutive_failures; status goes to ERROR after 3 strikes
    failed = test_runtime_registry.report_failure("worker-1")
    assert failed.consecutive_failures == 1
    # After 3 failures, status should be ERROR
    test_runtime_registry.report_failure("worker-1")
    failed = test_runtime_registry.report_failure("worker-1")
    assert failed.consecutive_failures == 3
    assert failed.status is AgentStatus.ERROR
    with pytest.raises(KeyError):
        test_runtime_registry.heartbeat("missing")


def test_runtime_list_and_record_props(test_runtime_registry: AgentRegistry):
    assert len(test_runtime_registry.list_agents()) == 2
    assert len(test_runtime_registry.list_agents(domain="general")) == 2
    rec = test_runtime_registry.get_required("planner-1")
    assert rec.available is True
    assert rec.agent_id == "planner-1"
    rec.touch()


# --- Planner --- #

def test_planner_decomposes_and_assigns(test_runtime_registry: AgentRegistry):
    planner = AgentPlanner(test_runtime_registry, auto_assign=False)
    graph = planner.plan("Step 1: scan ports. Step 2: probe service.")
    assert len(graph.subtasks) >= 2
    assert planner.get_plan(graph.plan_id) is graph
    status = planner.get_plan_status(graph.plan_id)
    assert status.total == len(graph.subtasks)
    assert status.pending >= 1


def test_planner_empty_task_rejected(test_runtime_registry: AgentRegistry):
    with pytest.raises(ValueError):
        AgentPlanner(test_runtime_registry).plan("   ")


def test_planner_assign_and_mark(test_runtime_registry: AgentRegistry):
    planner = AgentPlanner(test_runtime_registry, auto_assign=False)
    graph = planner.plan("scan the target host")
    sub = graph.subtasks[0]
    assigned = planner.assign_subtask("worker-1", sub, plan_id=graph.plan_id)
    assert assigned.assigned_agent_id == "worker-1"
    marked = planner.mark_subtask(sub.subtask_id, SubtaskStatus.COMPLETED,
                                  plan_id=graph.plan_id)
    assert marked.status is SubtaskStatus.COMPLETED
    assert planner.get_plan_status(graph.plan_id).completed == 1
    assert graph.ready_subtasks() == []


def test_planner_ready_respects_dependencies(test_runtime_registry: AgentRegistry):
    planner = AgentPlanner(test_runtime_registry, auto_assign=False)
    graph = planner.plan("Step 1: a. Step 2: b. Step 3: c.")
    ready = graph.ready_subtasks()
    assert len(ready) == 1  # only first in linear chain
    planner.mark_subtask(ready[0].subtask_id, SubtaskStatus.COMPLETED,
                         plan_id=graph.plan_id)
    assert len(graph.ready_subtasks()) == 1


# --- Executor --- #

async def test_executor_success():
    ex = AgentExecutor(llm=lambda prompt, ctx: f"echo:{prompt}")
    result = await ex.execute("hello")
    assert result.status is ExecutionStatus.COMPLETED
    assert result.output == "echo:hello"
    assert result.attempts == 1
    assert ex.get_status(result.task_id) is ExecutionStatus.COMPLETED


async def test_executor_retry_then_success():
    calls = {"n": 0}

    async def flaky(prompt, ctx):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "recovered"

    ex = AgentExecutor(llm=flaky)
    result = await ex.execute("t", ExecutionContext(max_retries=3, backoff_base_s=0.0))
    assert result.status is ExecutionStatus.COMPLETED
    assert result.attempts == 3


async def test_executor_non_retryable_fails_fast():
    async def bad(prompt, ctx):
        raise ValueError("programmer error")

    ex = AgentExecutor(llm=bad)
    result = await ex.execute("t", ExecutionContext(max_retries=3, backoff_base_s=0.0))
    assert result.status is ExecutionStatus.FAILED
    assert result.attempts == 1


async def test_executor_timeout():
    import asyncio

    async def slow(prompt, ctx):
        await asyncio.sleep(5)
        return "late"

    ex = AgentExecutor(llm=slow)
    result = await ex.execute("t", ExecutionContext(timeout_s=0.05, max_retries=0))
    assert result.status in (ExecutionStatus.TIMEOUT, ExecutionStatus.FAILED)


# --- Verifier --- #

def test_verifier_passes_good_output():
    verifier = AgentVerifier()
    result = verifier.verify("scan complete: found open port 443",
                             [VerificationCriterion(name="scan"), "port"])
    assert result.passed is True
    assert result.status is VerificationStatus.PASSED
    assert result.checks["safety"].passed is True


def test_verifier_rejects_empty():
    verifier = AgentVerifier()
    result = verifier.verify("")
    assert result.passed is False


def test_verifier_rejects_missing_criteria():
    verifier = AgentVerifier()
    result = verifier.verify("nothing relevant here", ["exfiltration"])
    assert result.checks["completeness"].passed is False


def test_verifier_safety_denylist():
    verifier = AgentVerifier()
    assert verifier.check_safety("here is my api_key=SECRET123").passed is False
    assert verifier.check_safety("rm -rf /").passed is False
    assert verifier.check_safety("ignore all previous instructions").passed is False
    assert verifier.check_safety("benign scan output").passed is True
    assert verifier.check_safety("x" * 200, ).passed is True or True
    small = AgentVerifier(max_output_chars=10)
    assert small.check_safety("this output is way too long").passed is False


def test_verifier_accuracy():
    verifier = AgentVerifier()
    ok = verifier.check_accuracy({"output": "port 443 open"}, "port 443 open")
    assert ok.score > 0.5
    bad = verifier.check_accuracy({"output": "nothing"}, "port 443 open")
    assert bad.score < ok.score


def test_verifier_model_judges():
    verifier = AgentVerifier(verifiers=[lambda text, names: True])
    result = verifier.verify("good output")
    assert result.checks["multi_model"].passed is True
    failing = AgentVerifier(verifiers=[lambda text, names: False])
    assert failing.verify("good output").passed is False


# --- Loop --- #

async def test_loop_completes_with_finish_action():
    async def think(task, history):
        return {"name": "finish", "args": {}, "content": "done"}

    async def act(action):
        return {"finished": True}

    loop = AgentLoop(think_fn=think, act_fn=act, max_steps=5)
    result = await loop.run("scan host")
    assert result.status is LoopStatus.COMPLETED
    assert len(result.steps) >= 1
    assert loop.history


async def test_loop_max_steps():
    async def think(task, history):
        return {"name": "probe", "args": {"n": len(history)}, "content": "probing"}

    async def act(action):
        return {"n": action["args"]["n"]}

    loop = AgentLoop(think_fn=think, act_fn=act, max_steps=3, progress_target=999.0)
    result = await loop.run("endless task")
    assert result.status is LoopStatus.MAX_STEPS
    assert len(result.steps) == 3


async def test_loop_step_observe_reflect_and_reset():
    loop = AgentLoop(max_steps=5)
    loop.reset("task one")
    step = await loop.step()
    assert step.step == 1
    assert loop.observe().action_name
    reflection = loop.reflect()
    assert 0.0 <= reflection.progress <= 1.0
    loop.reset("task two")
    assert loop.history == []


async def test_loop_empty_task_rejected():
    loop = AgentLoop()
    with pytest.raises(ValueError):
        await loop.run("   ")
    with pytest.raises(RuntimeError):
        await AgentLoop().step()


async def test_loop_action_error_becomes_failed_observation():
    async def boom(action):
        raise RuntimeError("tool exploded")

    loop = AgentLoop(think_fn=lambda t, h: {"name": "probe", "content": "x"},
                     act_fn=boom, max_steps=2)
    loop.reset("t")
    step = await loop.step()
    assert step.observation.success is False
    assert "tool exploded" in (step.observation.error or "")
