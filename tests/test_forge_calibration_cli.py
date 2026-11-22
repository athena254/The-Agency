"""CLI tests for the gate-calibration runbook (forge plan P4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from typer.testing import CliRunner

from agency.cli.main import app
from agency.forge.reviewer import ReviewReport, ReviewStore


def _seed_review_db(db_path: str, gate: str = "PASS", score: int = 5) -> None:
    store = ReviewStore(db_path)
    try:
        store.save(
            ReviewReport(
                report_id=str(uuid4()),
                created_at=datetime.now(UTC),
                root="D:/fake/worktree",
                gate=gate,  # type: ignore[arg-type]
                score=score,
            )
        )
    finally:
        store.close()


def test_calibrate_export_writes_labeling_worksheet(tmp_path) -> None:
    db = str(tmp_path / "review.db")
    out = str(tmp_path / "worksheet.jsonl")
    _seed_review_db(db)
    result = CliRunner().invoke(app, ["forge", "calibrate-export", "--db", db, "--out", out])
    assert result.exit_code == 0
    assert "Exported 1 verdicts" in result.output
    row = json.loads(open(out, encoding="utf-8").readline())
    assert row["gate"] == "PASS"
    assert row["score"] == 5
    assert row["correct"] is None
    assert row["findings"] == []


def test_calibrate_score_reports_agreement(tmp_path) -> None:
    labels = tmp_path / "labeled.jsonl"
    rows = [
        {"report_id": "a", "correct": True},
        {"report_id": "b", "correct": True},
        {"report_id": "c", "correct": False},
    ]
    labels.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")
    result = CliRunner().invoke(app, ["forge", "calibrate-score", "--labels", str(labels)])
    assert result.exit_code == 0
    assert "Agreement: 67%" in result.output
    assert "revise the rubric" in result.output


def test_calibrate_score_above_bar(tmp_path) -> None:
    labels = tmp_path / "labeled.jsonl"
    rows = [{"report_id": str(i), "correct": True} for i in range(4)]
    rows.append({"report_id": "x", "correct": False})
    labels.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")
    result = CliRunner().invoke(app, ["forge", "calibrate-score", "--labels", str(labels)])
    assert result.exit_code == 0
    assert "Agreement: 80%" in result.output
    assert "blocking thresholds may stay" in result.output


def test_calibrate_score_without_labels_is_honest(tmp_path) -> None:
    labels = tmp_path / "unlabeled.jsonl"
    labels.write_text(json.dumps({"report_id": "a", "correct": None}) + "\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["forge", "calibrate-score", "--labels", str(labels)])
    assert result.exit_code == 0
    assert "No labeled rows" in result.output
