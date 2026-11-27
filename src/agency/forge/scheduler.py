"""Forge Phase 6 scheduler helper: generate (never run) scheduling artifacts.

Produces systemd user service+timer text and a cron line for running the
factory on a schedule, plus halt instructions. Capture-only: nothing is
scheduled or executed on the host.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.core.sqlite_store import open_store
from agency.llm.cron_model import cron_model

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
    model: str = Field(
        default_factory=cron_model,
        description="Model used by Agency-scheduled cron jobs (AGENCY_CRON_MODEL).",
    )


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
        self._conn = open_store(self._db_path, _SCHEMA)

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


NO_INSTALL_GUARANTEE = (
    "GENERATE-ONLY GUARANTEE: this bundle is text for human review. "
    "generate_schedule never installs, enables, starts, disables or modifies any "
    "cron job, systemd unit or scheduler, and performs no network access."
)

PROVENANCE_GENERATOR = "agency.forge.scheduler.generate_schedule"


class ScheduleBundle(BaseModel):
    """Reviewable scheduling text plus its provenance and no-install promise."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    generated_at: datetime
    config: SchedulerConfig
    service: str = Field(min_length=1)
    timer: str = Field(min_length=1)
    cron_line: str = Field(min_length=1)
    halt_instructions: str = Field(min_length=1)
    generator: str = PROVENANCE_GENERATOR
    input_digest: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    no_install: str = NO_INSTALL_GUARANTEE
    caveats: tuple[str, ...] = CAVEATS

    def render(self) -> str:
        """Return one reviewable document containing the text and its promise."""
        return (
            f"# {self.provenance}\n"
            f"# {self.no_install}\n"
            "\n"
            "## systemd service\n"
            f"{self.service}\n"
            "## systemd timer\n"
            f"{self.timer}\n"
            "## cron line\n"
            f"{self.cron_line}\n"
            "\n"
            f"Halt instructions (manual):\n{self.halt_instructions}"
        )


def generate_schedule(config: SchedulerConfig | dict[str, Any]) -> ScheduleBundle:
    """Render cron/systemd text without installing or running any scheduler."""
    normalized = SchedulerConfig(**config) if isinstance(config, dict) else config
    report = generate_timer(normalized)
    digest = hashlib.sha256(normalized.model_dump_json().encode("utf-8")).hexdigest()[:16]
    provenance = (
        f"generated by {PROVENANCE_GENERATOR} from SchedulerConfig "
        f"(sha256:{digest}) at {report.created_at.isoformat()}; "
        "capture-only, no host scheduler read or modified"
    )
    logger.info("forge.schedule_generated", digest=digest)
    return ScheduleBundle(
        generated_at=report.created_at,
        config=normalized,
        service=report.service,
        timer=report.timer,
        cron_line=report.cron_line,
        halt_instructions=report.halt_instructions,
        input_digest=digest,
        provenance=provenance,
        caveats=report.caveats,
    )
