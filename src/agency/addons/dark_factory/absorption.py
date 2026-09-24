"""Legacy absorption mode — rewrite a repository toward Agency coding conventions.

The conventions include Pydantic v2 models for data, structlog for logging,
``from __future__ import annotations`` with full type hints, and Google-style
docstrings on public modules.
"""

from __future__ import annotations

import ast
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = structlog.get_logger(__name__)

_GITHUB_RE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?$")


class AbsorptionResult(BaseModel):
    """Outcome of rewriting a repository to Agency coding conventions."""

    model_config = ConfigDict(extra="forbid")

    repo_url: str
    repo_path: str
    files_analyzed: int = Field(default=0, ge=0)
    files_rewritten: int = Field(default=0, ge=0)
    validation_passed: bool = Field(default=False)
    committed: bool = Field(default=False)
    details: dict[str, object] = Field(default_factory=dict)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RepoAbsorber:
    """Legacy clone → analyze → rewrite → validate pipeline; commits are opt-in."""

    AGENCY_HEADER = '"""Agency module (absorbed)."""\n\nfrom __future__ import annotations\n'
    ATHENA_HEADER = AGENCY_HEADER  # Backward-compatible name; no ATHENA integration.

    def __init__(
        self, workspace: Path | str | None = None
    ) -> None:
        self.workspace = Path(workspace) if workspace else Path(tempfile.gettempdir()) / "agency" / "absorbed"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._log = logger.bind(component="RepoAbsorber", workspace=str(self.workspace))

    # ------------------------------------------------------------------ #
    # Pipeline steps
    # ------------------------------------------------------------------ #

    def clone_repo(self, repo_url: str, dest: Path | str | None = None) -> Path:
        """Clone a GitHub repo into the workspace. Returns the checkout path."""
        if not _GITHUB_RE.match(repo_url.strip()):
            raise ValueError(f"not a valid GitHub repo URL: {repo_url!r}")
        target = Path(dest) if dest else self.workspace / _repo_name(repo_url)
        if target.exists():
            raise FileExistsError(f"destination already exists: {target}")
        self._log.info("absorb.clone", repo_url=repo_url, dest=str(target))
        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(target)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr.strip()}")
        return target

    def analyze_structure(self, repo_path: Path | str) -> dict[str, object]:
        """Summarize the Python layout of a checkout (no code executed)."""
        root = Path(repo_path)
        if not root.is_dir():
            raise FileNotFoundError(f"repo path not found: {root}")
        py_files = sorted(p for p in root.rglob("*.py") if ".git" not in p.parts)
        total_lines = 0
        with_tests = False
        entry_points: list[str] = []
        for path in py_files:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            total_lines += text.count("\n") + 1
            lowered = str(path).lower()
            if "test" in lowered:
                with_tests = True
            if path.name in ("__main__.py", "cli.py", "main.py", "app.py"):
                entry_points.append(str(path.relative_to(root)))
        summary: dict[str, object] = {
            "root": str(root),
            "python_files": len(py_files),
            "total_lines": total_lines,
            "has_tests": with_tests,
            "entry_points": entry_points,
            "files": [str(p.relative_to(root)) for p in py_files],
        }
        self._log.info("absorb.analyzed", root=str(root), python_files=len(py_files))
        return summary

    def rewrite_to_agency(self, repo_path: Path | str) -> dict[str, object]:
        """Rewrite Python files in place toward Agency coding conventions."""
        root = Path(repo_path)
        if not root.is_dir():
            raise FileNotFoundError(f"repo path not found: {root}")
        rewritten: list[str] = []
        skipped: list[str] = []
        for path in sorted(root.rglob("*.py")):
            if ".git" in path.parts:
                continue
            try:
                original = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                skipped.append(str(path.relative_to(root)))
                continue
            updated = self._rewrite_source(original)
            if updated != original:
                path.write_text(updated, encoding="utf-8")
                rewritten.append(str(path.relative_to(root)))
        self._log.info("absorb.rewritten", root=str(root), rewritten=len(rewritten))
        return {"rewritten": rewritten, "skipped": skipped, "count": len(rewritten)}

    def validate(self, rewritten_code: str | Path | dict[str, str]) -> dict[str, object]:
        """Validate rewritten code: every unit must at least parse as Python."""
        units: dict[str, str] = {}
        if isinstance(rewritten_code, dict):
            units = dict(rewritten_code)
        elif isinstance(rewritten_code, Path) or (
            isinstance(rewritten_code, str) and Path(rewritten_code).exists()
        ):
            root = Path(rewritten_code)
            for path in sorted(root.rglob("*.py")):
                if ".git" in path.parts:
                    continue
                try:
                    units[str(path)] = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    units[str(path)] = f"# UNREADABLE: {exc}"
        else:
            units = {"<inline>": str(rewritten_code)}
        failures: dict[str, str] = {}
        for name, source in units.items():
            try:
                ast.parse(source)
            except SyntaxError as exc:
                failures[name] = f"{exc.msg} (line {exc.lineno})"
        report = {"checked": len(units), "failures": failures, "passed": not failures}
        self._log.info("absorb.validated", checked=len(units), passed=report["passed"])
        return report

    def commit_changes(
        self, repo_path: Path | str, message: str = "Absorb into Agency coding conventions"
    ) -> str:
        """Commit rewritten files. Returns the new commit SHA."""
        root = Path(repo_path)
        if not (root / ".git").is_dir():
            raise ValueError(f"not a git checkout: {root}")
        for args in (["add", "-A"], ["commit", "-m", message]):
            result = subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, timeout=120, check=False
            )
            if result.returncode != 0 and "nothing to commit" not in result.stdout + result.stderr:
                raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        commit_sha = sha.stdout.strip() if sha.returncode == 0 else "unknown"
        self._log.info("absorb.committed", root=str(root), sha=commit_sha)
        return commit_sha

    def absorb(self, repo_url: str, *, commit: bool = False) -> AbsorptionResult:
        """Clone, rewrite and validate; commit only on explicit opt-in."""
        repo_path = self.clone_repo(repo_url)
        summary = self.analyze_structure(repo_path)
        rewrite = self.rewrite_to_agency(repo_path)
        report = self.validate(repo_path)
        committed = False
        sha: str | None = None
        if report["passed"] and commit:
            sha = self.commit_changes(repo_path)
            committed = True
        return AbsorptionResult(
            repo_url=repo_url,
            repo_path=str(repo_path),
            files_analyzed=int(summary["python_files"]),
            files_rewritten=int(rewrite["count"]),
            validation_passed=bool(report["passed"]),
            committed=committed,
            details={"summary": summary, "rewrite": rewrite, "validation": report, "sha": sha},
        )

    # ------------------------------------------------------------------ #
    # Rewrite rules
    # ------------------------------------------------------------------ #

    def rewrite_to_athena(self, repo_path: Path | str) -> dict[str, object]:
        """Compatibility alias; rewrites toward Agency conventions, not ATHENA."""
        return self.rewrite_to_agency(repo_path)

    def _rewrite_source(self, source: str) -> str:
        """Apply lightweight, syntax-preserving Agency normalizations."""
        updated = source
        if "from __future__ import annotations" not in updated:
            updated = self.AGENCY_HEADER + updated.lstrip("\n")
        if "import structlog" not in updated and "from structlog" not in updated:
            updated = updated.replace(
                "from __future__ import annotations\n",
                "from __future__ import annotations\n\nimport structlog\n\nlogger = structlog.get_logger(__name__)\n",
                1,
            )
        # print(...) -> logger.info(...) outside of strings is hard in general;
        # only rewrite bare top-level print calls on their own line.
        updated = re.sub(
            r"^(\s*)print\((.*)\)\s*$",
            r"\1logger.info(\2)",
            updated,
            flags=re.MULTILINE,
        )
        return updated


class AbsorptionSpec(BaseModel):
    """Validated input for an absorption run."""

    model_config = ConfigDict(extra="forbid")

    repo_url: str

    @field_validator("repo_url")
    @classmethod
    def _must_be_github(cls, value: str) -> str:
        if not _GITHUB_RE.match(value.strip()):
            raise ValueError("repo_url must be a GitHub repo URL")
        return value.strip()


def _repo_name(repo_url: str) -> str:
    name = repo_url.rstrip("/").split("/")[-1]
    return name.removesuffix(".git")


__all__ = ["AbsorptionResult", "AbsorptionSpec", "RepoAbsorber"]
