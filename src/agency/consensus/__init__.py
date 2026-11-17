"""Slice B Lattice application state machine (deterministic, pure).

An ordered block of signed peer records is executed into a byte-stable app hash
and derived decisions. Ordering-engine (ABCI++) integration comes later.
"""

from agency.consensus.app_state import (
    ZERO_STATE_ROOT,
    BlockContext,
    BlockResult,
    Decision,
    LatticeAppState,
    Rejection,
    RejectionReason,
)

__all__ = [
    "ZERO_STATE_ROOT",
    "BlockContext",
    "BlockResult",
    "Decision",
    "LatticeAppState",
    "Rejection",
    "RejectionReason",
]
