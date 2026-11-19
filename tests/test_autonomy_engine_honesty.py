"""Honesty tests: the engine must never fake a forge build or publication."""

from __future__ import annotations

import hashlib

from agency.agents.planner import AgentPlanner
from agency.agents.registry import AgentRegistry
from agency.autonomy.engine import run_autonomous
from agency.forge.orchestration import BlacksmithOps
from agency.tools.registry import ToolRegistry

MINI_ADD_CODE = """\
def _add(a, b):
    return a + b


async def run(args, ctx):
    return {"result": _add(int(args["a"]), int(args["b"]))}
"""

MINI_ADD_TESTS = """\
import asyncio

import tool


def test_add():
    assert asyncio.run(tool.run({"a": 2, "b": 3}, None)) == {"result": 5}
"""

UNSAFE_CODE = """\
def run(args, ctx):
    return eval(args["x"])
"""


def _run(ops=None, specs=None, registry=None):
    reg = AgentRegistry()
    return run_autonomous(
        "build and use the tool",
        reg,
        AgentPlanner(reg),
        available_tools=set(),
        required_tools=["mini_add"],
        ops=ops,
        tool_specs=specs,
        tool_registry=registry,
    )


def _forge(res):
    return [e for e in res.proof.events if e.actor == "forge"]


def test_no_ops_blocks_honestly() -> None:
    res = _run()
    events = _forge(res)
    assert any(e.action == "build_blocked" for e in events)
    assert not any(e.action == "self_publish" and e.payload.get("published") for e in events)


def test_no_spec_blocks_honestly(tmp_path) -> None:
    ops = BlacksmithOps(str(tmp_path / "jobs.db"))
    try:
        res = _run(ops=ops, specs={})
        events = _forge(res)
        assert any(
            e.action == "build_blocked" and "spec" in str(e.payload.get("reason", ""))
            for e in events
        )
        assert not any(e.action == "self_publish" and e.payload.get("published") for e in events)
    finally:
        ops.close()


def test_real_pass_publishes_real_evidence(tmp_path) -> None:
    ops = BlacksmithOps(str(tmp_path / "jobs.db"))
    registry = ToolRegistry()
    try:
        res = _run(ops=ops, specs={"mini_add": (MINI_ADD_CODE, MINI_ADD_TESTS)}, registry=registry)
        events = [e for e in _forge(res) if e.action == "self_publish"]
        assert len(events) == 1
        payload = events[0].payload
        assert payload["published"] is True
        assert payload["registered"] is True
        assert payload["gate"] == "PASS"
        assert payload["report_id"]
        # real artifact digest (hash of the code), never the old name-hash stub
        assert payload["sha256"] == hashlib.sha256(MINI_ADD_CODE.encode("utf-8")).hexdigest()
        assert payload["sha256"] != hashlib.sha256(b"mini_add").hexdigest()
    finally:
        ops.close()


def test_real_fail_never_publishes(tmp_path) -> None:
    ops = BlacksmithOps(str(tmp_path / "jobs.db"))
    try:
        res = _run(ops=ops, specs={"mini_add": (UNSAFE_CODE, MINI_ADD_TESTS)})
        events = [e for e in _forge(res) if e.action == "self_publish"]
        assert len(events) == 1
        payload = events[0].payload
        assert payload["published"] is False
        assert payload["registered"] is False
        assert payload["gate"] == "FAIL"
    finally:
        ops.close()
