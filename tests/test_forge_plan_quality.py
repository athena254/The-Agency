"""Gap 4: plan quality signals are enforced for code jobs and made explicit
for legacy plans (never a silent pass)."""

from __future__ import annotations

from pathlib import Path

from agency.forge.planning import check_code_plan, check_plan

_LEGACY_PLAN = "# Plan\nGoal: x\nApproach: y\nFiles: z\nTests: run tests\nRisks: none\n"
_CODE_PLAN = (
    "# Plan\n"
    "Goal: add endpoint\n"
    "Approach: one seam, two adapters\n"
    "Files: `src/app.py`, `tests/`\n"
    "Tests: python -m pytest -q tests/\n"
    "Risks: regression in the resolve path\n"
)


def _write(tmp_path: Path, text: str) -> None:
    (tmp_path / "PLAN.md").write_text(text, encoding="utf-8")


def test_legacy_plan_passes_with_explicit_caveat(tmp_path: Path) -> None:
    _write(tmp_path, _LEGACY_PLAN)
    report = check_plan(tmp_path)
    assert report.gate == "PASS"
    assert report.findings == ()
    assert "lenient_plan" in report.caveats
    assert "no_file_allowlist" in report.caveats
    assert "no_named_test_command" in report.caveats
    assert "no_risks_declared" in report.caveats
    assert report.strict is False
    assert report.test_command == ""
    assert report.declared_risks == "none"


def test_strict_code_job_requires_quality_signals(tmp_path: Path) -> None:
    _write(tmp_path, _LEGACY_PLAN)
    report = check_code_plan(tmp_path)
    assert report.gate == "FAIL"
    assert "no_file_allowlist" in report.findings
    assert "no_named_test_command" in report.findings
    assert "no_risks_declared" in report.findings
    assert "lenient_plan" not in report.caveats
    assert report.strict is True


def test_strict_code_plan_passes_with_quality_signals(tmp_path: Path) -> None:
    _write(tmp_path, _CODE_PLAN)
    report = check_plan(tmp_path, strict=True)
    assert report.gate == "PASS"
    assert report.findings == ()
    assert report.allowed_files == ("src/app.py", "tests")
    assert "python -m pytest" in report.test_command
    assert report.declared_risks == "regression in the resolve path"
    assert "lenient_plan" not in report.caveats
    assert "no_file_allowlist" not in report.caveats


def test_job_class_turns_on_strict_mode(tmp_path: Path) -> None:
    _write(tmp_path, _LEGACY_PLAN)
    assert check_plan(tmp_path, job_class="trivial").gate == "PASS"
    assert check_plan(tmp_path, job_class="risky").gate == "FAIL"
    assert check_plan(tmp_path, job_class="risky").strict is True


def test_partial_quality_gaps_are_individual_findings(tmp_path: Path) -> None:
    # Named test command present, but no allow-list and "Risks: N/A".
    _write(tmp_path, "Goal: x\nApproach: y\nFiles: app.py\nTests: pytest -q\nRisks: N/A\n")
    report = check_code_plan(tmp_path)
    assert report.gate == "FAIL"
    assert "no_named_test_command" not in report.findings
    assert "no_file_allowlist" in report.findings
    assert "no_risks_declared" in report.findings


def test_missing_sections_still_fail_in_both_modes(tmp_path: Path) -> None:
    _write(tmp_path, "Goal: x\nApproach: y\nFiles: `app.py`\n")
    for strict in (False, True):
        report = check_plan(tmp_path, strict=strict)
        assert report.gate == "FAIL"
        assert "missing_sections" in report.findings
        assert set(report.missing_sections) == {"Tests", "Risks"}
