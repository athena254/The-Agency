"""Bounded, durable, append-only record of apprentice promotions.

Promotion to striker work is a human act. :class:`PromotionLedger` accepts only
a named human approver, refuses generic and self approvals, and never promotes
on its own. Records are appended to a JSONL file so decisions survive a restart;
the store is capped and a full ledger refuses further writes instead of
silently overwriting history. This module never dispatches, merges or pushes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_PROMOTIONS = 100
MAX_KEY_CHARS = 128
MAX_NOTE_CHARS = 1000

# Names that must never count as a human decision-maker.
_GENERIC_APPROVERS = frozenset(
    {
        "agent",
        "forge",
        "auto",
        "automatic",
        "auto-promotion",
        "auto_promotion",
        "system",
        "cli",
        "bot",
        "none",
        "n/a",
        "unknown",
        "self",
    }
)


class PromotionError(Exception):
    """Raised when a promotion is not allowed or the ledger is full."""


class PromotionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    apprentice: str = Field(min_length=1, max_length=MAX_KEY_CHARS)
    approver: str = Field(min_length=1, max_length=MAX_KEY_CHARS)
    at: str
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)


def _key(value: str) -> str:
    return str(value or "").strip().lower()[:MAX_KEY_CHARS]


class PromotionLedger:
    """Append-only promotion store backed by one bounded JSONL file."""

    def __init__(self, path: str | Path, *, max_records: int = MAX_PROMOTIONS) -> None:
        self._path = Path(path)
        self._max_records = min(max(1, int(max_records)), MAX_PROMOTIONS)
        self._promoted: set[str] = set()
        self._count = 0
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = PromotionRecord.model_validate_json(line)
            except (ValidationError, ValueError):
                # A corrupt line is not a promotion; fail closed on that entry.
                continue
            self._promoted.add(record.apprentice)
            self._count += 1

    def record_promotion(
        self, apprentice: str, *, approver: str, note: str = ""
    ) -> PromotionRecord:
        """Append a promotion and return its record, or raise ``PromotionError``."""
        apprentice_key = _key(apprentice)
        approver_clean = str(approver or "").strip()
        approver_key = approver_clean.lower()
        if not apprentice_key:
            raise PromotionError("a named apprentice is required")
        if not approver_key or approver_key in _GENERIC_APPROVERS:
            raise PromotionError("a named human approver is required")
        if approver_key == apprentice_key:
            raise PromotionError("self approval is not allowed")
        if apprentice_key in self._promoted:
            raise PromotionError("apprentice is already promoted")
        if self._count >= self._max_records:
            raise PromotionError("promotion ledger is full")

        record = PromotionRecord(
            apprentice=apprentice_key,
            approver=approver_clean[:MAX_KEY_CHARS],
            at=datetime.now(UTC).isoformat(),
            note=str(note or "").strip()[:MAX_NOTE_CHARS],
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.model_dump(mode="json")) + "\n")
        self._promoted.add(record.apprentice)
        self._count += 1
        return record

    def is_promoted(self, apprentice: str) -> bool:
        """Whether a named apprentice has a recorded human promotion."""
        return _key(apprentice) in self._promoted


__all__ = [
    "MAX_PROMOTIONS",
    "PromotionError",
    "PromotionLedger",
    "PromotionRecord",
]
