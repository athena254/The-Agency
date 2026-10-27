from __future__ import annotations

from pathlib import Path

from agency.forge import SchedulerConfig, generate_timer
from agency.forge.scheduler import SchedulerStore


def test_generate_timer_text():
    cfg = SchedulerConfig(command="agency run", schedule="* * * * *")
    report = generate_timer(cfg)
    assert "[Unit]" in report.service
    assert "ExecStart=agency run" in report.service
    assert "OnCalendar=* * * * *" in report.timer
    assert "agency run" in report.cron_line
    assert "systemctl" in report.halt_instructions
    assert report.service_text == report.service


def test_generate_timer_dict():
    report = generate_timer({"command": "echo hi", "schedule": "daily"})
    assert "echo hi" in report.cron_line


def test_scheduler_persistence(tmp_path: Path):
    cfg = SchedulerConfig(command="agency run", schedule="daily")
    report = generate_timer(cfg)
    db = tmp_path / "s.db"
    store = SchedulerStore(db)
    store.save(report)
    store.close()
    reopened = SchedulerStore(db)
    try:
        loaded = reopened.load(report.report_id)
    finally:
        reopened.close()
    assert loaded is not None
    assert "agency run" in loaded.cron_line
