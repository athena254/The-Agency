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


def _fake_config(provider, model, *, api_key=None):
    from pydantic import SecretStr

    from agency.llm.config import LLMConfig

    return LLMConfig(
        provider=provider,
        model=model,
        api_key=SecretStr(api_key) if api_key else None,
    )


def test_build_review_llm_prefers_google_serious_model(monkeypatch):
    """Google credentials drive the gate's serious model even when the task LLM
    uses a different backend; the echo fallback beneath must not be consulted."""
    import agency.llm.config as llm_config
    from agency.forge.reviewer import build_review_llm

    monkeypatch.setattr(
        llm_config,
        "load_named_provider",
        lambda name, **kw: _fake_config("google", "gemini-3.5-flash-lite", api_key="k"),
    )

    def _unexpected(*a, **kw):  # pragma: no cover - must not be reached
        raise AssertionError("load_config consulted despite Google credentials")

    monkeypatch.setattr(llm_config, "load_config", _unexpected)

    adapter = build_review_llm()
    assert adapter is not None
    assert adapter.model == "gemini-4-hydrogen"
    assert adapter.provider == "google"


def test_build_review_llm_falls_back_to_agency_llm(monkeypatch):
    """Without Google credentials the gate uses the configured Agency LLM
    unchanged (no Gemini override on a non-Google backend)."""
    import agency.llm.config as llm_config
    from agency.forge.reviewer import build_review_llm

    monkeypatch.setattr(
        llm_config,
        "load_named_provider",
        lambda name, **kw: _fake_config("google", "gemini-3.5-flash-lite"),
    )
    monkeypatch.setattr(
        llm_config,
        "load_config",
        lambda **kw: _fake_config("openai", "hy4-flash:free", api_key="k"),
    )

    adapter = build_review_llm()
    assert adapter is not None
    assert adapter.model == "hy4-flash:free"
    assert adapter.provider == "openai"


def test_build_review_llm_none_when_no_provider(monkeypatch):
    """No Google credentials and an echo task LLM mean no review LLM."""
    import agency.llm.config as llm_config
    from agency.forge.reviewer import build_review_llm

    monkeypatch.setattr(
        llm_config,
        "load_named_provider",
        lambda name, **kw: _fake_config("google", "gemini-3.5-flash-lite"),
    )
    monkeypatch.setattr(
        llm_config,
        "load_config",
        lambda **kw: _fake_config("echo", "echo"),
    )

    assert build_review_llm() is None
