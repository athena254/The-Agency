"""Hermetic tests for the LLM-backed Forge review gate.

No test in this module makes a network call: all provider configuration is
blanked by default and LLM responses are supplied by in-process stubs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agency.forge import reviewer
from agency.forge.external_agent import run_external_agent
from agency.forge.pipeline import run_pipeline
from agency.llm.adapter import LLMAdapter

_CLEAN_JSON = (
    '{"score": 5, "findings": [{"path": "ok.py", "line": 1, "rule_id": "LLM004", '
    '"severity": "low", "message": "SENSITIVE_RESPONSE_MARKER", "resolved": true}], '
    '"summary": "clean"}'
)


_LLM_ENV_PREFIXES = (
    "LLM_",
    "AGENCY_LLM_",
    "OPENAI_",
    "AGENCY_OPENAI_",
    "ANTHROPIC_",
    "AGENCY_ANTHROPIC_",
    "OPENROUTER_",
    "AGENCY_OPENROUTER_",
    "OLLAMA_",
    "AGENCY_OLLAMA_",
    "INCEPTION",
    "AGENCY_INCEPTION",
    "MERCURY",
    "AGENCY_MERCURY",
    "LOCAL_LLM_",
    "AGENCY_LOCAL_LLM_",
)


@pytest.fixture(autouse=True)
def _blank_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blank every credential/provider spelling so .env cannot leak in.

    Empty values (not deletion) are required: the config loader's dotenv step
    only sets variables that are absent, so present-but-empty values block the
    developer's .env from being reloaded mid-test.
    """
    for name in list(os.environ):
        if name.upper().startswith(_LLM_ENV_PREFIXES):
            monkeypatch.setenv(name, "")


def _configure_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AGENCY_LLM_MODEL", "stub-model")
    monkeypatch.setenv("AGENCY_LLM_API_KEY", "stub-key-not-a-real-credential")


class _StubLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def generate(self, prompt: str, context: Any = None) -> str:
        self.calls += 1
        return self.response


def _patch_generate(monkeypatch: pytest.MonkeyPatch, stub: _StubLLM | Any) -> None:
    async def fake_generate(self: LLMAdapter, prompt: str, context: Any = None) -> str:
        return await stub.generate(prompt, context)

    monkeypatch.setattr(LLMAdapter, "generate", fake_generate)


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["git", "init", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        ["git", "-C", str(repo), "config", "user.name", "t"],
    ):
        subprocess.run(args, check=True, capture_output=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True
    )
    return repo


# --------------------------------------------------------------------------- #
# (a) no provider configured -> heuristic, no network
# --------------------------------------------------------------------------- #


def test_no_provider_uses_heuristic_and_never_calls_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(self: LLMAdapter, prompt: str, context: Any = None) -> str:
        raise AssertionError("network call attempted with no provider configured")

    monkeypatch.setattr(LLMAdapter, "generate", forbidden)
    built: list[bool] = []
    original = reviewer.build_review_llm

    def spy() -> Any:
        built.append(True)
        return original()

    monkeypatch.setattr(reviewer, "build_review_llm", spy)
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")

    report = run_pipeline(tmp_path, ["ok.py"])

    assert built == [True]
    assert report.gate == "PASS"
    assert report.review is not None
    assert "heuristic_review" in report.review.caveats


# --------------------------------------------------------------------------- #
# (b) provider configured -> LLM reviewer is invoked
# --------------------------------------------------------------------------- #


def test_configured_provider_drives_pipeline_llm_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_provider(monkeypatch)
    stub = _StubLLM(_CLEAN_JSON)
    _patch_generate(monkeypatch, stub)
    (tmp_path / "ok.py").write_text("x = 1  # UNIQUE_SOURCE_MARKER\n", encoding="utf-8")

    report = run_pipeline(tmp_path, ["ok.py"])

    assert stub.calls == 1
    assert report.gate == "PASS"
    assert report.review is not None
    assert report.review.gate == "PASS"
    assert "heuristic_review" not in report.review.caveats
    dumped = report.model_dump_json()
    assert "SENSITIVE_RESPONSE_MARKER" not in dumped
    assert "UNIQUE_SOURCE_MARKER" not in dumped


def test_configured_provider_drives_external_agent_llm_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_provider(monkeypatch)
    stub = _StubLLM(_CLEAN_JSON)
    _patch_generate(monkeypatch, stub)
    repo = _git_repo(tmp_path)
    agent = tmp_path / "agent.py"
    agent.write_text(
        'from pathlib import Path\nPath("README.md").write_text("hi\\nchanged\\n")\n',
        encoding="utf-8",
    )

    report = run_external_agent(
        str(repo), [sys.executable, str(agent)], "change readme", cleanup=True
    )

    assert stub.calls == 1
    assert report.gate == "PASS"
    assert report.review is not None
    assert report.review.gate == "PASS"
    assert "heuristic_review" not in report.review.caveats
    assert "review_not_run" not in report.caveats
    assert "SENSITIVE_RESPONSE_MARKER" not in report.model_dump_json()


# --------------------------------------------------------------------------- #
# (c) LLM exception / refusal fails the gate closed
# --------------------------------------------------------------------------- #


def test_llm_exception_fails_gate_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_provider(monkeypatch)

    class Exploding:
        async def generate(self, prompt: str, context: Any = None) -> str:
            raise RuntimeError("SENSITIVE_RESPONSE_MARKER provider exploded")

    _patch_generate(monkeypatch, Exploding())
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")

    report = run_pipeline(tmp_path, ["ok.py"])

    assert report.gate == "FAIL"
    assert report.review is not None
    assert report.review.gate == "FAIL"
    assert any(f.rule_id == "LLM_REVIEW_FAILED" for f in report.review.findings)
    assert "SENSITIVE_RESPONSE_MARKER" not in report.model_dump_json()


def test_llm_refusal_fails_gate_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_provider(monkeypatch)
    stub = _StubLLM("I cannot review this code.")
    _patch_generate(monkeypatch, stub)
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")

    report = run_pipeline(tmp_path, ["ok.py"])

    assert report.gate == "FAIL"
    assert report.review is not None
    assert report.review.gate == "FAIL"
    assert stub.calls == 1


@pytest.mark.asyncio
async def test_llm_review_with_running_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_provider(monkeypatch)
    stub = _StubLLM(_CLEAN_JSON)
    _patch_generate(monkeypatch, stub)
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")

    report = run_pipeline(tmp_path, ["ok.py"])

    assert stub.calls == 1
    assert report.gate == "PASS"
    assert report.review is not None
    assert report.review.gate == "PASS"
    assert "heuristic_review" not in report.review.caveats


# --------------------------------------------------------------------------- #
# hallucination guard: uncited paths are demoted, never trusted
# --------------------------------------------------------------------------- #
_FOREIGN_JSON = (
    '{"score": 4, "findings": ['
    '{"path": "ghost.py", "line": 3, "rule_id": "LLM900", "severity": "critical", '
    '"message": "invented"}, '
    '{"path": "base.py", "line": 1, "rule_id": "LLM901", "severity": "high", '
    '"message": "real citation"}], "summary": "one hallucinated"}'
)


class _CapturingLLM(_StubLLM):
    def __init__(self, response: str) -> None:
        super().__init__(response)
        self.prompts: list[str] = []

    async def generate(self, prompt: str, context: Any = None) -> str:
        self.prompts.append(prompt)
        return await super().generate(prompt, context)


def test_llm_finding_with_uncited_path_is_demoted(tmp_path: Path, monkeypatch) -> None:
    from agency.forge.reviewer import run_review

    repo = _git_repo(tmp_path)
    _configure_provider(monkeypatch)
    stub = _CapturingLLM(_FOREIGN_JSON)
    _patch_generate(monkeypatch, stub)
    report = run_review(repo, ["base.py"], use_llm=True)
    rules = {f.rule_id: f for f in report.findings}
    assert "LLM_UNVERIFIED" in rules
    assert rules["LLM_UNVERIFIED"].severity == "low"
    assert rules["LLM_UNVERIFIED"].path == "ghost.py"
    assert "LLM901" in rules
    assert rules["LLM901"].severity == "high"


# --------------------------------------------------------------------------- #
# frozen-tree delta re-review
# --------------------------------------------------------------------------- #
def test_delta_re_review_scopes_prompt_and_records_shas(tmp_path: Path, monkeypatch) -> None:
    from agency.forge.diff import head_sha
    from agency.forge.reviewer import run_review

    repo = _git_repo(tmp_path)
    base = head_sha(repo)
    assert base
    (repo / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "extra"], check=True, capture_output=True
    )
    _configure_provider(monkeypatch)
    stub = _CapturingLLM(_CLEAN_JSON)
    _patch_generate(monkeypatch, stub)
    report = run_review(repo, ["base.py"], use_llm=True, delta_base_sha=base)
    assert report.delta_base_sha == base
    assert report.reviewed_sha == head_sha(repo)
    assert report.gate == "PASS"
    assert '"extra.py"' in stub.prompts[0]
    assert '"base.py"' not in stub.prompts[0]


def test_delta_base_unresolved_falls_back_with_caveat(tmp_path: Path, monkeypatch) -> None:
    from agency.forge.reviewer import run_review

    repo = _git_repo(tmp_path)
    _configure_provider(monkeypatch)
    stub = _CapturingLLM(_CLEAN_JSON)
    _patch_generate(monkeypatch, stub)
    report = run_review(repo, ["base.py"], use_llm=True, delta_base_sha="deadbeef" * 5)
    assert report.delta_base_sha == ""
    assert "delta_base_unresolved" in report.caveats
    assert '"base.py"' in stub.prompts[0]


# --------------------------------------------------------------------------- #
# shadow reviewer: same rubric, second model, recorded — never gating
# --------------------------------------------------------------------------- #
_SHADOW_FAIL_JSON = (
    '{"score": 1, "findings": [{"path": "base.py", "line": 2, "rule_id": "SH1", '
    '"severity": "high", "message": "x"}], "summary": "shadow disagrees"}'
)


def test_shadow_reviewer_records_divergence_without_gating(tmp_path: Path, monkeypatch) -> None:
    from agency.forge.reviewer import run_review

    repo = _git_repo(tmp_path)
    _configure_provider(monkeypatch)
    primary = _CapturingLLM(_CLEAN_JSON)
    _patch_generate(monkeypatch, primary)
    shadow = _StubLLM(_SHADOW_FAIL_JSON)
    report = run_review(repo, ["base.py"], use_llm=True, shadow_llm=shadow)
    assert report.gate == "PASS"  # the gate never sees the shadow verdict
    assert report.shadow_gate == "FAIL"
    assert report.shadow_score == 1
    assert report.shadow_findings
    assert report.shadow_findings[0].rule_id == "SH1"


def test_shadow_reviewer_failure_is_recorded_as_absent(tmp_path: Path, monkeypatch) -> None:
    from agency.forge.reviewer import run_review

    repo = _git_repo(tmp_path)
    _configure_provider(monkeypatch)
    primary = _CapturingLLM(_CLEAN_JSON)
    _patch_generate(monkeypatch, primary)

    class _BrokenShadow:
        async def generate(self, prompt: str, context: Any = None) -> str:
            raise RuntimeError("shadow provider down")

    report = run_review(repo, ["base.py"], use_llm=True, shadow_llm=_BrokenShadow())
    assert report.gate == "PASS"
    assert report.shadow_gate == ""
    assert report.shadow_score is None


def test_no_shadow_llm_means_absent_shadow_fields(tmp_path: Path, monkeypatch) -> None:
    from agency.forge.reviewer import run_review

    repo = _git_repo(tmp_path)
    _configure_provider(monkeypatch)
    primary = _CapturingLLM(_CLEAN_JSON)
    _patch_generate(monkeypatch, primary)
    report = run_review(repo, ["base.py"], use_llm=True)
    assert report.shadow_gate == ""
    assert report.shadow_score is None
