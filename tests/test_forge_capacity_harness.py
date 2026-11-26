"""Smoke test for the repeatable Forge capacity harness."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_capacity_harness_reports_control_plane(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "forge_capacity_harness.py"
    report_path = tmp_path / "report.json"
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--requests",
            "8",
            "--concurrency",
            "4",
            "--queue-timeout",
            "5",
            "--worktree-samples",
            "0",
            "--out",
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["control_plane"]["accepted"] == 8
    assert report["control_plane"]["rejected"] == 0
    assert "control_plane_only" in report["caveats"]
