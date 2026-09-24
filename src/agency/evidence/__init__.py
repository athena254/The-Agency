"""Agency Evidence System.

Tracks technical findings and the graded, append-only evidence that supports
or demolishes them. A finding starts as a hypothesis (``LEVEL_0``) and climbs
the evidence ladder one rung at a time under the supervision of
:class:`~agency.evidence.levels.EvidenceLevelManager`.
"""

from __future__ import annotations

from .levels import EvidenceLevelManager
from .store.models import (
    EvidenceEntry,
    EvidenceLevel,
    Finding,
    Provenance,
    Severity,
    VerificationState,
)
from .store.store import EvidenceNotFoundError, EvidenceStore, FindingsFilter

__all__ = [
    "EvidenceEntry",
    "EvidenceLevel",
    "EvidenceLevelManager",
    "EvidenceNotFoundError",
    "EvidenceStore",
    "Finding",
    "FindingsFilter",
    "Provenance",
    "Severity",
    "VerificationState",
]

level_manager = EvidenceLevelManager()
