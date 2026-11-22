"""Hermetic tests for the apprentice builder and the language-gap fixes."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agency.forge import apprentice as apprentice_module
from agency.forge.apprentice import (
    _FILE_CONTRACT,
    _OUTPUT_CONTRACT,
    _parse_content,
    _parse_files,
    detect_apprentice_language,
    run_apprentice,
)
from agency.forge.reviewer import _lint_check
from agency.forge.smoke import build_command

_LLM_ENV_PREFIXES = ("AGENCY_LLM_", "LLM_", "OPENAI_", "GOOGLE_API_KEY")


@pytest.fixture(autouse=True)
def _blank_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.upper().startswith(_LLM_ENV_PREFIXES):
            monkeypatch.setenv(name, "")
    monkeypatch.setattr(apprentice_module, "_FILE_RETRY_DELAY_S", 0.0)


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["git", "init", "-b", "main", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        ["git", "-C", str(repo), "config", "user.name", "t"],
    ):
        subprocess.run(args, check=True, capture_output=True)
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True
    )
    return repo


class _StubLLM:
    """Answers per-file calls in sequence (one file per apprentice call)."""

    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def generate(self, prompt: str, context: object = None) -> str:
        self.prompts.append(prompt)
        item = self.responses.pop(0) if self.responses else ""
        if isinstance(item, Exception):
            raise item
        return item


def _file(content: str) -> str:
    return json.dumps({"content": content})


def test_parse_files_accepts_plain_and_fenced_json() -> None:
    plain = '{"files": [{"path": "a.py", "content": "x = 1"}], "summary": "one file"}'
    assert _parse_files(plain) == [("a.py", "x = 1")]
    fenced = "```json\n" + plain + "\n```"
    assert _parse_files(fenced) == [("a.py", "x = 1")]


@pytest.mark.parametrize(
    "bad",
    [
        "no json here",
        '{"files": []}',
        '{"files": [{"path": "a.py"}]}',
        '{"files": [{"path": "", "content": "x"}]}',
        "[]",
    ],
)
def test_parse_files_rejects_malformed_answers(bad: str) -> None:
    assert _parse_files(bad) is None


def test_parse_content_accepts_plain_and_fenced_json() -> None:
    assert _parse_content('{"content": "x = 1\\n"}') == "x = 1\n"
    assert _parse_content('```json\n{"content": "x"}\n```') == "x"
    assert _parse_content("no json") is None
    assert _parse_content('{"content": ""}') is None


def test_output_contract_is_binding_in_prompt() -> None:
    assert "ONLY one JSON object" in _OUTPUT_CONTRACT
    assert '"files"' in _OUTPUT_CONTRACT
    assert '"content"' in _FILE_CONTRACT


def test_file_plans_cover_all_languages() -> None:
    assert set(apprentice_module.FILE_PLANS) == {"rust", "go", "python", "node"}


def test_detect_apprentice_language_from_manifests(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    assert detect_apprentice_language(tmp_path) == "rust"
    (tmp_path / "Cargo.toml").unlink()
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    assert detect_apprentice_language(tmp_path) == "go"
    (tmp_path / "go.mod").unlink()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert detect_apprentice_language(tmp_path) == "node"
    (tmp_path / "package.json").unlink()
    assert detect_apprentice_language(tmp_path) == "python"


def test_apprentice_pass_path_builds_and_gates(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    stub = _StubLLM(
        [
            _file("VALUE = 41\n"),
            _file("import app\n\ndef test_value():\n    assert app.VALUE == 41\n"),
        ]
    )
    report = run_apprentice(
        repo,
        ["unused"],
        "build a minimal module",
        run_tests_flag=True,
        test_paths=["test_app.py"],
        run_review_flag=False,
        llm=stub,
    )
    assert report.gate == "PASS", report.failure_detail
    assert report.tests is not None and report.tests.gate == "PASS"
    assert set(report.changed_paths) == {"app.py", "test_app.py", "server.py"}
    assert report.command == ("apprentice", "hy4-flash:free")
    assert "apprentice_generated" in report.caveats
    assert "review_not_run" in report.caveats
    # per-file protocol: exactly one planned file per call
    assert len(stub.prompts) == 2
    assert "NOW WRITE exactly one file: app.py" in stub.prompts[0]
    assert "NOW WRITE exactly one file: test_app.py" in stub.prompts[1]


def test_apprentice_language_kwarg_overrides_empty_repo(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)  # empty repo: detection would say python
    stub = _StubLLM(
        [
            _file('[package]\nname = "shorty"\nedition = "2021"\n'),
            _file("pub struct Shortener;\n"),
        ]
    )
    report = run_apprentice(
        repo, ["unused"], "task", language="rust", run_review_flag=False, llm=stub
    )
    assert report.gate == "PASS", report.failure_detail
    # src/main.rs is factory-static: exactly two LLM calls, three files
    assert len(stub.prompts) == 2
    assert "NOW WRITE exactly one file: Cargo.toml" in stub.prompts[0]
    assert "NOW WRITE exactly one file: src/lib.rs" in stub.prompts[1]
    assert set(report.changed_paths) == {"Cargo.toml", "src/lib.rs", "src/main.rs"}
    from agency.forge.apprentice import STATIC_FILES

    assert STATIC_FILES[("rust", "src/main.rs")].startswith("use shorty::Shortener;")


def test_apprentice_fail_closed_on_unparsable_answer(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    report = run_apprentice(
        repo, ["unused"], "task", run_review_flag=False, llm=_StubLLM(["I cannot comply"])
    )
    assert report.gate == "FAIL"
    assert "unparsable" in report.failure_detail
    assert "app.py" in report.failure_detail  # the first planned python file


def test_apprentice_fail_closed_on_llm_error(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    report = run_apprentice(
        repo,
        ["unused"],
        "task",
        run_review_flag=False,
        llm=_StubLLM([RuntimeError("429 burst"), RuntimeError("503 burst")]),
    )
    assert report.gate == "FAIL"
    assert "LLM call failed" in report.failure_detail


def test_apprentice_recovers_from_one_bad_file_answer(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    stub = _StubLLM(
        [
            "garbage no json",
            _file("VALUE = 7\n"),
            _file("import app\n\ndef test_v():\n    assert app.VALUE == 7\n"),
        ]
    )
    report = run_apprentice(repo, ["unused"], "task", run_review_flag=False, llm=stub)
    assert report.gate == "PASS", report.failure_detail


def test_apprentice_fail_closed_without_llm(tmp_path: Path, monkeypatch) -> None:
    repo = _git_repo(tmp_path)
    monkeypatch.setattr(apprentice_module, "build_apprentice_llm", lambda: None)
    report = run_apprentice(repo, ["unused"], "task", run_review_flag=False)
    assert report.gate == "FAIL"
    assert "no apprentice LLM configured" in report.failure_detail


def test_apprentice_failing_tests_feed_the_retry_tail(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    stub = _StubLLM(
        [
            _file("VALUE = 1\n"),
            _file("import app\n\ndef test_value():\n    assert app.VALUE == 2\n"),
        ]
    )
    report = run_apprentice(
        repo,
        ["unused"],
        "task",
        run_tests_flag=True,
        test_paths=["test_app.py"],
        run_review_flag=False,
        llm=stub,
    )
    assert report.gate == "FAIL"
    assert report.tests is not None and report.tests.gate == "FAIL"
    assert "test output tail" in report.failure_detail


def test_apprentice_refuses_protected_paths(tmp_path: Path, monkeypatch) -> None:
    repo = _git_repo(tmp_path)
    monkeypatch.setattr(apprentice_module, "FILE_PLANS", {"python": ((".env", "protected probe"),)})
    report = run_apprentice(
        repo, ["unused"], "task", run_review_flag=False, llm=_StubLLM([_file("SECRET=1")])
    )
    assert report.gate == "FAIL"
    assert "refused" in report.failure_detail


# --------------------------------------------------------------------------- #
# language-gap fixes: lint beyond Python, smoke beyond python/npm
# --------------------------------------------------------------------------- #
def test_lint_check_covers_non_python_code(tmp_path: Path) -> None:
    go_file = tmp_path / "main.go"
    go_file.write_text("package main\n" + "x := 1 // " + "a" * 140 + "\n", encoding="utf-8")
    findings = _lint_check(tmp_path, "main.go")
    rules = {f.rule_id for f in findings}
    assert "LINT001" in rules  # long line flagged for Go too
    (tmp_path / "main.rs").write_text("fn main() {} \n", encoding="utf-8")
    rust_rules = {f.rule_id for f in _lint_check(tmp_path, "main.rs")}
    assert "LINT003" in rust_rules  # trailing whitespace
    (tmp_path / "lib.rs").write_text('let s = "x";\n', encoding="utf-8")
    assert _lint_check(tmp_path, "lib.rs") == []


def test_smoke_build_command_per_language(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    assert build_command(tmp_path, 8080) == ["cargo", "run", "--quiet"]
    (tmp_path / "Cargo.toml").unlink()

    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    (tmp_path / "shorty.go").write_text("package main\n", encoding="utf-8")
    assert build_command(tmp_path, 8080) == ["go", "run", "."]
    (tmp_path / "go.mod").unlink()
    (tmp_path / "main.go").unlink()
    (tmp_path / "shorty.go").unlink()

    (tmp_path / "server.py").write_text("import app\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    import sys as _sys

    assert build_command(tmp_path, 8080) == [_sys.executable, "server.py"]
    (tmp_path / "server.py").unlink()
    (tmp_path / "app.py").unlink()

    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "node --test test/app.test.ts"}}', encoding="utf-8"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "server.ts").write_text(
        'import { createHandler } from "./app.ts";\n', encoding="utf-8"
    )
    (tmp_path / "src" / "app.ts").write_text("export const x = 1;\n", encoding="utf-8")
    assert build_command(tmp_path, 8080) == ["node", "src/server.ts"]

    (tmp_path / "package.json").write_text(
        '{"scripts": {"start": "node build.js"}}', encoding="utf-8"
    )
    cmd = build_command(tmp_path, 8080)
    npm = "npm.cmd" if os.name == "nt" else "npm"
    assert cmd[0] == npm and cmd[1] == "run"
