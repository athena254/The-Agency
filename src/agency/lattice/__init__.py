"""Unified Lattice Service — central coordination and memory layer.

Agents program against :class:`~agency.lattice.backends.base.LatticeBackend`
and never touch a database driver directly. See ``docs/SPEC_LATTICE.md``.
"""

from __future__ import annotations

from agency.lattice.backends.base import Embedder, LatticeBackend
from agency.lattice.config import get_config, load_config, reset_config
from agency.lattice.factory import get_lattice, reset_lattice
from agency.lattice.models import (
    ConsensusProposal,
    EdgeType,
    LatticeConfig,
    LatticeEvent,
    NodeType,
    Reputation,
    Vote,
    ensure_utc,
    utc_now,
)

__all__ = [
    "ConsensusProposal",
    "EdgeType",
    "Embedder",
    "LatticeBackend",
    "LatticeConfig",
    "LatticeEvent",
    "NodeType",
    "Reputation",
    "Vote",
    "ensure_utc",
    "get_config",
    "get_lattice",
    "load_config",
    "reset_config",
    "reset_lattice",
    "utc_now",
]
