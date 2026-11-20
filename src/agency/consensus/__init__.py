"""Lattice consensus building blocks (deterministic, pure).

Slice B provides the application state machine; Slice C adds an
ordering-engine-agnostic block adapter and task-scoped execution grants. Both are
protocol foundation only: ABCI++, a real ordering engine, and peer actuation
remain follow-ups.
"""

from agency.consensus.actions import ActionOutcome, ActionRegistry, ActionSpec
from agency.consensus.app_state import (
    ZERO_STATE_ROOT,
    BlockContext,
    BlockResult,
    Decision,
    LatticeAppState,
    Rejection,
    RejectionReason,
)
from agency.consensus.consumer_input import ConsumerInput, check_coverage
from agency.consensus.engine_adapter import EngineAdapter, ExecutionReport

__all__ = [
    "ZERO_STATE_ROOT",
    "ActionOutcome",
    "ActionRegistry",
    "ActionSpec",
    "BlockContext",
    "BlockResult",
    "ConsumerInput",
    "Decision",
    "EngineAdapter",
    "ExecutionReport",
    "LatticeAppState",
    "Rejection",
    "RejectionReason",
    "check_coverage",
]
