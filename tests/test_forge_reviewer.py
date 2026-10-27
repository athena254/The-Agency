"""Tests for Forge Phase 5 AI reviewer loop."""

from __future__ import annotations

from pathlib import Path

from agency.forge import ReviewStore, run_review


def _write(root: Path, name: str, data: str) -> str:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data, encoding="utf-8")
    return name


def test_review_pass_clean(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    report = run_review(tmp_path, ["ok.py"])
    assert report.gate == "PASS"
    assert report.score >= 4
    assert "not_a_human_review" in report.caveats
    assert report.rounds <= 5


def test_review_fail_on_eval(tmp_path: Path):
    _write(tmp_path, "bad.py", "result = eval(user_input)\n")
    report = run_review(tmp_path, ["bad.py"])
    assert report.gate == "FAIL"
    assert any(f.severity == "high" for f in report.findings) or report.score < 4


def test_review_with_tests(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    (tmp_path / "test_ok.py").write_text(
        "def test_one():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    report = run_review(tmp_path, ["ok.py"], test_paths=["test_ok.py"], run_tests_flag=True)
    assert report.gate == "PASS"
    assert "tests_not_run" not in report.caveats


def test_review_persistence(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    report = run_review(tmp_path, ["ok.py"])
    db = tmp_path / "r.db"
    store = ReviewStore(db)
    store.save(report)
    store.close()
    reopened = ReviewStore(db)
    try:
        loaded = reopened.load(report.report_id)
    finally:
        reopened.close()
    assert loaded is not None
    assert loaded.gate == report.gate


def test_max_rounds_bound(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    report = run_review(tmp_path, ["ok.py"], max_rounds=2)
    assert report.max_rounds == 2
    assert report.rounds <= 2


class _FakeLLM:
    async def generate(self, prompt, context=None):
        return '{"score": 5, "findings": [], "summary": "clean"}'


def test_llm_review_path(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    from agency.forge.reviewer import run_review

    report = run_review(tmp_path, ["ok.py"], use_llm=True, llm=_FakeLLM())
    assert report.gate == "PASS"
    assert report.score == 5
    assert "heuristic_review" not in report.caveats
