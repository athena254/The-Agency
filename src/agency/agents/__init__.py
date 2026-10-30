"""Public package exports, loaded only when explicitly requested.

Importing persistence DTOs must not import a server or execution runtime.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {'AgentExecutor': ('agency.agents.executor', 'AgentExecutor'), 'ExecutionContext': ('agency.agents.executor', 'ExecutionContext'), 'ExecutionResult': ('agency.agents.executor', 'ExecutionResult'), 'ExecutionStatus': ('agency.agents.executor', 'ExecutionStatus'), 'AgentLoop': ('agency.agents.loop', 'AgentLoop'), 'LoopResult': ('agency.agents.loop', 'LoopResult'), 'LoopStatus': ('agency.agents.loop', 'LoopStatus'), 'Observation': ('agency.agents.loop', 'Observation'), 'Reflection': ('agency.agents.loop', 'Reflection'), 'StepResult': ('agency.agents.loop', 'StepResult'), 'Thought': ('agency.agents.loop', 'Thought'), 'AgentPlanner': ('agency.agents.planner', 'AgentPlanner'), 'PlanStatus': ('agency.agents.planner', 'PlanStatus'), 'PlanSummary': ('agency.agents.planner', 'PlanSummary'), 'Subtask': ('agency.agents.planner', 'Subtask'), 'SubtaskSpec': ('agency.agents.planner', 'SubtaskSpec'), 'SubtaskStatus': ('agency.agents.planner', 'SubtaskStatus'), 'TaskGraph': ('agency.agents.planner', 'TaskGraph'), 'MAX_ISSUED_PROMPT_CHARS': ('agency.agents.prompts', 'MAX_ISSUED_PROMPT_CHARS'), 'issued_system_prompt': ('agency.agents.prompts', 'issued_system_prompt'), 'AgentRecord': ('agency.agents.registry', 'AgentRecord'), 'AgentRegistry': ('agency.agents.registry', 'AgentRegistry'), 'AgentStatus': ('agency.agents.registry', 'AgentStatus'), 'AgentVerifier': ('agency.agents.verifier', 'AgentVerifier'), 'CheckOutcome': ('agency.agents.verifier', 'CheckOutcome'), 'VerificationCriterion': ('agency.agents.verifier', 'VerificationCriterion'), 'VerificationResult': ('agency.agents.verifier', 'VerificationResult'), 'VerificationStatus': ('agency.agents.verifier', 'VerificationStatus')}

__all__ = ['MAX_ISSUED_PROMPT_CHARS', 'AgentExecutor', 'AgentLoop', 'AgentPlanner', 'AgentRecord', 'AgentRegistry', 'AgentStatus', 'AgentVerifier', 'CheckOutcome', 'ExecutionContext', 'ExecutionResult', 'ExecutionStatus', 'LoopResult', 'LoopStatus', 'Observation', 'PlanStatus', 'PlanSummary', 'Reflection', 'StepResult', 'Subtask', 'SubtaskSpec', 'SubtaskStatus', 'TaskGraph', 'Thought', 'VerificationCriterion', 'VerificationResult', 'VerificationStatus', 'issued_system_prompt']


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
