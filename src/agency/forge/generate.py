"""Forge app generation: multi-file generation via templates / spec, KISS.

``generate_app`` writes files to ``repo_path`` (or via an optional
factory-made ``AgencyCodingAgent`` hook), then runs the existing Forge
pipeline (inspect / security scan / optional tests) via ``run_pipeline``.
It never auto-merges or authorizes release.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.forge.inspection import MAX_FILE_BYTES, MAX_FILES, _rejects_for_name
from agency.forge.pipeline import ForgePipelineReport, run_pipeline
from agency.forge.protected_paths import is_protected

logger = structlog.get_logger(__name__)

GenerateGate = Literal["PASS", "FAIL"]

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_FILES = ("pyproject.toml", "Dockerfile", ".env.example", "README.md")

_DEFAULT_APP = """from fastapi import FastAPI

app = FastAPI(title="generated-app")


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
"""

_DEFAULT_TEST = """from app import app
from fastapi.testclient import TestClient


def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
"""


class GenerateReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str
    gate: GenerateGate
    files_written: tuple[str, ...] = ()
    pipeline: ForgePipelineReport | None = None
    coding_agent_report_id: str = ""
    note: str = "Forge generated app; does not auto-merge or authorize release."


def _load_template(name: str) -> str:
    path = TEMPLATES_DIR / name
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def _default_files(app_name: str = "generated-app") -> dict[str, str]:
    pyproject = _load_template("pyproject.toml") or (
        '[project]\nname = "generated-app"\nversion = "0.1.0"\n'
    )
    pyproject = pyproject.replace("generated-app", app_name, 1)
    readme = _load_template("README.md") or "# Generated App\n"
    return {
        "app.py": _DEFAULT_APP.replace("generated-app", app_name),
        "test_app.py": _DEFAULT_TEST,
        "pyproject.toml": pyproject,
        "README.md": readme,
    }


def _normalize_spec(spec: Any) -> dict[str, str] | None:
    """Return {path: content} or None for the default minimal app."""
    if spec is None:
        return None
    if isinstance(spec, dict):
        files = spec.get("files", spec)
        if isinstance(files, dict):
            return {str(k): str(v) for k, v in files.items()}
        if isinstance(files, (list, tuple)):
            out: dict[str, str] = {}
            for item in files:
                if isinstance(item, dict):
                    path = item.get("path") or item.get("file")
                    content = item.get("content", item.get("new", ""))
                    if path is not None:
                        out[str(path)] = str(content)
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    out[str(item[0])] = str(item[1])
            return out
    return None


def _validate_files(files: dict[str, str]) -> list[str]:
    if len(files) > MAX_FILES:
        raise ValueError(f"file_count_exceeds_limit:{MAX_FILES}")
    if not files:
        raise ValueError("empty_change_set")
    for path, content in files.items():
        reason = _rejects_for_name(path)
        if reason is not None:
            raise ValueError(reason)
        normalized = path.replace("\\", "/").strip("/")
        if normalized != ".env.example" and is_protected(normalized):
            raise ValueError(f"protected path: {path}")
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"file_too_large:{path}")
    return sorted(files)


def _write_files(root: Path, files: dict[str, str]) -> tuple[str, ...]:
    written: list[str] = []
    for path in sorted(files):
        normalized = path.replace("\\", "/")
        parts = [p for p in normalized.split("/") if p not in ("", ".")]
        target = root.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(files[path].encode("utf-8"))
        written.append("/".join(parts))
    return tuple(written)


def _test_paths(files: dict[str, str]) -> list[str]:
    return sorted(
        p.replace("\\", "/")
        for p in files
        if Path(p).name.startswith("test_") and p.endswith(".py")
    ) or sorted(p for p in files if p.endswith("_test.py"))


def generate_app(
    spec: Any = None,
    repo_path: str | Path | None = None,
    agent: Any = None,
    *,
    app_name: str = "generated-app",
    run_tests_flag: bool = True,
    test_timeout_s: int = 120,
) -> GenerateReport:
    """Generate an app from ``spec`` (or the minimal FastAPI default).

    ``spec`` may be ``{"files": {path: content}}``, ``{"files": [...]}``,
    a flat ``{path: content}`` mapping, or ``None``/empty for the default.
    ``agent`` is an optional factory-made ``AgencyCodingAgent``-style hook:
    when supplied and it exposes ``run_task``, file creation is delegated
    to it (in its worktree) and the pipeline runs on the resulting tree.
    """
    files = _normalize_spec(spec)
    if not files:
        files = _default_files(app_name)
    paths = _validate_files(files)
    test_paths = _test_paths(files)
    do_tests = bool(run_tests_flag and test_paths)

    report_id = str(uuid4())
    created_at = datetime.now(UTC)

    if agent is not None and callable(getattr(agent, "run_task", None)):
        # Agent hook: delegate creation to the agent's worktree. The
        # protected-by-policy .env.example template is copied in directly
        # afterwards (it contains only placeholder values).
        agent_files = {p: c for p, c in files.items() if p.replace("\\", "/") != ".env.example"}
        edits = [
            {
                "op": "write_file",
                "path": p.replace("\\", "/"),
                "content": agent_files[p],
            }
            for p in sorted(agent_files)
        ]
        coding_report = agent.run_task(
            "forge-generate",
            edits,
            run_tests_flag=do_tests,
            test_paths=test_paths,
            test_timeout_s=test_timeout_s,
        )
        root = Path(coding_report.worktree_path)
        if ".env.example" in {p.replace("\\", "/") for p in files}:
            extra = {p: c for p, c in files.items() if p.replace("\\", "/") == ".env.example"}
            _write_files(root, extra)
            paths = sorted(set(paths) | {".env.example"})
        pipeline = run_pipeline(
            root,
            paths,
            run_tests_flag=do_tests,
            test_paths=test_paths,
            test_timeout_s=test_timeout_s,
        )
        gate: GenerateGate = (
            "PASS" if (pipeline.gate == "PASS" and coding_report.gate == "PASS") else "FAIL"
        )
        return GenerateReport(
            report_id=report_id,
            created_at=created_at,
            root=str(root),
            gate=gate,
            files_written=tuple(paths),
            pipeline=pipeline,
            coding_agent_report_id=coding_report.report_id,
        )

    if repo_path is None:
        workspace = getattr(agent, "workspace", None) if agent is not None else None
        if workspace is None:
            raise ValueError("repo_path is required when no agent workspace is available")
        repo_path = workspace
    root = Path(repo_path)
    if not root.is_absolute():
        raise ValueError(f"repo_path must be an absolute path, got {repo_path!r}")
    root.mkdir(parents=True, exist_ok=True)
    written = _write_files(root, files)
    pipeline = run_pipeline(
        root,
        list(written),
        run_tests_flag=do_tests,
        test_paths=test_paths,
        test_timeout_s=test_timeout_s,
    )
    logger.info("forge.generate_done", root=str(root), files=len(written), gate=pipeline.gate)
    return GenerateReport(
        report_id=report_id,
        created_at=created_at,
        root=str(root),
        gate=pipeline.gate,
        files_written=written,
        pipeline=pipeline,
    )


__all__ = [
    "TEMPLATES_DIR",
    "TEMPLATE_FILES",
    "GenerateGate",
    "GenerateReport",
    "_default_files",
    "generate_app",
]
