"""Autonomy package: internal self-governing execution.

Fully automatic but deterministic: one task in -> proven, traceable
answer out. No external input mid-run. Every consequential decision
is a pure function of declared rules + registry state.
"""

from agency.autonomy.decisions import Decision, DecisionRecord, decide
from agency.autonomy.engine import DispatchOutcome, RunResult, dispatch_assignments, run_autonomous
from agency.autonomy.loop_driver import (
    GateResult,
    GateRunner,
    GitWorkspace,
    LoopConfig,
    LoopDriver,
    LoopResult,
    WorkerAdapter,
    WorkerResult,
)
from agency.autonomy.loop_gates import (
    AcceptanceVerifier,
    ForgeGateRunner,
    NoopVerifier,
    Verdict,
    Verifier,
)
from agency.autonomy.loop_queue import (
    from_backlog,
    load_queue,
    parse_github_issues,
    parse_goal_plan,
    parse_repo_imports,
)
from agency.autonomy.loop_state import (
    LoopBudgets,
    LoopEvent,
    LoopItem,
    LoopState,
    LoopStore,
)
from agency.autonomy.loop_workers import (
    ClaudeCodeWorker,
    CodexWorker,
    CursorWorker,
    EditingWorker,
    ExternalWorker,
    NoopWorker,
    OpencodeWorker,
    PiWorker,
    build_task_prompt,
)
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
    "AcceptanceVerifier",
    "Assignment",
    "ClaudeCodeWorker",
    "CodexWorker",
    "CursorWorker",
    "Decision",
    "DecisionRecord",
    "DispatchOutcome",
    "EditingWorker",
    "ExternalWorker",
    "ForgeGateRunner",
    "GateResult",
    "GateRunner",
    "GitWorkspace",
    "LoopBudgets",
    "LoopConfig",
    "LoopDriver",
    "LoopEvent",
    "LoopItem",
    "LoopResult",
    "LoopState",
    "LoopStore",
    "NoopVerifier",
    "NoopWorker",
    "OpencodeWorker",
    "PiWorker",
    "ProofBundle",
    "ProofEvent",
    "RunResult",
    "SpawnDecision",
    "ToolGapDecision",
    "Verdict",
    "Verifier",
    "WorkerAdapter",
    "WorkerResult",
    "assign_task",
    "build_task_prompt",
    "can_self_publish",
    "decide",
    "decide_spawn",
    "decide_tool_gap",
    "detect_cycle",
    "dispatch_assignments",
    "from_backlog",
    "load_queue",
    "parse_github_issues",
    "parse_goal_plan",
    "parse_repo_imports",
    "run_autonomous",
    "score_agent",
    "verify_chain",
]
