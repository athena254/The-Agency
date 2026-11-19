"""Management surfaces: backlog intake, forge status CLI, reviewer test-results wiring."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from typer.testing import CliRunner

from agency.cli.main import app
from agency.forge.orchestration import BlacksmithOps
from agency.forge.reviewer import run_review
from agency.forge.test_runner import TestReport


def _test_report(root: str, gate: str = "PASS") -> TestReport:
    return TestReport(
        report_id=str(uuid4()),
        created_at=datetime.now(UTC),
        root=root,
        workdir=root,
        command=("pytest",),
        gate=gate,  # type: ignore[arg-type]
        returncode=0 if gate == "PASS" else 1,
        duration_s=0.1,
        stdout="2 passed in 0.17s",
    )


class _FakeLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    async def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.payload)


def test_ingest_backlog_creates_jobs(tmp_path) -> None:
    ops = BlacksmithOps(str(tmp_path / "jobs.db"))
    try:
        prd = "# Add multiply\nImplement multiply in mathx.\n# Add divide\nImplement divide."
        jobs = ops.ingest_backlog(prd, "D:/fake/repo")
        assert len(jobs) == 2
        assert jobs[0].title == "Add multiply"
        assert jobs[1].title == "Add divide"
        assert all(j.state.value == "QUEUED" for j in jobs)
        assert ops.status().total == 2
    finally:
        ops.close()


def test_forge_status_cli_json(tmp_path) -> None:
    db = str(tmp_path / "jobs.db")
    ops = BlacksmithOps(db)
    try:
        ops.submit("Add x", "Add function x", "D:/fake/repo")
    finally:
        ops.close()
    runner = CliRunner()
    result = runner.invoke(app, ["forge", "status", "--db", db, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["total"] == 1
    assert data["by_state"]["QUEUED"] == 1


def test_llm_review_sees_real_test_results(tmp_path) -> None:
    (tmp_path / "m.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    llm = _FakeLLM({"score": 5, "findings": [], "summary": "clean"})
    report = run_review(
        str(tmp_path),
        ["m.py"],
        use_llm=True,
        llm=llm,
        tests_report=_test_report(str(tmp_path)),
    )
    assert report.gate == "PASS"
    assert llm.prompts
    assert "2 passed" in llm.prompts[0]
    assert "tests_not_run" not in report.caveats


def test_fabricated_tests_not_run_finding_is_filtered(tmp_path) -> None:
    (tmp_path / "m.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    llm = _FakeLLM(
        {
            "score": 5,
            "findings": [
                {
                    "path": "m.py",
                    "line": 1,
                    "rule_id": "tests-not-run",
                    "severity": "medium",
                    "message": "tests were not run",
                    "resolved": False,
                }
            ],
            "summary": "ok",
        }
    )
    report = run_review(
        str(tmp_path),
        ["m.py"],
        use_llm=True,
        llm=llm,
        tests_report=_test_report(str(tmp_path)),
    )
    assert not any(f.rule_id.lower() in {"tests-not-run", "tests_not_run"} for f in report.findings)


def test_no_tests_keeps_tests_not_run_caveat(tmp_path) -> None:
    (tmp_path / "m.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    llm = _FakeLLM({"score": 5, "findings": [], "summary": "clean"})
    report = run_review(str(tmp_path), ["m.py"], use_llm=True, llm=llm)
    assert "tests_not_run" in report.caveats
