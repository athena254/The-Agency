"""Forge Phase 6 scheduler helper: generate (never run) scheduling artifacts.

Produces systemd user service+timer text and a cron line for running the
factory on a schedule, plus halt instructions. Capture-only: nothing is
scheduled or executed on the host.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

MAX_COMMAND_CHARS = 2000
MAX_SCHEDULE_CHARS = 200
MAX_NAME_CHARS = 64
MAX_PATH_CHARS = 512
MAX_DESC_CHARS = 200

SchedulerGate = Literal["PASS", "FAIL"]

CAVEATS: tuple[str, ...] = (
    "schedule_not_applied",
    "capture_only",
    "release_not_authorized",
)


class SchedulerConfig(BaseModel):
    """Bounded configuration for generated scheduling artifacts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: str = Field(min_length=1, max_length=MAX_COMMAND_CHARS)
    schedule: str = Field(min_length=1, max_length=MAX_SCHEDULE_CHARS)
    description: str = Field(default="The Agency Forge factory run", max_length=MAX_DESC_CHARS)
    service_name: str = Field(default="agency-forge", min_length=1, max_length=MAX_NAME_CHARS)
    working_directory: str = Field(default=".", max_length=MAX_PATH_CHARS)
    on_calendar: str | None = Field(default=None, max_length=MAX_SCHEDULE_CHARS)


class SchedulerReport(BaseModel):
    """Immutable bounded scheduling artifact outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    config: SchedulerConfig
    service: str = Field(min_length=1)
    timer: str = Field(min_length=1)
    cron_line: str = Field(min_length=1)
    halt_instructions: str = Field(min_length=1)
    gate: SchedulerGate = "PASS"
    caveats: tuple[str, ...] = CAVEATS
    note: str = "Generated scheduling artifacts only; nothing is scheduled or run."

    # Convenience aliases for callers/tests that expect other field names.
    @property
    def service_text(self) -> str:
        return self.service

    @property
    def timer_text(self) -> str:
        return self.timer

    @property
    def cron(self) -> str:
        return self.cron_line

    @property
    def halt(self) -> str:
        return self.halt_instructions


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forge_scheduler_reports (
    report_id  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    gate       TEXT NOT NULL,
    document   TEXT NOT NULL
);
"""


class SchedulerStore:
    """Synchronous SQLite persistence for :class:`SchedulerReport`."""

    def __init__(self, db_path: str | Path = "forge_scheduler.db") -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, report: SchedulerReport) -> str:
        self._conn.execute(
            "INSERT INTO forge_scheduler_reports (report_id, created_at, gate, document) "
            "VALUES (?, ?, ?, ?)",
            (
                report.report_id,
                report.created_at.isoformat(),
                report.gate,
                report.model_dump_json(),
            ),
        )
        self._conn.commit()
        logger.info("forge.scheduler_saved", report_id=report.report_id, gate=report.gate)
        return report.report_id

    def load(self, report_id: str) -> SchedulerReport | None:
        cursor = self._conn.execute(
            "SELECT document FROM forge_scheduler_reports WHERE report_id = ?", (report_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return SchedulerReport.model_validate_json(row[0])

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def generate_timer(config: SchedulerConfig | dict[str, Any]) -> SchedulerReport:
    if isinstance(config, dict):
        config = SchedulerConfig(**config)

    report_id = str(uuid4())
    created_at = datetime.now(UTC)

    service_name = config.service_name
    service_unit_name = f"{service_name}.service"
    timer_unit_name = f"{service_name}.timer"
    on_calendar = config.on_calendar or config.schedule

    service = (
        f"[Unit]\n"
        f"Description={config.description}\n"
        f"\n"
        f"[Service]\n"
        f"Type=oneshot\n"
        f"WorkingDirectory={config.working_directory}\n"
        f"ExecStart={config.command}\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=default.target\n"
    )

    timer = (
        f"[Unit]\n"
        f"Description={config.description} (timer)\n"
        f"\n"
        f"[Timer]\n"
        f"OnCalendar={on_calendar}\n"
        f"Persistent=true\n"
        f"Unit={service_unit_name}\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=timers.target\n"
    )

    cron_line = f"{config.schedule} {config.command}"

    halt_instructions = (
        "To halt/disable the schedule (run as the owning user, no host changes made by this tool):\n"
        f"  systemctl --user disable --now {timer_unit_name}\n"
        f"  systemctl --user stop {service_unit_name}\n"
        "  # Remove the cron line from your crontab if used:\n"
        f"  crontab -l | grep -v {config.command!r} | crontab -\n"
        "  # Or edit with: crontab -e\n"
    )

    logger.info("forge.scheduler_generated", service=service_name)
    return SchedulerReport(
        report_id=report_id,
        created_at=created_at,
        config=config,
        service=service,
        timer=timer,
        cron_line=cron_line,
        halt_instructions=halt_instructions,
        gate="PASS",
        caveats=CAVEATS,
    )
