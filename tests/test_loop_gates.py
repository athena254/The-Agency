"""Tests for the autonomous loop gate composition (Phase 4)."""

from __future__ import annotations

import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import mock

from agency.autonomy import loop_gates
from agency.autonomy.loop_gates import (
    AcceptanceVerifier,
    ForgeGateRunner,
    NoopVerifier,
)
from agency.autonomy.loop_state import LoopItem


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "seed.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    (repo / "test_fail.py").write_text(
        "def test_fail():\n    raise AssertionError('acceptance-check-failed')\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _item(item_id: str = "a", **over) -> LoopItem:
    args = {"id": item_id, "title": item_id}
    args.update(over)
    return LoopItem(**args)


def test_noop_verifier_always_pass(tmp_path):
    repo = _git_repo(tmp_path)
    item = _item(acceptance=("test_fail.py",))
    verdict = NoopVerifier().verify(repo, item, ["seed.py"])
    assert verdict.gate == "PASS"
    assert verdict.detail == ""


def test_acceptance_verifier_empty_acceptance(tmp_path):
    repo = _git_repo(tmp_path)
    item = _item()
    verdict = AcceptanceVerifier().verify(repo, item, ["seed.py"])
    assert verdict.gate == "PASS"
    assert verdict.detail == "no_acceptance_criteria"


def test_acceptance_verifier_fail_on_failing_test(tmp_path):
    repo = _git_repo(tmp_path)
    item = _item(acceptance=("test_fail.py",))
    verdict = AcceptanceVerifier().verify(repo, item, ["seed.py"])
    assert verdict.gate == "FAIL"
    assert "acceptance-check-failed" in verdict.detail


def test_forge_gate_runner_pass(tmp_path):
    repo = _git_repo(tmp_path)
    item = _item(acceptance=("test_ok.py",))
    runner = ForgeGateRunner(run_tests=True, run_review=False, verifier=NoopVerifier())
    captured = {}
    real_pipeline = loop_gates.run_pipeline

    def wrapper(*args, **kwargs):
        report = real_pipeline(*args, **kwargs)
        captured["pipeline"] = report
        return report

    with mock.patch.object(loop_gates, "run_pipeline", wrapper):
        result = runner.run(repo, item, ["seed.py", "test_ok.py"])

    assert result.gate == "PASS"
    assert result.error_signature == ""
    assert captured["pipeline"].report_id in result.reports
    uuid.UUID(result.reports[0])


def test_forge_gate_runner_fail_on_failing_acceptance(tmp_path):
    repo = _git_repo(tmp_path)
    item = _item(acceptance=("test_fail.py",))
    runner = ForgeGateRunner(run_tests=True, run_review=False, verifier=NoopVerifier())

    first = runner.run(repo, item, ["seed.py", "test_fail.py"])
    second = runner.run(repo, item, ["seed.py", "test_fail.py"])

    assert first.gate == "FAIL"
    assert "acceptance-check-failed" in first.feedback
    assert first.error_signature
    assert first.error_signature == second.error_signature


def test_forge_gate_runner_verifier_failure_blocks(tmp_path):
    repo = _git_repo(tmp_path)
    item = _item(acceptance=("test_fail.py",))
    runner = ForgeGateRunner(run_tests=False, run_review=False, verifier=AcceptanceVerifier())

    result = runner.run(repo, item, ["seed.py"])

    assert result.gate == "FAIL"
    assert "acceptance-check-failed" in result.feedback


def test_forge_gate_runner_holdout_failure(tmp_path):
    repo = _git_repo(tmp_path)
    command = f'{shlex.quote(sys.executable)} -c "import sys; sys.exit(1)"'
    item = _item(description=f"holdout: smoke :: {command}")
    runner = ForgeGateRunner(run_tests=False, run_review=False, verifier=NoopVerifier())

    captured = {}
    real_pipeline = loop_gates.run_pipeline
    real_holdout = loop_gates.run_holdout

    def pipeline_wrapper(*args, **kwargs):
        report = real_pipeline(*args, **kwargs)
        captured["pipeline"] = report
        return report

    def holdout_wrapper(*args, **kwargs):
        report = real_holdout(*args, **kwargs)
        captured["holdout"] = report
        return report

    with (
        mock.patch.object(loop_gates, "run_pipeline", pipeline_wrapper),
        mock.patch.object(loop_gates, "run_holdout", holdout_wrapper),
    ):
        result = runner.run(repo, item, ["seed.py"])

    assert result.gate == "FAIL"
    assert captured["pipeline"].report_id in result.reports
    assert captured["holdout"].report_id in result.reports
    assert "smoke" in result.feedback
