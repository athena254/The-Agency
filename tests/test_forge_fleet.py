"""Hermetic tests for the fleet runner and the patient review retry."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agency.forge import apprentice as apprentice_module
from agency.forge.external_agent import ExternalAgentReport
from agency.forge.fleet import FleetRunner, is_infrastructure_failure
from agency.forge.inspection import InspectionReport
from agency.forge.reviewer import REVIEW_MAX_PROVIDER_RETRIES, REVIEW_RETRY_DELAY_S, run_review
from agency.forge.security_scan import SecurityReport


@pytest.fixture(autouse=True)
def _fast_review_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agency.forge.reviewer.REVIEW_RETRY_DELAY_S", 0.0)


def _report(
    gate: str = "PASS",
    *,
    failure: str = "",
    command: tuple[str, ...] = ("apprentice", "hy4-flash:free"),
) -> ExternalAgentReport:
    now = datetime.now(UTC)
    root = "D:/fake/worktree"
    return ExternalAgentReport(
        report_id=str(uuid4()),
        created_at=now,
        worktree_path=root,
        branch="fleet-test",
        task="t",
        command=command,
        exit_code=0 if gate == "PASS" else 1,
        changed_paths=("src/x.py",),
        failure_detail=failure,
        inspection=InspectionReport(report_id=str(uuid4()), created_at=now, root=root, gate="PASS"),
        security=SecurityReport(
            report_id=str(uuid4()),
            created_at=now,
            root=root,
            gate="PASS",
            scanned_files=1,
            skipped_files=0,
        ),
        gate=gate,  # type: ignore[arg-type]
    )


def _runner(name: str, outcome: Exception | ExternalAgentReport, calls: list[str]):
    def runner(repo, command, task, **kwargs):
        calls.append(name)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return runner


def test_infrastructure_failure_detection() -> None:
    infra = _report("FAIL", failure="app.py: LLM call failed: HTTPStatusError")
    verdict = _report("FAIL", failure="review findings: src/x.py:3 LLM901 (high)")
    assert is_infrastructure_failure(infra) is True
    assert is_infrastructure_failure(verdict) is False


def test_fleet_failover_on_infrastructure_failure() -> None:
    calls: list[str] = []
    down = _runner(
        "apprentice",
        _report("FAIL", failure="app.py: LLM call failed: HTTPStatusError"),
        calls,
    )
    striker = _runner("opencode", _report("PASS", command=("opencode", "run")), calls)
    fleet = FleetRunner([("apprentice", down), ("opencode", striker)])
    report = fleet("D:/fake/repo", ["unused"], "task")
    assert report.gate == "PASS"
    assert report.command == ("opencode", "run")
    assert calls == ["apprentice", "opencode"]


def test_fleet_does_not_fail_over_on_genuine_verdicts() -> None:
    calls: list[str] = []
    strict = _runner("apprentice", _report("FAIL", failure="review findings: src/x.py:3 L901 (high)"), calls)
    never = _runner("opencode", _report("PASS"), calls)
    fleet = FleetRunner([("apprentice", strict), ("opencode", never)])
    report = fleet("D:/fake/repo", ["unused"], "task")
    assert report.gate == "FAIL"
    assert calls == ["apprentice"]  # the second worker never burned


def test_fleet_returns_last_report_when_all_fail_on_infrastructure() -> None:
    calls: list[str] = []
    fleet = FleetRunner(
        [
            ("a", _runner("a", _report("FAIL", failure="503 service unavailable"), calls)),
            ("b", _runner("b", _report("FAIL", failure="timeout waiting for provider"), calls)),
        ]
    )
    report = fleet("D:/fake/repo", ["unused"], "task")
    assert report.gate == "FAIL"
    assert calls == ["a", "b"]
    assert "timeout" in report.failure_detail


def test_fleet_all_runners_raise() -> None:
    calls: list[str] = []
    fleet = FleetRunner(
        [
            ("a", _runner("a", RuntimeError("boom"), calls)),
            ("b", _runner("b", RuntimeError("bam"), calls)),
        ]
    )
    with pytest.raises(RuntimeError, match="every fleet runner raised"):
        fleet("D:/fake/repo", ["unused"], "task")


def test_fleet_needs_runners() -> None:
    with pytest.raises(ValueError, match="at least one runner"):
        FleetRunner([])


# --------------------------------------------------------------------------- #
# patient review retry: a down judge waits instead of burning builder attempts
# --------------------------------------------------------------------------- #
class _FlakyLLM:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def generate(self, prompt: str, context: object = None) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("503 burst")
        return (
            '{"score": 5, "findings": [], "summary": "clean after recovery"}'
        )


def test_review_retries_through_provider_bursts(tmp_path, monkeypatch) -> None:
    from pathlib import Path as _Path
    import subprocess as _sp

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["git", "init", "-b", "main", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        ["git", "-C", str(repo), "config", "user.name", "t"],
    ):
        _sp.run(args, check=True, capture_output=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _sp.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    _sp.run(["git", "-C", str(repo), "commit", "-m", "i"], check=True, capture_output=True)

    stub = _FlakyLLM(failures=REVIEW_MAX_PROVIDER_RETRIES)  # recovers on the last try
    report = run_review(repo, ["a.py"], use_llm=True, llm=stub)
    assert report.gate == "PASS"
    assert stub.calls == REVIEW_MAX_PROVIDER_RETRIES + 1
    assert "heuristic_review" not in report.caveats


def test_review_never_retries_refusals(tmp_path, monkeypatch) -> None:
    from pathlib import Path as _Path
    import subprocess as _sp

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["git", "init", "-b", "main", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        ["git", "-C", str(repo), "config", "user.name", "t"],
    ):
        _sp.run(args, check=True, capture_output=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _sp.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    _sp.run(["git", "-C", str(repo), "commit", "-m", "i"], check=True, capture_output=True)

    stub = _FlakyLLM(failures=0)

    async def refuse(prompt: str, context: object = None) -> str:
        stub.calls += 1
        return "I cannot comply with this request."

    stub.generate = refuse  # type: ignore[method-assign]
    report = run_review(repo, ["a.py"], use_llm=True, llm=stub)
    assert report.gate == "FAIL"  # fail-closed
    assert stub.calls == 1  # refusals are never retried


# --------------------------------------------------------------------------- #
# cross-family fallback judge: when the primary reviewer's provider is down,
# an independent judge (hermes CLI) may score the change instead
# --------------------------------------------------------------------------- #
def test_review_falls_back_to_independent_judge(tmp_path, monkeypatch) -> None:
    import subprocess as _sp

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["git", "init", "-b", "main", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        ["git", "-C", str(repo), "config", "user.name", "t"],
    ):
        _sp.run(args, check=True, capture_output=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _sp.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    _sp.run(["git", "-C", str(repo), "commit", "-m", "i"], check=True, capture_output=True)

    class _DownForever:
        async def generate(self, prompt: str, context: object = None) -> str:
            raise RuntimeError("503 burst")

    class _HermesJudgeStub:
        calls = 0

        async def generate(self, prompt: str, context: object = None) -> str:
            _HermesJudgeStub.calls += 1
            assert "SERVICE CONTRACT" not in prompt  # it judges the real prompt
            return '{"score": 4, "findings": [], "summary": "independent judge: clean"}'

    report = run_review(repo, ["a.py"], use_llm=True, llm=_DownForever(), fallback_llm=_HermesJudgeStub())
    assert report.gate == "PASS"
    assert _HermesJudgeStub.calls == 1
    assert "review_fallback_judge" in report.caveats
    assert "heuristic_review" not in report.caveats


def test_review_fallback_failure_still_fails_closed(tmp_path, monkeypatch) -> None:
    import subprocess as _sp

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["git", "init", "-b", "main", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        ["git", "-C", str(repo), "config", "user.name", "t"],
    ):
        _sp.run(args, check=True, capture_output=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _sp.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    _sp.run(["git", "-C", str(repo), "commit", "-m", "i"], check=True, capture_output=True)

    class _AllDown:
        async def generate(self, prompt: str, context: object = None) -> str:
            raise RuntimeError("503 burst")

    report = run_review(repo, ["a.py"], use_llm=True, llm=_AllDown(), fallback_llm=_AllDown())
    assert report.gate == "FAIL"
    assert "review_fallback_judge" not in report.caveats
