"""Tests for the Forge external-agent runner (Windows/CI hardening)."""

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agency.forge import external_agent as ea
from agency.forge.external_agent import (
    ExternalAgentReport,
    emit_report_json,
    render_report_json,
    run_external_agent,
    write_report_json,
)


def _git_repo(tmp_path: Path, extra_files: dict[str, str] | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("hi\n")
    for name, content in (extra_files or {}).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True
    )
    return repo


def _write_agent(tmp_path: Path, body: str, name: str = "agent.py") -> Path:
    agent = tmp_path / name
    agent.write_text(body)
    return agent


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=False, capture_output=True, text=True
    )


def _make_path_shim(bindir: Path, name: str, script: Path) -> str:
    """Create a PATH-resolvable agent shim and return the bare command name."""
    bindir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        shim = bindir / f"{name}.cmd"
        shim.write_text("@echo off" + os.linesep + f'"{sys.executable}" "{script}" %*' + os.linesep)
    else:
        shim = bindir / name
        shim.write_text(f"#!{sys.executable}\n" + script.read_text())
        shim.chmod(0o755)
    return name


def test_external_agent_applies_and_gates_pass(tmp_path):
    repo = _git_repo(tmp_path)
    agent = tmp_path / "agent.py"
    agent.write_text('from pathlib import Path\nPath("README.md").write_text("hi\\nchanged\\n")\n')
    report = run_external_agent(
        str(repo),
        [sys.executable, str(agent)],
        "change readme",
        cleanup=True,
    )
    assert report.gate == "PASS"
    assert report.changed_paths == ("README.md",)
    assert report.exit_code == 0
    assert report.inspection.gate == "PASS"
    assert report.security.gate == "PASS"


def test_external_agent_failure_on_nonzero_exit(tmp_path):
    repo = _git_repo(tmp_path)
    agent = tmp_path / "agent.py"
    agent.write_text("import sys\nsys.exit(1)\n")
    report = run_external_agent(
        str(repo),
        [sys.executable, str(agent)],
        "fail",
        cleanup=True,
    )
    assert report.gate == "FAIL"
    assert report.exit_code == 1


def test_external_agent_no_changes_fails(tmp_path):
    repo = _git_repo(tmp_path)
    agent = tmp_path / "agent.py"
    agent.write_text("print('noop')\n")
    report = run_external_agent(
        str(repo),
        [sys.executable, str(agent)],
        "noop",
        cleanup=True,
    )
    assert report.gate == "FAIL"
    assert report.changed_paths == ()


def _readme_agent(tmp_path: Path) -> Path:
    return _write_agent(
        tmp_path,
        """from pathlib import Path
Path("README.md").write_text("hi" + chr(10) + "changed" + chr(10))
""",
    )


def test_bare_executable_name_resolved_from_path(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    agent = _readme_agent(tmp_path)
    bindir = tmp_path / "bin"
    name = _make_path_shim(bindir, "opencode", agent)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))

    report = run_external_agent(str(repo), [name, "run", "--auto"], "change readme", cleanup=True)

    assert report.gate == "PASS"
    assert report.exit_code == 0
    assert report.changed_paths == ("README.md",)
    assert Path(report.command[0]).is_absolute()
    assert Path(report.command[0]).name.lower().startswith("opencode")


@pytest.mark.skipif(os.name != "nt", reason="Windows .CMD shim fallback")
def test_cmd_shim_fallback_when_direct_launch_fails(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    agent = _readme_agent(tmp_path)
    bindir = tmp_path / "bin"
    name = _make_path_shim(bindir, "opencode", agent)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))

    real_spawn = ea._spawn
    calls: list[list[str]] = []

    def flaky_spawn(cmd_list, cwd):
        calls.append(list(cmd_list))
        if len(calls) == 1:
            raise OSError("simulated WinError 193")
        return real_spawn(cmd_list, cwd)

    monkeypatch.setattr(ea, "_spawn", flaky_spawn)

    report = run_external_agent(str(repo), [name, "run"], "change readme", cleanup=True)

    assert report.gate == "PASS"
    assert report.exit_code == 0
    assert len(calls) == 2
    assert Path(calls[0][0]).suffix.lower() == ".cmd"
    assert calls[1][:3] == ["cmd", "/c", "call"]
    assert calls[1][3] == calls[0][0]


def test_agent_timeout_kills_process_tree(tmp_path):
    repo = _git_repo(tmp_path)
    agent = _write_agent(
        tmp_path,
        """import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
time.sleep(60)
""",
    )

    start = time.monotonic()
    report = run_external_agent(
        str(repo), [sys.executable, str(agent)], "hang", timeout_s=1, cleanup=True
    )
    elapsed = time.monotonic() - start

    assert report.exit_code == 124
    assert "[timeout]" in report.stderr
    assert report.gate == "FAIL"
    assert not Path(report.worktree_path).exists()
    assert elapsed < 20, f"runner waited {elapsed:.1f}s; process tree was not killed"


def test_cleanup_removes_worktree_and_created_branch(tmp_path):
    repo = _git_repo(tmp_path)
    agent = _readme_agent(tmp_path)

    report = run_external_agent(
        str(repo),
        [sys.executable, str(agent)],
        "change readme",
        branch="forge-test-cleanup",
        cleanup=True,
    )

    assert report.gate == "PASS"
    assert not Path(report.worktree_path).exists()
    assert _git(repo, "rev-parse", "--verify", "--quiet", "forge-test-cleanup").returncode != 0


def test_cleanup_preserves_preexisting_branch(tmp_path):
    repo = _git_repo(tmp_path)
    assert _git(repo, "branch", "forge-test-keep").returncode == 0
    agent = _readme_agent(tmp_path)

    report = run_external_agent(
        str(repo),
        [sys.executable, str(agent)],
        "change readme",
        branch="forge-test-keep",
        cleanup=True,
    )

    assert report.gate == "PASS"
    assert not Path(report.worktree_path).exists()
    assert _git(repo, "rev-parse", "--verify", "--quiet", "forge-test-keep").returncode == 0


def test_protected_tracked_file_change_fails_gate(tmp_path):
    repo = _git_repo(tmp_path, extra_files={"uv.lock": "version = 1" + chr(10)})
    agent = _write_agent(
        tmp_path,
        """from pathlib import Path
Path("uv.lock").write_text("version = 2" + chr(10))
""",
    )

    report = run_external_agent(str(repo), [sys.executable, str(agent)], "touch lock", cleanup=True)

    assert report.gate == "FAIL"
    assert report.protected_paths == ("uv.lock",)
    assert "protected_file_modified" in report.caveats
    # The change itself is benign; the guard is what failed the gate.
    assert report.inspection.gate == "PASS"
    assert report.security.gate == "PASS"


def test_protected_paths_parameter_overrides_default(tmp_path):
    repo = _git_repo(tmp_path, extra_files={"uv.lock": "version = 1" + chr(10)})
    agent = _readme_agent(tmp_path)

    report = run_external_agent(
        str(repo),
        [sys.executable, str(agent)],
        "change readme",
        cleanup=True,
        protected_paths=("README.md",),
    )

    assert report.gate == "FAIL"
    assert report.protected_paths == ("README.md",)


def test_new_untracked_protected_file_fails_gate(tmp_path):
    repo = _git_repo(tmp_path)
    agent = _write_agent(
        tmp_path,
        """from pathlib import Path
Path("README.md").write_text("hi" + chr(10) + "changed" + chr(10))
Path("uv.lock").write_text("version = 1" + chr(10))
""",
    )

    report = run_external_agent(
        str(repo), [sys.executable, str(agent)], "create lock", cleanup=True
    )

    assert report.gate == "FAIL"
    assert report.protected_paths == ("uv.lock",)
    assert "uv.lock" in report.changed_paths
    assert "protected_file_modified" in report.caveats


def test_venv_creation_is_ignored(tmp_path):
    repo = _git_repo(tmp_path)
    agent = _write_agent(
        tmp_path,
        """from pathlib import Path
Path("README.md").write_text("hi" + chr(10) + "changed" + chr(10))
Path(".venv").mkdir(exist_ok=True)
Path(".venv/pyvenv.cfg").write_text("x" + chr(10))
""",
    )

    report = run_external_agent(str(repo), [sys.executable, str(agent)], "make venv", cleanup=True)

    assert report.gate == "PASS"
    assert report.changed_paths == ("README.md",)


def test_main_repo_modification_fails_gate(tmp_path):
    repo = _git_repo(tmp_path)
    agent = _write_agent(
        tmp_path,
        f"""from pathlib import Path
Path({str(repo / "README.md")!r}).write_text("main repo touched" + chr(10))
""",
    )

    report = run_external_agent(str(repo), [sys.executable, str(agent)], "touch main", cleanup=True)

    assert report.gate == "FAIL"
    assert report.main_repo_changes == ("README.md",)
    assert "main_repo_modified" in report.caveats


def test_preexisting_main_repo_dirt_is_not_flagged(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "README.md").write_text("dirty before run" + chr(10))
    agent = _readme_agent(tmp_path)

    report = run_external_agent(
        str(repo), [sys.executable, str(agent)], "change readme", cleanup=True
    )

    assert report.gate == "PASS"
    assert report.changed_paths == ("README.md",)
    assert report.main_repo_changes == ()


def test_render_report_json_is_not_wrapped(tmp_path, monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    repo = _git_repo(tmp_path)
    agent = _write_agent(tmp_path, "print('x' * 500)" + chr(10))

    report = run_external_agent(str(repo), [sys.executable, str(agent)], "print", cleanup=True)

    compact = render_report_json(report)
    assert chr(10) not in compact
    parsed = json.loads(compact)
    assert parsed["report_id"] == report.report_id
    assert "x" * 500 in parsed["stdout"]

    pretty = render_report_json(report, indent=2)
    assert json.loads(pretty)["report_id"] == report.report_id


def test_write_report_json_round_trip(tmp_path):
    repo = _git_repo(tmp_path)
    agent = _readme_agent(tmp_path)
    report = run_external_agent(
        str(repo), [sys.executable, str(agent)], "change readme", cleanup=True
    )

    path = tmp_path / "artifacts" / "report.json"
    write_report_json(report, path)
    assert path.is_file()
    assert ExternalAgentReport.model_validate_json(path.read_text(encoding="utf-8")) == report

    # Overwriting an existing artifact is allowed (atomic replace).
    write_report_json(report, path)
    assert ExternalAgentReport.model_validate_json(path.read_text(encoding="utf-8")) == report


def test_emit_report_json_to_stream(tmp_path):
    repo = _git_repo(tmp_path)
    agent = _write_agent(tmp_path, "print('ok')" + chr(10))
    report = run_external_agent(str(repo), [sys.executable, str(agent)], "print", cleanup=True)

    buffer = io.StringIO()
    emit_report_json(report, stream=buffer)

    text = buffer.getvalue()
    assert text.endswith(chr(10))
    assert json.loads(text)["report_id"] == report.report_id


def test_invalid_timeouts_rejected(tmp_path):
    repo = _git_repo(tmp_path)
    cmd = [sys.executable, "-c", "pass"]
    with pytest.raises(ValueError, match="timeout_s"):
        run_external_agent(str(repo), cmd, "t", timeout_s=0)
    with pytest.raises(ValueError, match="timeout_s"):
        run_external_agent(str(repo), cmd, "t", timeout_s=ea.MAX_AGENT_TIMEOUT_S + 1)
    with pytest.raises(ValueError, match="test_timeout_s"):
        run_external_agent(str(repo), cmd, "t", test_timeout_s=0)


def test_missing_executable_reports_launch_failure(tmp_path):
    repo = _git_repo(tmp_path)
    report = run_external_agent(str(repo), ["definitely-not-a-real-agent-xyz"], "t", cleanup=True)
    assert report.exit_code == 127
    assert "failed to launch agent" in report.stderr
    assert report.gate == "FAIL"


def test_launch_value_error_reports_127(tmp_path):
    repo = _git_repo(tmp_path)
    report = run_external_agent(str(repo), [sys.executable, "-c", "pass\x00bad"], "t", cleanup=True)
    assert report.exit_code == 127
    assert "failed to launch agent" in report.stderr
    assert report.gate == "FAIL"


def test_isolation_violation_untracked_file_in_original_checkout_fails(tmp_path):
    """An agent that creates a NEW file in the original checkout fails the gate.

    The pre-existing ``_tracked_changes`` check ignores untracked files, so this
    is the regression test for the shared ``snapshot_changes`` isolation guard.
    """
    repo = _git_repo(tmp_path)
    agent = _write_agent(
        tmp_path,
        f"""from pathlib import Path
Path({str(repo / "ESCAPED.txt")!r}).write_text("escaped" + chr(10))
Path("README.md").write_text("hi" + chr(10) + "changed" + chr(10))
""",
    )

    report = run_external_agent(str(repo), [sys.executable, str(agent)], "escape", cleanup=True)

    assert report.gate == "FAIL"
    assert report.isolation_violations == ("ESCAPED.txt",)
    assert "ESCAPED.txt" in report.note
    assert "left its isolated worktree" in report.note
    assert "isolation_violation" in report.caveats
    # The worktree-only edit is still visible to Forge's normal inspection.
    assert report.changed_paths == ("README.md",)
    assert report.inspection.gate == "PASS"


def test_isolation_violation_tracked_edit_in_original_checkout_names_path(tmp_path):
    repo = _git_repo(tmp_path)
    agent = _write_agent(
        tmp_path,
        f"""from pathlib import Path
Path({str(repo / "README.md")!r}).write_text("main repo touched" + chr(10))
Path("README.md").write_text("hi" + chr(10) + "changed" + chr(10))
""",
    )

    report = run_external_agent(str(repo), [sys.executable, str(agent)], "escape", cleanup=True)

    assert report.gate == "FAIL"
    assert report.isolation_violations == ("README.md",)
    assert "README.md" in report.note


def test_confined_agent_has_no_isolation_violations(tmp_path):
    repo = _git_repo(tmp_path)
    agent = _readme_agent(tmp_path)

    report = run_external_agent(str(repo), [sys.executable, str(agent)], "confine", cleanup=True)

    assert report.gate == "PASS"
    assert report.isolation_violations == ()


def test_failure_detail_keeps_bounded_stderr_tail(tmp_path):
    repo = _git_repo(tmp_path)
    marker = "PROVIDER_ERROR_401_UNAUTHORIZED"
    agent = _write_agent(
        tmp_path,
        f"""import sys
sys.stderr.write("x" * 5000 + chr(10))
sys.stderr.write({marker!r} + chr(10))
sys.exit(3)
""",
    )

    report = run_external_agent(str(repo), [sys.executable, str(agent)], "fail", cleanup=True)

    assert report.gate == "FAIL"
    assert report.failure_detail.endswith(marker)
    assert len(report.failure_detail) <= ea.MAX_FAILURE_DETAIL_CHARS + len("...[truncated]...") + 1


def test_failure_detail_falls_back_to_stdout(tmp_path):
    repo = _git_repo(tmp_path)
    agent = _write_agent(
        tmp_path,
        """import sys
print("provider stdout: rate limited")
sys.exit(1)
""",
    )

    report = run_external_agent(str(repo), [sys.executable, str(agent)], "fail", cleanup=True)

    assert report.gate == "FAIL"
    assert "provider stdout: rate limited" in report.failure_detail


def test_failure_detail_redacts_secrets(tmp_path):
    repo = _git_repo(tmp_path)
    agent = _write_agent(
        tmp_path,
        """import sys
sys.stderr.write("auth failed for sk-abcdefghijklmnop123456 and API_KEY=hunter2secret" + chr(10))
sys.exit(1)
""",
    )

    report = run_external_agent(str(repo), [sys.executable, str(agent)], "fail", cleanup=True)

    assert report.gate == "FAIL"
    assert "sk-abcdefghijklmnop123456" not in report.failure_detail
    assert "hunter2secret" not in report.failure_detail
    assert "[redacted]" in report.failure_detail
