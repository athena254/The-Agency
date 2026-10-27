"""Forge Phase 6 backlog helper: split a PRD into ordered issues.

Parses markdown headings into bounded, ordered backlog items. Capture-only:
does not schedule, run, or mutate anything on the host.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

MAX_ITEMS = 64
MAX_PRD_CHARS = 1_000_000
MAX_TITLE_CHARS = 200
MAX_BODY_CHARS = 20_000

BacklogGate = Literal["PASS", "FAIL"]

CAVEATS: tuple[str, ...] = (
    "backlog_capture_only",
    "not_a_schedule",
    "release_not_authorized",
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


class BacklogItem(BaseModel):
    """A single ordered issue sliced from a PRD."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    body: str = ""
    level: int = Field(default=1, ge=1, le=6)


class BacklogReport(BaseModel):
    """Immutable bounded backlog slicing outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    prd_hash: str = ""
    items: tuple[BacklogItem, ...] = ()
    gate: BacklogGate = "PASS"
    caveats: tuple[str, ...] = CAVEATS
    note: str = "Bounded PRD slicing; does not schedule or authorize release."


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forge_backlog_reports (
    report_id  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    gate       TEXT NOT NULL,
    document   TEXT NOT NULL
);
"""


class BacklogStore:
    """Synchronous SQLite persistence for :class:`BacklogReport`."""

    def __init__(self, db_path: str | Path = "forge_backlog.db") -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, report: BacklogReport) -> str:
        self._conn.execute(
            "INSERT INTO forge_backlog_reports (report_id, created_at, gate, document) "
            "VALUES (?, ?, ?, ?)",
            (
                report.report_id,
                report.created_at.isoformat(),
                report.gate,
                report.model_dump_json(),
            ),
        )
        self._conn.commit()
        logger.info("forge.backlog_saved", report_id=report.report_id, gate=report.gate)
        return report.report_id

    def load(self, report_id: str) -> BacklogReport | None:
        cursor = self._conn.execute(
            "SELECT document FROM forge_backlog_reports WHERE report_id = ?", (report_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return BacklogReport.model_validate_json(row[0])

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _slice(prd_text: str) -> list[BacklogItem]:
    matches = list(_HEADING_RE.finditer(prd_text))
    items: list[BacklogItem] = []
    if not matches:
        text = prd_text.strip()
        if text:
            items.append(
                BacklogItem(
                    order=1,
                    title=text.splitlines()[0][:MAX_TITLE_CHARS] or "Untitled",
                    body=text[:MAX_BODY_CHARS],
                    level=1,
                )
            )
        return items
    for idx, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()[:MAX_TITLE_CHARS]
        if not title:
            title = f"Section {idx + 1}"
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(prd_text)
        body = prd_text[start:end].strip()[:MAX_BODY_CHARS]
        items.append(BacklogItem(order=idx + 1, title=title, body=body, level=level))
    return items


def slice_prd(prd_text: str) -> BacklogReport:
    if not isinstance(prd_text, str):
        raise TypeError("prd_text must be a string")
    if len(prd_text) > MAX_PRD_CHARS:
        raise ValueError(f"prd_text exceeds {MAX_PRD_CHARS} chars")

    report_id = str(uuid4())
    created_at = datetime.now(UTC)
    prd_hash = hashlib.sha256(prd_text.encode("utf-8")).hexdigest()

    all_items = _slice(prd_text)
    gate: BacklogGate = "PASS"
    if len(all_items) > MAX_ITEMS:
        gate = "FAIL"
        all_items = all_items[:MAX_ITEMS]

    # Truncate titles/bodies already bounded in model; ensure order contiguous.
    items = tuple(
        BacklogItem(order=i + 1, title=it.title, body=it.body, level=it.level)
        for i, it in enumerate(all_items)
    )

    note = "Bounded PRD slicing; does not schedule or authorize release."
    if gate == "FAIL":
        note = f"item count exceeded limit {MAX_ITEMS}; truncated to first {MAX_ITEMS}."

    logger.info("forge.backlog_sliced", items=len(items), gate=gate)
    return BacklogReport(
        report_id=report_id,
        created_at=created_at,
        prd_hash=prd_hash,
        items=items,
        gate=gate,
        caveats=CAVEATS,
        note=note,
    )
