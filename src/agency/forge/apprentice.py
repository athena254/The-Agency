"""Forge apprentice builder: the factory's own LLM applies bounded edits.

The apprentice is the Agency's task LLM (a factory-issued spawn, not an
external CLI striker). It receives the rendered work order plus
language-specific implementation notes, answers with one bounded JSON file
list, and those files are applied through the existing gated coding agent
(worktree isolation, protected paths, inspection, security scan, tests).
An independent review round then scores the result. Fail-closed throughout:
an unparsable answer, an LLM error, or a gate failure yields a FAIL report
whose bounded ``failure_detail`` feeds the orchestrator's retry note. No LLM
prose is ever persisted (test-enforced invariant).
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from agency.forge.coding_agent import MAX_EDITS, run_coding_agent
from agency.forge.external_agent import ExternalAgentReport, _failure_detail
from agency.forge.inspection import MAX_FILES, inspect_changes
from agency.forge.pipeline import CAVEATS as _PIPELINE_CAVEATS
from agency.forge.reviewer import ReviewReport, run_review
from agency.forge.security_scan import scan_changes

logger = structlog.get_logger(__name__)

APPRENTICE_MODEL = "hy4-flash:free"
DEFAULT_TEST_TIMEOUT_S = 240

_BASE_CAVEATS = ("apprentice_generated", "agent_output_unverified", *_PIPELINE_CAVEATS, "review_not_run")

_OUTPUT_CONTRACT = (
    "\n\nOUTPUT FORMAT (binding): respond with ONLY one JSON object, no prose,"
    ' no code fences:\n{"files": [{"path": "relative/path", "content": "full'
    ' file content"}], "summary": "one line"}\n'
    "Every file is created or overwritten inside the isolated worktree. No"
    " shell commands, no patches, no diffs — full file contents only. At most"
    f" {MAX_EDITS} files; each file under 1 MiB."
)

_LANGUAGE_NOTES: dict[str, str] = {
    "rust": (
        "- Rust 2021 edition, standard library ONLY (no crates, no network installs).\n"
        "- Files: Cargo.toml (name, edition = \"2021\", no [dependencies]), src/main.rs.\n"
        "- Tests inline in src/main.rs under #[cfg(test)] mod tests (std-only assertions).\n"
        "- The test gate runs: cargo test\n"
        "- Read HOST (default 127.0.0.1) and PORT (default 8080) from std::env; bind TcpListener."
    ),
    "go": (
        "- Go, standard library ONLY (net/http, encoding/json, os).\n"
        "- Files: go.mod (module shorty; go 1.22), main.go, main_test.go.\n"
        "- The test gate runs: go test ./...\n"
        "- Read HOST (default 127.0.0.1) and PORT (default 8080) via os.Getenv."
    ),
    "node": (
        "- TypeScript on Node >= 26 (native type stripping: erasable TS only —"
        " no enums, no namespaces, plain type annotations).\n"
        "- Files: package.json ({\"type\":\"module\", \"scripts\":{\"test\":\"node --test"
        " test/app.test.ts\"}}), src/app.ts, test/app.test.ts.\n"
        "- Built-in modules only (node:http, node:crypto) — NO npm installs.\n"
        "- The test gate runs: npm test (which must invoke node --test).\n"
        "- Read HOST (default 127.0.0.1) and PORT (default 8080) from process.env."
    ),
    "python": (
        "- Python 3.12+, standard library ONLY (http.server, json, hashlib, secrets).\n"
        "- Files: app.py, test_app.py (pytest, importing app.py from the repo root).\n"
        "- The test gate runs: pytest test_app.py\n"
        "- Read HOST (default 127.0.0.1) and PORT (default 8080) from os.environ."
    ),
}


def detect_apprentice_language(root: str | Path) -> str:
    """Language key for implementation notes, from repo-root manifests."""
    base = Path(root)
    if (base / "Cargo.toml").is_file():
        return "rust"
    if (base / "go.mod").is_file():
        return "go"
    if (base / "package.json").is_file():
        return "node"
    return "python"


def build_apprentice_llm() -> Any | None:
    """The task LLM drives the apprentice; echo/offline means not configured."""
    try:
        from agency.llm.adapter import LLMAdapter
        from agency.llm.config import ProviderKind, load_config

        config = load_config()
        if config.provider is ProviderKind.ECHO:
            return None
        return LLMAdapter(config=config)
    except Exception:  # noqa: BLE001
        logger.warning("forge.apprentice_llm_unavailable")
        return None


def _generate(llm: Any, prompt: str) -> str:
    """Bridge the async LLM adapter from sync code (reviewer's pattern)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(llm.generate(prompt))
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(llm.generate(prompt))).result()


def _parse_files(response: str) -> list[tuple[str, str]] | None:
    """Extract the bounded file list from the apprentice's JSON answer."""
    text = (response or "").strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw_files = data.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        return None
    files: list[tuple[str, str]] = []
    for item in raw_files[:MAX_FILES]:
        if not isinstance(item, dict):
            return None
        path = str(item.get("path", "")).strip().replace("\\", "/")
        content = item.get("content")
        if not path or not isinstance(content, str) or not content.strip():
            return None
        files.append((path, content))
    return files


def _empty_reports(root: Path) -> tuple[Any, Any]:
    return inspect_changes(root, []), scan_changes(root, [])


def run_apprentice(
    repo_root: str | Path,
    command: str | list[str],
    task: str,
    *,
    branch: str | None = None,
    worktree_root: str | Path | None = None,
    run_tests_flag: bool = False,
    test_paths: list[str] | None = None,
    test_timeout_s: int = DEFAULT_TEST_TIMEOUT_S,
    run_review_flag: bool = True,
    llm: Any = None,
    **kwargs: Any,
) -> ExternalAgentReport:
    """One apprentice build: LLM files -> gated coding agent -> LLM review.

    Signature-compatible with the external-agent runner so BlacksmithOps can
    dispatch apprentices exactly like strikers; ``command`` is accepted but
    ignored (the apprentice is in-process).
    """
    del command  # in-process builder; the CLI command does not apply
    real_repo = Path(repo_root)
    report_id = str(uuid4())
    created_at = datetime.now(UTC)

    def _fail(detail: str, *, review: ReviewReport | None = None) -> ExternalAgentReport:
        inspection, security = _empty_reports(real_repo)
        caveats = list(_BASE_CAVEATS)
        if review is not None:
            caveats = [c for c in caveats if c != "review_not_run"]
        return ExternalAgentReport(
            report_id=report_id,
            created_at=created_at,
            worktree_path=str(real_repo),
            branch=str(branch or "forge-apprentice"),
            command=("apprentice", APPRENTICE_MODEL),
            task=task,
            exit_code=1,
            changed_paths=(),
            failure_detail=detail[:500],
            inspection=inspection,
            security=security,
            review=review,
            gate="FAIL",
            caveats=tuple(caveats),
        )

    llm = llm if llm is not None else build_apprentice_llm()
    if llm is None:
        return _fail("no apprentice LLM configured; run refused (fail closed)")

    notes = _LANGUAGE_NOTES[detect_apprentice_language(real_repo)]
    prompt = f"{task}\n\nIMPLEMENTATION NOTES (language-specific, binding):\n{notes}{_OUTPUT_CONTRACT}"

    try:
        response = _generate(llm, prompt)
    except Exception as exc:  # noqa: BLE001 - provider errors fail the build honestly
        logger.warning("forge.apprentice_llm_call_failed", error_class=type(exc).__name__)
        return _fail(f"apprentice LLM call failed: {type(exc).__name__}")

    files = _parse_files(response)
    if files is None:
        return _fail("apprentice returned unparsable output; expected the JSON files contract")

    # run_coding_agent enforces worktree isolation, protected paths, size caps
    # and per-file limits; write_file creates-or-overwrites inside the worktree.
    edits: list[tuple[str, str, str]] = [
        ("write_file", path, content) for path, content in files
    ]
    try:
        coding = run_coding_agent(
            real_repo,
            edits,
            branch=branch or "forge-apprentice",
            worktree_root=worktree_root,
            run_tests_flag=run_tests_flag,
            test_paths=list(test_paths or []),
            test_timeout_s=test_timeout_s,
        )
    except ValueError as exc:  # protected path / invalid input: honest build failure
        return _fail(f"apprentice build refused: {exc}")
    applied_paths = tuple(path for path, _content in files)

    if coding.gate != "PASS":
        detail = f"apprentice build failed: {coding.note}"
        if coding.tests is not None and coding.tests.gate != "PASS":
            tail = _failure_detail(coding.tests.stdout, coding.tests.stderr, limit=400)
            if tail:
                detail = f"{detail} | test output tail: {tail}"
        inspection, security = _empty_reports(real_repo)
        return ExternalAgentReport(
            report_id=report_id,
            created_at=created_at,
            worktree_path=coding.worktree_path,
            branch=coding.branch,
            command=("apprentice", APPRENTICE_MODEL),
            task=task,
            exit_code=1,
            changed_paths=applied_paths,
            failure_detail=_failure_detail(detail, "", limit=500) or detail[:500],
            inspection=inspection,
            security=security,
            tests=coding.tests,
            gate="FAIL",
            caveats=_BASE_CAVEATS,
        )

    review: ReviewReport | None = None
    review_error = False
    if run_review_flag:
        try:
            review = run_review(
                coding.worktree_path,
                list(applied_paths),
                use_llm=True,
                tests_report=coding.tests,
            )
        except Exception:  # noqa: BLE001
            review_error = True
            logger.error("forge.apprentice_review_error")

    overall = "PASS"
    if review is not None and review.gate != "PASS":
        overall = "FAIL"
    if review_error:
        overall = "FAIL"

    caveats = list(_BASE_CAVEATS)
    if coding.tests is not None:
        caveats = [c for c in caveats if c != "tests_not_run"]
    if review is not None:
        caveats = [c for c in caveats if c != "review_not_run"]
    if review_error:
        caveats.append("review_error")

    report = ExternalAgentReport(
        report_id=report_id,
        created_at=created_at,
        worktree_path=coding.worktree_path,
        branch=coding.branch,
        command=("apprentice", APPRENTICE_MODEL),
        task=task,
        exit_code=0 if overall == "PASS" else 1,
        changed_paths=applied_paths,
        inspection=coding.inspection,
        security=coding.security,
        tests=coding.tests,
        review=review,
        gate="PASS" if overall == "PASS" else "FAIL",  # type: ignore[arg-type]
        caveats=tuple(caveats),
    )
    logger.info(
        "forge.apprentice_done",
        root=coding.worktree_path,
        files=len(files),
        gate=report.gate,
    )
    return report


apprentice = run_apprentice

__all__ = [
    "APPRENTICE_MODEL",
    "apprentice",
    "build_apprentice_llm",
    "detect_apprentice_language",
    "run_apprentice",
]
