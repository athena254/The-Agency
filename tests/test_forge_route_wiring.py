"""Focused tests for Forge gap 1-3: route wiring, env discovery, runtime gate."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from uuid import uuid4

import pytest

from agency.forge import external_agent as ea
from agency.forge.external_agent import run_external_agent
from agency.forge.reviewer import ReviewReport
from agency.forge.routing import apply_route_model, resolve_route
from agency.forge.runtime_verify import RuntimeReport, verify_runtime


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "init"], check=True, capture_output=True
    )
    return repo


def _fake_agent(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Replace the spawned agent with a stub that edits one file in the worktree."""
    captured: dict[str, list[str]] = {}

    def fake(cmd_list: list[str], cwd: str, timeout_s: int) -> tuple[int, str, str]:
        captured["command"] = list(cmd_list)
        Path(cwd, "README.md").write_text("changed\n", encoding="utf-8")
        return (0, "", "")

    monkeypatch.setattr(ea, "_run_agent", fake)
    return captured


def _pass_review() -> ReviewReport:
    return ReviewReport(
        report_id=str(uuid4()),
        created_at=datetime.now(UTC),
        root="x",
        gate="PASS",
        score=5,
    )


class _FakeAdapter:
    provider = "fake-google"


# --- Gap 1: model applied automatically -------------------------------------


def test_apply_route_model_precedence() -> None:
    cmd, how = apply_route_model(["hermes", "chat", "--prompt", "{model}"], "m1")
    assert how == "placeholder"
    assert cmd == ["hermes", "chat", "--prompt", "m1"]

    for base in (["opencode", "run"], ["hermes", "chat"]):
        cmd, how = apply_route_model(list(base), "m1")
        assert how == "flag"
        assert cmd == [*base, "--model", "m1"]

    cmd, how = apply_route_model(["python", "agent.py"], "m1")
    assert (how, cmd) == ("unsupported", ["python", "agent.py"])
    cmd, how = apply_route_model(["python", "agent.py"], "")
    assert (how, cmd) == ("none", ["python", "agent.py"])

    # An explicit model in the template is never clobbered or duplicated.
    cmd, how = apply_route_model(["opencode", "run", "--model", "explicit"], "m1")
    assert (how, cmd) == ("already-set", ["opencode", "run", "--model", "explicit"])
    cmd, how = apply_route_model(["opencode", "run", "--model=explicit"], "m1")
    assert how == "already-set" and cmd == ["opencode", "run", "--model=explicit"]


def test_resolve_route_uses_name_then_text() -> None:
    assert resolve_route("risky", "").job_class == "risky"
    assert resolve_route("", "harden auth permissions").job_class == "risky"
    assert resolve_route("unknown", "fix typo").job_class == "trivial"


def test_worker_gets_model_flag_for_known_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    captured = _fake_agent(monkeypatch)
    report = run_external_agent(
        str(repo),
        ["opencode", "run"],
        "change readme",
        route="risky",
        model="test/model-x",
        run_review_flag=False,
        cleanup=True,
    )
    assert report.gate == "PASS"
    assert "--model" in captured["command"]
    assert "test/model-x" in captured["command"]
    assert report.model_applied == "test/model-x"
    assert report.model_mechanism == "flag"
    assert "--model" in report.command
    assert report.model == "test/model-x"  # requested model preserved separately


def test_route_model_used_when_no_explicit_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    _fake_agent(monkeypatch)
    report = run_external_agent(
        str(repo),
        ["opencode", "run"],
        "change readme",
        route="risky",
        run_review_flag=False,
        cleanup=True,
    )
    assert report.model_applied == resolve_route("risky").model
    assert report.model_applied and report.model_mechanism == "flag"


def test_worker_model_placeholder_still_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    captured = _fake_agent(monkeypatch)
    report = run_external_agent(
        str(repo),
        ["tool", "--model={model}"],
        "change readme",
        model="m2",
        run_review_flag=False,
        cleanup=True,
    )
    assert report.model_applied == "m2"
    assert report.model_mechanism == "placeholder"
    assert "--model=m2" in captured["command"]


def test_unknown_command_gets_no_model_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    captured = _fake_agent(monkeypatch)
    report = run_external_agent(
        str(repo),
        ["python", "agent.py"],
        "change readme",
        model="m3",
        run_review_flag=False,
        cleanup=True,
    )
    assert report.model_mechanism == "unsupported"
    assert report.model_applied == ""
    assert Path(captured["command"][0]).stem.lower() == "python"
    assert captured["command"][1] == "agent.py"
    assert captured["command"] == list(report.command)


# --- Gap 2: repo-local review config ----------------------------------------


def test_repo_env_discovered_and_provider_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    (repo / ".env").write_text(
        "FORGE_WIRING_KEY=wire-secret\nAGENCY_FORGE_REVIEW_PROVIDER=agency\n", encoding="utf-8"
    )
    monkeypatch.delenv("FORGE_WIRING_KEY", raising=False)
    monkeypatch.delenv("AGENCY_FORGE_REVIEW_PROVIDER", raising=False)
    _fake_agent(monkeypatch)
    monkeypatch.setattr(ea, "build_review_llm", lambda: _FakeAdapter())
    monkeypatch.setattr(ea, "run_review", lambda *a, **k: _pass_review())

    report = run_external_agent(str(repo), ["python", "agent"], "change readme", cleanup=True)

    assert report.gate == "PASS"
    assert report.review_config_source == ".env"
    assert os.environ["FORGE_WIRING_KEY"] == "wire-secret"
    assert report.review_provider == "fake-google"
    assert report.review_provider_mode == "agency"
    assert "wire-secret" not in report.model_dump_json()


def test_explicit_env_wins_over_repo_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo(tmp_path)
    (repo / ".env").write_text("FORGE_WIRING_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("FORGE_WIRING_KEY", "from-explicit")
    _fake_agent(monkeypatch)

    report = run_external_agent(
        str(repo),
        ["python", "agent"],
        "change readme",
        run_review_flag=False,
        cleanup=True,
    )

    assert os.environ["FORGE_WIRING_KEY"] == "from-explicit"
    assert report.review_config_source == "environment"


def test_no_env_file_reports_environment_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    _fake_agent(monkeypatch)
    report = run_external_agent(
        str(repo),
        ["python", "agent"],
        "change readme",
        run_review_flag=False,
        cleanup=True,
    )
    assert report.review_config_source == "environment"
    assert report.review_provider == "not-run"


# --- Gap 3: runtime gate on risky routes ------------------------------------


_VerifyCall = tuple[tuple[Path, list[str], list[str]], dict[str, object]]


def _record_verify(monkeypatch: pytest.MonkeyPatch, report: RuntimeReport) -> list[_VerifyCall]:
    calls: list[_VerifyCall] = []

    def fake(
        root: Path, command: list[str], endpoints: list[str], **kwargs: object
    ) -> RuntimeReport:
        calls.append(((root, command, endpoints), kwargs))
        return report

    monkeypatch.setattr(ea, "verify_runtime", fake)
    return calls


def test_risky_route_runs_runtime_and_fail_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    _fake_agent(monkeypatch)
    calls = _record_verify(
        monkeypatch,
        RuntimeReport(
            gate="FAIL",
            command=("python", "-m", "server"),
            endpoints=("http://example.test/health",),
            statuses=(("http://example.test/health", 503),),
            duration_s=0.1,
            note="bad health",
        ),
    )

    report = run_external_agent(
        str(repo),
        ["python", "agent"],
        "harden auth permissions",
        route="risky",
        verify_command="python -m server",
        verify_endpoints=["http://example.test/health"],
        run_review_flag=False,
        cleanup=True,
    )

    assert report.gate == "FAIL"
    assert report.runtime is not None and report.runtime.gate == "FAIL"
    assert ("http://example.test/health", 503) in report.runtime.statuses
    assert "runtime_gate_failed" in report.caveats
    assert "Runtime verification FAILED" in report.note
    assert len(calls) == 1
    root, command, endpoints = calls[0][0]
    assert str(root) == report.worktree_path
    assert command == ["python", "-m", "server"]
    assert endpoints == ["http://example.test/health"]


def test_runtime_pass_keeps_gate_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo(tmp_path)
    _fake_agent(monkeypatch)
    _record_verify(
        monkeypatch,
        RuntimeReport(
            gate="PASS",
            command=("python",),
            endpoints=("http://example.test/",),
            statuses=(("http://example.test/", 200),),
            duration_s=0.1,
        ),
    )
    report = run_external_agent(
        str(repo),
        ["python", "agent"],
        "risky deploy path",
        route="risky",
        verify_command=["python"],
        verify_endpoints=["http://example.test/"],
        run_review_flag=False,
        cleanup=True,
    )
    assert report.gate == "PASS"
    assert report.runtime is not None and report.runtime.gate == "PASS"
    assert "runtime_gate_failed" not in report.caveats


def test_verify_params_ignored_for_standard_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    _fake_agent(monkeypatch)
    calls = _record_verify(
        monkeypatch,
        RuntimeReport(gate="FAIL", command=(), endpoints=(), statuses=(), duration_s=0.0),
    )
    report = run_external_agent(
        str(repo),
        ["python", "agent"],
        "add footer",
        route="standard",
        verify_command="python -m server",
        verify_endpoints=["http://example.test/"],
        run_review_flag=False,
        cleanup=True,
    )
    assert report.gate == "PASS"
    assert report.runtime is None
    assert not calls


def test_risky_without_verify_params_skips_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    _fake_agent(monkeypatch)
    calls = _record_verify(
        monkeypatch,
        RuntimeReport(gate="FAIL", command=(), endpoints=(), statuses=(), duration_s=0.0),
    )
    report = run_external_agent(
        str(repo),
        ["python", "agent"],
        "harden auth",
        route="risky",
        run_review_flag=False,
        cleanup=True,
    )
    assert report.runtime is None
    assert "runtime_verification_skipped" in report.caveats
    assert not calls


def test_verify_timeout_validation(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    with pytest.raises(ValueError, match="verify_timeout_s"):
        run_external_agent(str(repo), ["python"], "t", verify_timeout_s=0, cleanup=True)


# --- verify_runtime itself with injected fakes (no network, no process) ------


def test_verify_runtime_with_injected_fakes(tmp_path: Path) -> None:
    class _Response:
        status = 200

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    class _Proc:
        def terminate(self) -> None:
            return None

        def wait(self, timeout: int = 5) -> None:
            return None

        def kill(self) -> None:
            return None

    seen: list[str] = []

    def fake_urlopen(url: str, timeout: int = 2) -> _Response:
        seen.append(url)
        return _Response()

    report = verify_runtime(
        tmp_path,
        ["python", "-m", "server"],
        ["http://example.test/"],
        urlopen=fake_urlopen,
        popen=lambda *a, **k: _Proc(),
        sleep=lambda _s: None,
    )
    assert report.gate == "PASS"
    assert report.statuses == (("http://example.test/", 200),)
    assert seen == ["http://example.test/"]


def test_verify_runtime_missing_inputs_fails_closed() -> None:
    report = verify_runtime(".", [], [])
    assert report.gate == "FAIL"
    assert report.note == "missing_command_or_endpoints"
