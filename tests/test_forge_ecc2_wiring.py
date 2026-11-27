"""Parent wiring tests for ECC wave 2."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from agency.cli.main import app
from agency.forge.external_agent import run_external_agent
from agency.forge.runtime_specs import spec_for


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
    (repo / "README.md").write_text("wave2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "init"], check=True, capture_output=True
    )
    return repo


def _worker(tmp_path: Path) -> Path:
    script = tmp_path / "worker.py"
    script.write_text(
        "import pathlib, subprocess\n"
        "lines = ['Goal: x', 'Approach: y', 'Files: `app.py`, `PLAN.md`', "
        "'Tests: pytest -q', 'Risks: low']\n"
        "pathlib.Path('PLAN.md').write_text('\\n'.join(lines))\n"
        "pathlib.Path('app.py').write_text('x = 1')\n"
        "subprocess.run(['git', 'add', '.'])\n"
        "subprocess.run(['git', 'commit', '-qm', 'work'])\n",
        encoding="utf-8",
    )
    return script


def test_runtime_specs_exist_and_fail_closed() -> None:
    node = spec_for("node")
    assert node.verify_command and node.endpoints
    assert spec_for("unknown-product").verify_command == ()


def test_risky_review_runs_automatically(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    gold = tmp_path / "gold.json"
    gold.write_text(
        json.dumps(
            {
                "set_id": "gold",
                "owner": "human-lead",
                "version": 1,
                "tasks": [{"task_id": "t1", "prompt": "ping", "expected": "OK"}],
            }
        ),
        encoding="utf-8",
    )
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"ping": "OK"}), encoding="utf-8")
    report = run_external_agent(
        repo,
        [sys.executable, str(_worker(tmp_path))],
        "harden auth permissions",
        worktree_root=tmp_path / "wt-risky",
        plan_required=True,
        route="risky",
        run_tests_flag=False,
        run_review_flag=False,
        risky_critics=("APPROVE", "APPROVE"),
        risky_gold_file=str(gold),
        risky_results_file=str(results),
    )
    assert report.risky_review is not None
    assert report.risky_review.allowed is True
    assert report.gate == "PASS"


def test_ship_refuses_when_human_gates_are_not_attested(tmp_path: Path) -> None:
    from agency.forge.ship import ship_branch

    repo = _repo(tmp_path)
    report = ship_branch(
        repo,
        "main",
        pipeline_gate="PASS",
        smoke_gate="PASS",
        approved=True,
        changed_paths=("src/auth/login.py",),
        approvals=(),
        repo_root=repo,
    )
    assert report.gate == "FAIL"
    assert any(item.startswith("human_gate_missing:") for item in report.blocked_reasons)


def test_promotion_cli_requires_a_named_human(tmp_path: Path) -> None:
    ledger = tmp_path / "promotions.jsonl"
    runner = CliRunner()
    refused = runner.invoke(
        app,
        [
            "forge",
            "promote",
            "--apprentice",
            "scout",
            "--approver",
            "auto",
            "--ledger",
            str(ledger),
        ],
    )
    assert "promotion refused" in refused.output
    accepted = runner.invoke(
        app,
        [
            "forge",
            "promote",
            "--apprentice",
            "scout",
            "--approver",
            "human-lead",
            "--ledger",
            str(ledger),
        ],
    )
    assert "promoted scout" in accepted.output


def test_learn_uses_the_guarded_bridge(tmp_path: Path) -> None:
    source = tmp_path / "ci.json"
    source.write_text(
        json.dumps([{"task_id": "t-1", "gate": "FAILED", "source": "ci", "detail": "one failed"}]),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app,
        ["forge", "learn", "--input", str(source), "--approver", "human-lead"],
    )
    assert result.exit_code == 0, result.output
    assert "bridge: PASS approved=True" in result.output
    assert "PROPOSAL" in result.output


def test_load_and_live_view_commands(tmp_path: Path) -> None:
    runner = CliRunner()
    load = runner.invoke(app, ["forge", "load", "--jobs", "5"])
    assert load.exit_code == 0, load.output
    assert "backpressure" in load.output.lower()
    jobs = runner.invoke(app, ["forge", "jobs", "--db", str(tmp_path / "jobs.db"), "--view"])
    assert jobs.exit_code == 0, jobs.output
    assert "Jobs:" in jobs.output
