"""Autonomy package: internal self-governing execution.

Fully automatic but deterministic: one task in -> proven, traceable
answer out. No external input mid-run. Every consequential decision
is a pure function of declared rules + registry state.
"""

from agency.autonomy.decisions import Decision, DecisionRecord, decide
from agency.autonomy.engine import RunResult, run_autonomous
from agency.autonomy.proof import ProofBundle, ProofEvent, verify_chain
from agency.autonomy.router import (
    Assignment,
    assign_task,
    detect_cycle,
    score_agent,
)
from agency.autonomy.spawner import SpawnDecision, decide_spawn
from agency.autonomy.toolforge import ToolGapDecision, can_self_publish, decide_tool_gap

__all__ = [
    "Assignment",
    "Decision",
    "DecisionRecord",
    "ProofBundle",
    "ProofEvent",
    "RunResult",
    "SpawnDecision",
    "ToolGapDecision",
    "assign_task",
    "can_self_publish",
    "decide",
    "decide_spawn",
    "decide_tool_gap",
    "detect_cycle",
    "run_autonomous",
    "score_agent",
    "verify_chain",
]
