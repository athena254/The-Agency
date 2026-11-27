"""Tests for the ECC gate-speed slice: parallel cheap gates and diff budgets."""

from __future__ import annotations

import threading
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agency.forge.diff_budget import (
    HARD_MAX_FILES,
    HARD_MAX_LINES,
    BudgetException,
    DiffBudgetReport,
    FileChange,
    check_diff_budget,
)
from agency.forge.parallel_gates import (
    ParallelGateReport,
    run_parallel_gates,
)


def _pass() -> object:
    return SimpleNamespace(gate="PASS")


def test_cheap_gates_run_concurrently_then_review() -> None:
    barrier = threading.Barrier(3, timeout=3)
    cheap_done: list[str] = []

    def cheap(name: str) -> Callable[[], object]:
        def run() -> object:
            barrier.wait()
            cheap_done.append(name)
            return SimpleNamespace(gate="PASS")

        return run

    def review() -> object:
        # Review is only submitted after every cheap gate has finished.
        assert len(cheap_done) == 3
        return "PASS"

    report = run_parallel_gates(
        {"inspection": cheap("inspection"), "security": cheap("security"), "tests": cheap("tests")},
        review=review,
    )

    assert isinstance(report, ParallelGateReport)
    assert report.gate == "PASS"
    assert [r.name for r in report.results] == ["inspection", "security", "tests"]
    assert all(r.gate == "PASS" for r in report.results)
    assert report.review_permitted is True
    assert report.review is not None and report.review.gate == "PASS"


def test_review_blocked_when_a_cheap_gate_fails() -> None:
    calls: list[str] = []

    def review() -> object:
        calls.append("review")
        return "PASS"

    report = run_parallel_gates(
        {"inspection": _pass, "security": lambda: "FAIL"},
        review=review,
    )
    assert report.gate == "FAIL"
    assert report.review_permitted is False
    assert report.review is None
    assert calls == []
    assert any("review_not_permitted" in c for c in report.caveats)


def test_skip_evidences_missing_blocks_review() -> None:
    report = run_parallel_gates({"tests": lambda: "SKIP"}, review=_pass)
    assert report.gate == "FAIL"
    assert report.review_permitted is False


def test_timeout_is_uncertain_not_success() -> None:
    blocker = threading.Event()

    def slow() -> object:
        blocker.wait(timeout=5)
        return "PASS"

    try:
        report = run_parallel_gates(
            {"inspection": slow, "security": _pass},
            timeout_s=0.05,
            review=_pass,
        )
    finally:
        blocker.set()

    slow_result = next(r for r in report.results if r.name == "inspection")
    assert slow_result.gate == "UNCERTAIN"
    assert "timeout" in slow_result.detail
    assert report.gate == "FAIL"
    assert report.review is None
    assert any("inspection" in c for c in report.caveats)


def test_raised_gate_exception_is_uncertain() -> None:
    def broken() -> object:
        msg = "secret-value-must-not-leak"
        raise RuntimeError(msg)

    report = run_parallel_gates({"security": broken}, review=_pass)
    result = report.results[0]
    assert result.gate == "UNCERTAIN"
    assert "RuntimeError" in result.detail
    assert "secret-value-must-not-leak" not in result.detail
    assert report.gate == "FAIL"


def test_worker_pool_is_bounded() -> None:
    lock = threading.Lock()
    active = 0
    peak = 0

    def gate() -> object:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        threading.Event().wait(0.03)
        with lock:
            active -= 1
        return "PASS"

    run_parallel_gates({name: gate for name in ("a", "b", "c", "d")}, max_workers=1)
    assert peak == 1


def test_empty_gates_rejected() -> None:
    with pytest.raises(ValueError):
        run_parallel_gates({})


def test_diff_budget_within_limits_passes() -> None:
    changes = [
        FileChange(path="a.py", added=5, deleted=2),
        FileChange(path="b.py", added=3, deleted=1),
    ]
    report = check_diff_budget(changes)
    assert isinstance(report, DiffBudgetReport)
    assert report.gate == "PASS"
    assert report.file_count == 2
    assert report.changed_lines == 11
    assert report.exceptions_applied == ()
    assert report.reasons == ()


def test_diff_budget_file_cap_denies() -> None:
    changes = [FileChange(path=f"f{i}.py", added=1, deleted=0) for i in range(3)]
    report = check_diff_budget(changes, max_files=2)
    assert report.gate == "FAIL"
    assert any(r.startswith("file_count_exceeded") for r in report.reasons)


def test_diff_budget_line_cap_denies() -> None:
    report = check_diff_budget(
        [FileChange(path="a.py", added=300, deleted=100)], max_changed_lines=350
    )
    assert report.gate == "FAIL"
    assert any(r.startswith("changed_lines_exceeded") for r in report.reasons)


def test_diff_budget_exception_is_explicit_and_audited() -> None:
    changes = [
        FileChange(path="migration.py", added=200, deleted=0),
        FileChange(path="main.py", added=1, deleted=0),
    ]
    waiver = BudgetException(
        path="migration.py",
        reason="generated schema migration",
        approved_by="human-reviewer",
    )
    report = check_diff_budget(changes, max_files=1, max_changed_lines=50, exceptions=[waiver])
    assert report.gate == "PASS"
    assert report.charged_files == 1
    assert report.charged_lines == 1
    assert report.exceptions_applied == ("migration.py",)
    assert any("exception" in c for c in report.caveats)


def test_diff_budget_exception_without_approval_denies() -> None:
    waiver = BudgetException(path="a.py", reason="   ", approved_by="   ")
    report = check_diff_budget([FileChange(path="a.py", added=1, deleted=0)], exceptions=[waiver])
    assert report.gate == "FAIL"
    assert any(r.startswith("invalid_exception") for r in report.reasons)


def test_diff_budget_duplicate_path_denies() -> None:
    report = check_diff_budget(
        [FileChange(path="a.py", added=1, deleted=0), FileChange(path="a.py", added=2, deleted=0)]
    )
    assert report.gate == "FAIL"
    assert any(r.startswith("duplicate_path") for r in report.reasons)


def test_diff_budget_unsafe_path_denies() -> None:
    report = check_diff_budget([FileChange(path="../outside.py", added=1, deleted=0)])
    assert report.gate == "FAIL"
    assert any(r.startswith("unsafe_path") for r in report.reasons)


def test_diff_budget_limits_clamped_to_ceiling() -> None:
    report = check_diff_budget(
        [FileChange(path="a.py", added=1, deleted=0)],
        max_files=HARD_MAX_FILES + 99,
        max_changed_lines=HARD_MAX_LINES + 999,
    )
    assert report.limits.max_files == HARD_MAX_FILES
    assert report.limits.max_changed_lines == HARD_MAX_LINES
    assert any("clamped" in c for c in report.caveats)


def test_diff_budget_empty_change_set_denies() -> None:
    report = check_diff_budget([])
    assert report.gate == "FAIL"
    assert any("empty_change_set" in r for r in report.reasons)


def test_diff_budget_report_is_immutable() -> None:
    report = check_diff_budget([FileChange(path="a.py", added=1, deleted=0)])
    with pytest.raises(ValidationError):
        report.gate = "FAIL"
