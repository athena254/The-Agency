"""Forge apprentice builder: the factory's own LLM applies bounded edits.

The apprentice is the Agency's task LLM (a factory-issued spawn, not an
external CLI striker). It receives the rendered work order plus
language-specific implementation notes and writes ONE FILE PER CALL (small
generations finish inside the free provider's duration window; one long
multi-file call does not). The files are applied through the existing gated
coding agent
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

_FILE_CONTRACT = (
    "\n\nOUTPUT FORMAT (binding): respond with ONLY one JSON object, no prose,"
    ' no code fences:\n{"content": "<the complete file content>"}\n'
    "Full file contents only — no shell commands, no patches, no explanations."
)

# The file plan per language is fixed (the work order and notes already pin
# the layout), so the apprentice generates ONE FILE PER CALL. Small calls
# stay under the free provider's ~90s generation kill-switch; one long
# multi-file call does not. This is also the context-economy doctrine: the
# model never re-carries earlier files.
COMPACT_CONTRACT = (
    "SERVICE CONTRACT (binding):\n"
    "- URL shortener; standard library ONLY; no external dependencies; no network installs.\n"
    "- Read HOST (default 127.0.0.1) and PORT (default 8080) from the environment.\n"
    "- GET / -> 200 with a small JSON status body.\n"
    '- POST /shorten, JSON body {"url": "<absolute http(s) url>"} -> 200 with JSON'
    ' {"short": "<id>", "url": "<original>"}; missing/invalid url -> 400.\n'
    "- GET /<id> -> 302 with Location header to the stored url; unknown id -> 404.\n"
    "- In-memory map storage; short unique id (base62/counter/hash prefix).\n"
)

FILE_PLANS: dict[str, tuple[tuple[str, str], ...]] = {
    "rust": (
        (
            "Cargo.toml",
            "the crate manifest: [package] name = \"shorty\", version = \"0.1.0\","
            " edition = \"2021\"; NO [dependencies] section.",
        ),
        (
            "src/lib.rs",
            "the logic library: `pub struct Shortener` (HashMap<String,String> behind"
            " std::sync::Mutex, AtomicU64 counter), `pub fn new()`, `pub fn shorten(&self,"
            " url: &str) -> String` (counter-based id, validate scheme http/https else"
            " panic-free empty result), `pub fn resolve(&self, id: &str) ->"
            " Option<String>`; end with `#[cfg(test)] mod tests` covering roundtrip +"
            " unknown + distinct ids. Keep it small and simple (KISS).",
        ),
        (
            "src/main.rs",
            "factory-provided transport shim (see STATIC_FILES): the apprentice owns"
            " the logic and tests in src/lib.rs; the fixed HTTP shell is"
            " infrastructure, not creative work.",
        ),
    ),
    "go": (
        ("go.mod", "the module manifest: module shorty; go 1.22."),
        (
            "main.go",
            "the complete service. Wire handlers on an http.ServeMux (export"
            " `func NewMux() *http.ServeMux` so tests can reach it) plus `func main()`"
            " reading HOST/PORT and calling http.ListenAndServe.",
        ),
        (
            "main_test.go",
            "tests via net/http/httptest against NewMux(): shorten 200+id, resolve 302"
            " Location, unknown 404, invalid 400. NO subprocesses.",
        ),
    ),
    "python": (
        (
            "app.py",
            "the complete service on http.server. Expose a handler class factory"
            " (e.g. `def make_handler(store):` returning a"
            " http.server.BaseHTTPRequestHandler subclass using `store`) plus `main()`"
            " reading HOST/PORT, so tests can exercise handler logic without sockets.",
        ),
        (
            "test_app.py",
            "pytest tests importing app and exercising the handler logic directly"
            " (shorten 200+id payload, resolve 302 target, unknown 404, invalid 400)."
            " NO subprocesses, NO real sockets.",
        ),
    ),
    "node": (
        (
            "package.json",
            'the manifest: {"name": "shorty", "type": "module", "scripts": {"test":'
            ' "node --test test/app.test.ts"}}. No dependencies.',
        ),
        (
            "src/app.ts",
            "the complete service. Export `export function shorten(url: string):"
            " { short: string; url: string }` and `export function resolve(id: string):"
            " string | null`, plus server startup reading HOST/PORT with node:http."
            " Erasable TypeScript only (no enums/namespaces).",
        ),
        (
            "test/app.test.ts",
            "node:test tests importing src/app.ts: shorten returns an id,"
            " resolve returns the url / null for unknown, invalid input handled. NO"
            " subprocesses, NO real sockets.",
        ),
    ),
}

_FILE_RETRY_DELAY_S = 30.0

# Factory-provided files: fixed infrastructure the apprentice does not
# regenerate (a reasoning model burns its whole duration window reasoning
# about raw HTTP parsing). Everything meaningful — the logic and the tests —
# stays apprentice-owned.
STATIC_FILES: dict[tuple[str, str], str] = {
    ("rust", "src/main.rs"): r"""use shorty::Shortener;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::{Arc, Mutex};

fn respond(stream: &mut std::net::TcpStream, status_line: &str, body: &str, extra: &str) {
    let resp = format!(
        "HTTP/1.1 {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n{}Connection: close\r\n\r\n{}",
        status_line,
        body.len(),
        extra,
        body
    );
    let _ = stream.write_all(resp.as_bytes());
}

fn extract_json_string_field<'a>(body: &'a str, field: &str) -> Option<&'a str> {
    let key = format!("\"{}\"", field);
    let start = body.find(&key)? + key.len();
    let rest = &body[start..];
    let colon = rest.find(':')?;
    let after = rest[colon + 1..].trim_start();
    if !after.starts_with('"') {
        return None;
    }
    let value = &after[1..];
    let end = value.find('"')?;
    Some(&value[..end])
}

fn read_request(stream: &mut std::net::TcpStream) -> Option<String> {
    let mut buf: Vec<u8> = Vec::new();
    let mut chunk = [0u8; 4096];
    loop {
        match stream.read(&mut chunk) {
            Ok(0) => break,
            Ok(n) => buf.extend_from_slice(&chunk[..n]),
            Err(_) => return None,
        }
        if buf.len() > 512 * 1024 {
            return None;
        }
        let text = String::from_utf8_lossy(&buf).to_string();
        if let Some(idx) = text.find("\r\n\r\n") {
            let headers = &text[..idx];
            let content_length: usize = headers
                .lines()
                .find_map(|line| {
                    let mut it = line.split(':');
                    let name = it.next()?.trim();
                    if name.eq_ignore_ascii_case("content-length") {
                        it.next()?.trim().parse().ok()
                    } else {
                        None
                    }
                })
                .unwrap_or(0);
            let body_start = idx + 4;
            while buf.len() < body_start + content_length {
                match stream.read(&mut chunk) {
                    Ok(0) => break,
                    Ok(m) => buf.extend_from_slice(&chunk[..m]),
                    Err(_) => break,
                }
            }
            break;
        }
    }
    Some(String::from_utf8_lossy(&buf).to_string())
}

fn main() {
    let host = std::env::var("HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let port: u16 = std::env::var("PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(8080);
    let shortener = Arc::new(Mutex::new(Shortener::new()));
    let listener = TcpListener::bind((host.as_str(), port)).expect("bind failed");
    for stream in listener.incoming() {
        let mut stream = match stream { Ok(s) => s, Err(_) => continue };
        let req = read_request(&mut stream).unwrap_or_default();
        let mut parts = req.split_whitespace();
        let method = parts.next().unwrap_or("").to_string();
        let path = parts.next().unwrap_or("/").to_string();
        if method == "GET" && (path == "/" || path.is_empty()) {
            respond(&mut stream, "200 OK", "{\"status\":\"ok\",\"service\":\"shorty\"}", "");
        } else if method == "POST" && path == "/shorten" {
            let url = extract_json_string_field(&req, "url").unwrap_or("").to_string();
            let id = if url.is_empty()
                || !(url.starts_with("http://") || url.starts_with("https://"))
                || url.contains('\r')
                || url.contains('\n')
            {
                String::new()
            } else {
                shortener.lock().unwrap().shorten(&url)
            };
            if id.is_empty() {
                respond(&mut stream, "400 Bad Request", "{\"error\":\"invalid url\"}", "");
            } else {
                respond(&mut stream, "200 OK", &format!("{{\"short\":\"{}\"}}", id), "");
            }
        } else if method == "GET" && path.len() > 1 {
            let id = path.get(1..).unwrap_or("").to_string();
            if id.is_empty() {
                respond(&mut stream, "404 Not Found", "{\"error\":\"not found\"}", "");
            } else {
                let resolved = {
                    let guard = shortener.lock().unwrap();
                    guard.resolve(&id)
                };
                match resolved {
                    Some(url) => respond(&mut stream, "302 Found", "", &format!("Location: {}\r\n", url)),
                    None => respond(&mut stream, "404 Not Found", "{\"error\":\"not found\"}", ""),
                }
            }
        } else {
            respond(&mut stream, "404 Not Found", "{\"error\":\"not found\"}", "");
        }
    }
}
""",
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


def _parse_content(response: str) -> str | None:
    """Extract one file's content from the per-file JSON contract."""
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
    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    return content


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
    language: str = "",
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

    # Empty target repos cannot reveal their language: the job carries it.
    lang = (language or "").strip().lower()
    if lang == "typescript":
        lang = "node"
    if lang not in FILE_PLANS:
        lang = detect_apprentice_language(real_repo)
    plan = FILE_PLANS[lang]

    # One file per call with a COMPACT prompt: the full work order stays in
    # the job record for audit, but generation prompts carry only the service
    # contract plus the single file's instruction. A reasoning model handed
    # the whole build spec reasons about the entire app even when asked for
    # one tiny manifest, blowing past the free provider's duration window.
    files: list[tuple[str, str]] = []
    for path, purpose in plan:
        static_content = STATIC_FILES.get((lang, path))
        if static_content is not None:
            files.append((path, static_content))
            logger.info("forge.apprentice_static_file", file=path)
            continue
        prompt = (
            f"{COMPACT_CONTRACT}\nNOW WRITE exactly one file: {path} — {purpose}."
            f"{_FILE_CONTRACT}"
        )
        response: str | None = None
        last_error: str | None = None
        for attempt in range(2):
            try:
                response = _generate(llm, prompt)
                last_error = None
            except Exception as exc:  # noqa: BLE001 - provider bursts fail honestly
                last_error = f"LLM call failed: {type(exc).__name__}"
                logger.warning(
                    "forge.apprentice_file_call_failed", file=path, error_class=type(exc).__name__
                )
            if response is not None and _parse_content(response) is not None:
                break
            if attempt == 0:
                import time as _time

                _time.sleep(_FILE_RETRY_DELAY_S)
        if last_error is not None and response is None:
            return _fail(f"{path}: {last_error}")
        content = _parse_content(response or "")
        if content is None:
            return _fail(f"{path}: apprentice returned unparsable output (file contract)")
        files.append((path, content))
        logger.info("forge.apprentice_file_generated", file=path, chars=len(content))

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
    "FILE_PLANS",
    "apprentice",
    "build_apprentice_llm",
    "detect_apprentice_language",
    "run_apprentice",
]
