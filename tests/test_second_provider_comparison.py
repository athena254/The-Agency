"""Offline tests for scripts/second_provider_comparison.py (gaps.md #10 leg).

The comparison script is evidence-producing and needs live providers; the
hermetic suite must not require it to run. These tests pin only what must
hold offline: argument handling (unknown provider names, --env-file pointing
at a missing file so no live keys load) and the honest skip/refusal behavior
when no live provider is configured (echo ambient env), plus the exact-token
output matcher. No network access anywhere in this module.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "second_provider_comparison.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("second_provider_comparison", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name="script")
def script_fixture():
    return _load_script()


@pytest.fixture(autouse=True)
def _no_live_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin every credential-bearing variable the script's discovery reads.

    The ambient suite already pins ``AGENCY_LLM_*`` to echo (conftest); these
    pins keep the google resolution and any generic fallback vars offline even
    when a worktree ``.env`` exists on disk.
    """
    for key in (
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "AGENCY_GOOGLE_API_KEY",
        "AGENCY_GEMINI_API_KEY",
        "GOOGLE_MODEL",
        "GEMINI_MODEL",
        "GOOGLE_BASE_URL",
        "GEMINI_BASE_URL",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_PROVIDER",
        "LLM_MODEL",
    ):
        monkeypatch.setenv(key, "")


# --------------------------------------------------------------------------- #
# Argument handling
# --------------------------------------------------------------------------- #


def test_unknown_provider_name_is_recorded_as_skipped(script, tmp_path: Path) -> None:
    out = tmp_path / "evidence.json"
    exit_code = script.main(
        ["--out", str(out), "--env-file", str(tmp_path / "missing.env"), "--providers", "bogus"]
    )
    assert exit_code == 2  # nothing ran
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert evidence["runs"]["bogus"]["verdict"] == "skipped"
    assert evidence["runs"]["bogus"]["ran"] is False
    assert "unknown provider" in evidence["runs"]["bogus"]["reason"]


def test_unknown_env_file_is_a_no_op(script, tmp_path: Path) -> None:
    """A missing --env-file must behave like an empty one (offline refusal)."""
    out = tmp_path / "evidence.json"
    exit_code = script.main(
        [
            "--out",
            str(out),
            "--env-file",
            str(tmp_path / "does-not-exist.env"),
            "--providers",
            "default,google",
        ]
    )
    assert exit_code == 2
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert evidence["env_file"].endswith("does-not-exist.env")


# --------------------------------------------------------------------------- #
# Offline refusal — echo ambient env, no live keys: nothing runs, no forced pass
# --------------------------------------------------------------------------- #


def test_offline_refusal_writes_honest_skip_evidence(script, tmp_path: Path) -> None:
    out = tmp_path / "evidence.json"
    exit_code = script.main(
        [
            "--out",
            str(out),
            "--env-file",
            str(tmp_path / "missing.env"),
            "--providers",
            "default,google",
        ]
    )
    assert exit_code == 2  # echo ambient env + no google key: nothing to qualify
    evidence = json.loads(out.read_text(encoding="utf-8"))

    assert evidence["providers_configured"]["google"]["live"] is False
    assert evidence["providers_configured"]["default"]["provider"] == "echo"
    for name in ("default", "google"):
        run = evidence["runs"][name]
        assert run["ran"] is False
        assert run["verdict"] == "skipped"
        assert run["adapter"] is None and run["task"] is None
        assert "not configured" in run["reason"]
    # No fabricated pass anywhere in the evidence.
    assert '"verdict": "pass"' not in out.read_text(encoding="utf-8")


def test_single_provider_subset_only_records_that_provider(script, tmp_path: Path) -> None:
    out = tmp_path / "evidence.json"
    exit_code = script.main(
        [
            "--out",
            str(out),
            "--env-file",
            str(tmp_path / "missing.env"),
            "--providers",
            "google",
        ]
    )
    assert exit_code == 2
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert set(evidence["runs"]) == {"google"}


# --------------------------------------------------------------------------- #
# Exact-token output matcher (digit guard)
# --------------------------------------------------------------------------- #


def test_output_matches_exact_token(script) -> None:
    assert script.output_matches("42", "42") is True
    assert script.output_matches("The answer is 42.", "42") is True
    assert script.output_matches("42\n\n42\n\n42", "42") is True


def test_output_matches_rejects_partial_numbers_and_empty(script) -> None:
    assert script.output_matches("", "42") is False
    assert script.output_matches("420", "42") is False
    assert script.output_matches("923", "42") is False
    assert script.output_matches("id 4242 pending", "42") is False


def test_secrets_scan_flags_leaked_env_values(script, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_API_KEY=supersecretvalue-0123456789\n", encoding="utf-8")
    leaked = script.secrets_scan("body contains supersecretvalue-0123456789", env_file)
    assert leaked == ["GOOGLE_API_KEY"]
    assert script.secrets_scan("clean body", env_file) == []
