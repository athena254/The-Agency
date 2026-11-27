"""Parent wiring tests for the ECC hardening wave."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from agency.cli.main import app
from agency.forge.external_agent import run_external_agent
from agency.forge.project_adapters import prepare_dependencies
from agency.forge.work_order import WorkOrder, render_work_order


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "p@local"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "p"], check=True, capture_output=True
    )
    (repo / "README.md").write_text("ecc wiring\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "init"], check=True, capture_output=True
    )
    return repo


def _worker(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "worker.py"
    script.write_text(body, encoding="utf-8")
    return script


def test_adapters_delegate_to_the_generic_seam(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    note = prepare_dependencies(tmp_path, work, "python")
    assert "python" in note


def test_work_order_carries_handoff_and_regression_instructions(tmp_path: Path) -> None:
    order = WorkOrder(
        title="Fix a bug",
        task="fix the failing gate",
        repo=str(tmp_path),
        role="striker",
        job_class="standard",
        tool_budget=40,
        regression_required=True,
        diff_budget_required=True,
    )
    text = render_work_order(order)
    assert "HANDOFF ENVELOPE" in text
    assert "REGRESSION PROOF (hard)" in text
    assert "DIFF BUDGET (hard)" in text


def test_regression_proof_is_enforced(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    worker = _worker(
        tmp_path,
        "import pathlib, subprocess\n"
        "pathlib.Path('app.py').write_text('x = 1')\n"
        "subprocess.run(['git', 'add', '.'])\n"
        "subprocess.run(['git', 'commit', '-qm', 'fix'])\n",
    )
    report = run_external_agent(
        repo,
        [sys.executable, str(worker)],
        "fix a bug",
        worktree_root=tmp_path / "wt-regression",
        regression_required=True,
        run_tests_flag=False,
        run_review_flag=False,
    )
    assert report.gate == "FAIL"
    assert report.regression is not None and not report.regression.ok


def test_diff_budget_blocks_oversized_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    worker = _worker(
        tmp_path,
        "import pathlib, subprocess\n"
        "for i in range(25):\n"
        "    pathlib.Path(f'module_{i}.py').write_text('x = 1')\n"
        "subprocess.run(['git', 'add', '.'])\n"
        "subprocess.run(['git', 'commit', '-qm', 'wide'])\n",
    )
    report = run_external_agent(
        repo,
        [sys.executable, str(worker)],
        "add many modules",
        worktree_root=tmp_path / "wt-diff",
        diff_budget_required=True,
        run_tests_flag=False,
        run_review_flag=False,
    )
    assert report.diff_budget is not None
    assert report.diff_budget.gate == "FAIL"
    assert report.gate == "FAIL"


def test_parallel_gates_run_in_the_worker(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    worker = _worker(
        tmp_path,
        "import pathlib, subprocess\n"
        "pathlib.Path('app.py').write_text('x = 1')\n"
        "subprocess.run(['git', 'add', '.'])\n"
        "subprocess.run(['git', 'commit', '-qm', 'work'])\n",
    )
    report = run_external_agent(
        repo,
        [sys.executable, str(worker)],
        "add a small module",
        worktree_root=tmp_path / "wt-parallel",
        parallel_gates_flag=True,
        run_tests_flag=False,
        run_review_flag=False,
    )
    assert report.parallel is not None
    assert report.parallel.gate == "PASS"
    assert report.gate == "PASS"


def test_new_ecc_commands_are_registered_and_useful() -> None:
    runner = CliRunner()
    for name in ("critic", "escalate", "health", "schedule"):
        result = runner.invoke(app, ["forge", name, "--help"])
        assert result.exit_code == 0, (name, result.output)
    scheduled = runner.invoke(
        app,
        ["forge", "schedule", "--command", "python -m http.server", "--schedule", "*/5 * * * *"],
    )
    assert scheduled.exit_code == 0
    assert "generate-only" in scheduled.output
