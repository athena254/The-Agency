"""Forge v1 — read-only patch inspection gate (bounded, non-executing)."""

from __future__ import annotations

from agency.forge.inspection import (
    CAVEATS,
    MAX_FILE_BYTES,
    MAX_FILES,
    FileRecord,
    InspectChangesResult,
    InspectionReport,
    InspectionStore,
    RejectedRecord,
    inspect_changes,
)

__all__ = [
    "CAVEATS",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "FileRecord",
    "InspectChangesResult",
    "InspectionReport",
    "InspectionStore",
    "RejectedRecord",
    "inspect_changes",
]
