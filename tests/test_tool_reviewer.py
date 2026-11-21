"""Tests for the import reviewers (Phase 4a of tool provisioning).

SkillSpectorReviewer is exercised against a FAKE ``skillspector`` executable
staged on PATH (no network, no real scanner install): the stub records the
argv it received and writes a canned JSON report to the ``--output`` target.
Fail-closed behavior is asserted everywhere: scanner missing, nonzero exit,
unreadable/malformed report, and timeout all yield verdict UNAVAILABLE, which
callers must treat as "do not register". StaticFallbackReviewer is checked for
its deterministic pattern vocabulary, including the ``.env`` vs ``os.environ``
boundary, and is always marked available.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agency.tools.reviewer import (
    SkillSpectorReviewer,
    StaticFallbackReviewer,
    ToolReviewer,
    ToolReviewVerdict,
)

REPORT_TEMPLATE = {"score": 95, "findings": []}


def _verdict(**over: Any) -> ToolReviewVerdict:
    fields: dict[str, Any] = {
        "scanner": "skillspector",
        "score": 90,
        "verdict": "PASS",
        "findings_summary": "clean",
        "report_json": {},
    }
    fields.update(over)
    return ToolReviewVerdict(**fields)


def _write_stub(
    bin_dir: Path, *, report: dict[str, Any] | str | None, exit_code: int, sleep_s: float
) -> None:
    """Stage a fake ``skillspector`` executable: records argv, writes the report."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    canned = bin_dir / "canned_report.json"
    if report is None:
        canned.write_text("", encoding="utf-8")
    else:
        payload = report if isinstance(report, str) else json.dumps(report)
        canned.write_text(payload, encoding="utf-8")
    stub = bin_dir / "stub.py"
    stub.write_text(
        "import json, os, sys, time\n"
        f"CANNED = {json.dumps(str(canned))}\n"
        f"BIN = {json.dumps(str(bin_dir))}\n"
        "args = sys.argv[1:]\n"
        "with open(os.path.join(BIN, 'args.json'), 'w', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(args))\n"
        "sleep_path = os.path.join(BIN, 'sleep_s.txt')\n"
        "if os.path.exists(sleep_path):\n"
        "    time.sleep(float(open(sleep_path, encoding='utf-8').read()))\n"
        "out = args[args.index('--output') + 1]\n"
        "data = open(CANNED, encoding='utf-8').read()\n"
        "if data:\n"  # empty canned report -> scanner produced no output file
        "    with open(out, 'w', encoding='utf-8') as fh:\n"
        "        fh.write(data)\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        launcher = bin_dir / "skillspector.cmd"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{stub}" %*\r\n', encoding="utf-8")
    else:
        launcher = bin_dir / "skillspector"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)


def _stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kw: Any) -> Path:
    bin_dir = tmp_path / "bin"
    _write_stub(bin_dir, **kw)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    return bin_dir


def _cleared_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def _args_recorded(bin_dir: Path) -> list[str]:
    return json.loads((bin_dir / "args.json").read_text(encoding="utf-8"))


# --- verdict model ------------------------------------------------------------


def test_verdict_is_frozen() -> None:
    v = _verdict()
    with pytest.raises(ValidationError):
        v.score = 0  # type: ignore[misc]


def test_verdict_vocabulary_is_closed() -> None:
    with pytest.raises(ValidationError):
        _verdict(verdict="MAYBE")


def test_reviewers_satisfy_the_tool_reviewer_protocol() -> None:
    assert isinstance(SkillSpectorReviewer(), ToolReviewer)
    assert isinstance(StaticFallbackReviewer(), ToolReviewer)


# --- SkillSpectorReviewer: availability ---------------------------------------


def test_unavailable_when_not_on_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _cleared_path(monkeypatch, tmp_path)
    reviewer = SkillSpectorReviewer()
    assert reviewer.is_available() is False
    verdict = reviewer.review(tmp_path)
    assert verdict.verdict == "UNAVAILABLE"
    assert verdict.scanner == "skillspector"
    assert "PATH" in verdict.findings_summary


def test_available_when_stub_on_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stage(monkeypatch, tmp_path, report=REPORT_TEMPLATE, exit_code=0, sleep_s=0.0)
    assert SkillSpectorReviewer().is_available() is True


def test_review_invokes_the_documented_command_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bin_dir = _stage(monkeypatch, tmp_path, report=REPORT_TEMPLATE, exit_code=0, sleep_s=0.0)
    scan_root = tmp_path / "pkg"
    scan_root.mkdir()
    verdict = SkillSpectorReviewer().review(scan_root)
    assert verdict.verdict == "PASS"
    args = _args_recorded(bin_dir)
    assert args[:2] == ["scan", str(scan_root)]
    assert args[2:-2] == ["--no-llm", "--format", "json"]
    assert args[-2] == "--output"
    assert args[-1] != str(scan_root)  # report goes to its own temp file


# --- SkillSpectorReviewer: verdict mapping ------------------------------------


@pytest.mark.parametrize("score", [80, 95, 100])
def test_pass_requires_score_at_least_80(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, score: int
) -> None:
    _stage(monkeypatch, tmp_path, report={"score": score, "findings": []}, exit_code=0, sleep_s=0.0)
    verdict = SkillSpectorReviewer().review(tmp_path)
    assert verdict.verdict == "PASS"
    assert verdict.score == score


@pytest.mark.parametrize("score", [0, 79])
def test_fail_below_score_80(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, score: int) -> None:
    _stage(monkeypatch, tmp_path, report={"score": score, "findings": []}, exit_code=0, sleep_s=0.0)
    assert SkillSpectorReviewer().review(tmp_path).verdict == "FAIL"


def test_fail_on_high_severity_finding_even_with_good_score(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = {
        "score": 95,
        "findings": [{"severity": "high", "message": "shell execution"}],
    }
    _stage(monkeypatch, tmp_path, report=report, exit_code=0, sleep_s=0.0)
    verdict = SkillSpectorReviewer().review(tmp_path)
    assert verdict.verdict == "FAIL"
    assert verdict.score == 95
    assert "high" in verdict.findings_summary


def test_medium_findings_do_not_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report = {
        "score": 85,
        "findings": [{"severity": "medium", "message": "style"}],
    }
    _stage(monkeypatch, tmp_path, report=report, exit_code=0, sleep_s=0.0)
    verdict = SkillSpectorReviewer().review(tmp_path)
    assert verdict.verdict == "PASS"
    assert "medium" in verdict.findings_summary


def test_missing_or_non_integer_score_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stage(monkeypatch, tmp_path, report={"findings": []}, exit_code=0, sleep_s=0.0)
    verdict = SkillSpectorReviewer().review(tmp_path)
    assert verdict.verdict == "FAIL"


def test_report_json_round_trips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stage(monkeypatch, tmp_path, report=REPORT_TEMPLATE, exit_code=0, sleep_s=0.0)
    verdict = SkillSpectorReviewer().review(tmp_path)
    assert verdict.report_json == REPORT_TEMPLATE


# --- SkillSpectorReviewer: scanner failures -> UNAVAILABLE --------------------


def test_unavailable_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stage(monkeypatch, tmp_path, report=REPORT_TEMPLATE, exit_code=3, sleep_s=0.0)
    verdict = SkillSpectorReviewer().review(tmp_path)
    assert verdict.verdict == "UNAVAILABLE"
    assert "3" in verdict.findings_summary


def test_unavailable_on_malformed_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stage(monkeypatch, tmp_path, report="this is not json", exit_code=0, sleep_s=0.0)
    assert SkillSpectorReviewer().review(tmp_path).verdict == "UNAVAILABLE"


def test_unavailable_on_missing_report_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stage(monkeypatch, tmp_path, report=None, exit_code=0, sleep_s=0.0)
    assert SkillSpectorReviewer().review(tmp_path).verdict == "UNAVAILABLE"


def test_unavailable_on_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _write_stub(bin_dir, report=REPORT_TEMPLATE, exit_code=0, sleep_s=0.0)
    (bin_dir / "sleep_s.txt").write_text("3", encoding="utf-8")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    verdict = SkillSpectorReviewer(scan_timeout_s=1).review(tmp_path)
    assert verdict.verdict == "UNAVAILABLE"
    assert "timed out" in verdict.findings_summary


# --- StaticFallbackReviewer ---------------------------------------------------


def _pkg(
    tmp_path: Path,
    tool_py: str,
    test_py: str = "import tool\n\n\ndef test_ok():\n    assert True\n",
) -> Path:
    root = tmp_path / "pkg"
    root.mkdir(exist_ok=True)
    (root / "tool.py").write_text(tool_py, encoding="utf-8")
    (root / "test_tool.py").write_text(test_py, encoding="utf-8")
    return root


CLEAN_TOOL = "def run(args, ctx):\n    return {'ok': True}\n"


def test_fallback_is_always_available_and_passes_clean_code(tmp_path: Path) -> None:
    reviewer = StaticFallbackReviewer()
    assert reviewer.is_available() is True
    verdict = reviewer.review(_pkg(tmp_path, CLEAN_TOOL))
    assert verdict.verdict == "PASS"
    assert verdict.score == 100
    assert verdict.scanner == "static-fallback"


@pytest.mark.parametrize(
    ("snippet", "label"),
    [
        ("import subprocess\n", "subprocess"),
        ("import os\n\n\ndef run(args, ctx):\n    os.system('ls')\n", "os.system"),
        ("def run(args, ctx):\n    return eval(args['x'])\n", "eval"),
        ("def run(args, ctx):\n    exec(args['x'])\n", "exec"),
        ("def run(args, ctx):\n    return __import__(args['m'])\n", "__import__"),
        ("import socket\n", "socket"),
        ("import socketserver\n", "socketserver"),
        ("import requests\n", "requests"),
        ("import httpx\n", "httpx"),
    ],
)
def test_fallback_flags_forbidden_patterns_in_tool_code(
    tmp_path: Path, snippet: str, label: str
) -> None:
    verdict = StaticFallbackReviewer().review(_pkg(tmp_path, CLEAN_TOOL + "\n" + snippet))
    assert verdict.verdict == "FAIL"
    assert label in verdict.findings_summary


def test_fallback_allows_http_clients_in_the_test_file(tmp_path: Path) -> None:
    test_py = "import httpx\nimport requests\n\n\ndef test_ok():\n    assert True\n"
    verdict = StaticFallbackReviewer().review(_pkg(tmp_path, CLEAN_TOOL, test_py=test_py))
    assert verdict.verdict == "PASS"


def test_fallback_universal_patterns_apply_everywhere(tmp_path: Path) -> None:
    verdict = StaticFallbackReviewer().review(
        _pkg(
            tmp_path,
            CLEAN_TOOL,
            test_py="import tool\n\n\ndef test_ok():\n    assert tool.run({'cmd': 'rm -rf /tmp/x'}, None)\n",
        )
    )
    assert verdict.verdict == "FAIL"
    assert "rm -rf" in verdict.findings_summary


def test_fallback_flags_credential_paths(tmp_path: Path) -> None:
    tool_py = "from pathlib import Path\n\nHOME = Path('~/.ssh/id_rsa')\n"
    verdict = StaticFallbackReviewer().review(_pkg(tmp_path, tool_py))
    assert verdict.verdict == "FAIL"
    assert "id_rsa" in verdict.findings_summary


def test_fallback_does_not_flag_os_environ(tmp_path: Path) -> None:
    # ".env" must not false-positive inside "os.environ".
    tool_py = "import os\n\n\ndef run(args, ctx):\n    return os.environ.get('HOME', '')\n"
    verdict = StaticFallbackReviewer().review(_pkg(tmp_path, tool_py))
    assert verdict.verdict == "PASS"


def test_fallback_flags_dotenv_reference(tmp_path: Path) -> None:
    tool_py = "from pathlib import Path\n\nSECRETS = Path('.env')\n"
    verdict = StaticFallbackReviewer().review(_pkg(tmp_path, tool_py))
    assert verdict.verdict == "FAIL"
    assert ".env" in verdict.findings_summary
