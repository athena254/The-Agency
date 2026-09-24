"""GhostFactory — multi-language builder with six operating modes.

Modes
-----
NEW:        scaffold a fresh project in any supported language.
REVERSE:    reconstruct source from a binary (static analysis only).
FORK:       clone a repo and apply a change-set description.
CONTRIBUTE: prepare a pull-request payload for an upstream repo.
REWORK:     convert code from one shape/language to another.
STUDY:      produce a study plan + starter scaffold for a language.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.addons.ghost_factory.languages import Language, LanguageSpec, LanguageSupport
from agency.addons.ghost_factory.reverse import ReverseEngineer

logger = structlog.get_logger(__name__)


class BuildMode(str, Enum):
    """The six Ghost Factory operating modes."""

    NEW = "new"
    REVERSE = "reverse"
    FORK = "fork"
    CONTRIBUTE = "contribute"
    REWORK = "rework"
    STUDY = "study"


class NewSpec(BaseModel):
    """Specification for a fresh build."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(default="")
    language: Language = Field(default=Language.PYTHON)
    features: list[str] = Field(default_factory=list)


class GhostBuild(BaseModel):
    """A build artifact produced by the Ghost Factory."""

    model_config = ConfigDict(extra="forbid")

    mode: BuildMode
    language: Language
    name: str
    files: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GhostFactory:
    """Six-mode, multi-language project builder."""

    def __init__(
        self,
        languages: LanguageSupport | None = None,
        reverser: ReverseEngineer | None = None,
    ) -> None:
        self._log = logger.bind(component="GhostFactory")
        self._languages = languages or LanguageSupport()
        self._reverser = reverser or ReverseEngineer()

    @property
    def languages(self) -> LanguageSupport:
        """The underlying language toolchain harness."""
        return self._languages

    # ------------------------------------------------------------------ #
    # NEW
    # ------------------------------------------------------------------ #

    def build_new(
        self, spec: NewSpec | dict[str, Any], language: Language | str | None = None
    ) -> GhostBuild:
        """Scaffold a new project (hello-world + feature stubs + README)."""
        parsed = spec if isinstance(spec, NewSpec) else NewSpec.model_validate(spec)
        lang = self._resolve_language(language or parsed.language)
        lang_spec = self._languages.get_language_spec(lang)
        slug = _slug(parsed.name)
        files = {
            f"{slug}{lang_spec.extension}": _new_source(lang, lang_spec, parsed),
            "README.md": f"# {parsed.name}\n\n{parsed.description or 'Built with Ghost Factory.'}\n\n## Language\n\n{lang.value}\n",
        }
        build = GhostBuild(
            mode=BuildMode.NEW,
            language=lang,
            name=parsed.name,
            files=files,
            notes=[f"Scaffolded {parsed.name} in {lang.value}."],
            evidence={
                "features": parsed.features,
                "toolchain_available": self._languages.toolchain_available(lang),
            },
        )
        self._log.info("ghost.new", name=parsed.name, language=lang.value)
        return build

    # ------------------------------------------------------------------ #
    # REVERSE
    # ------------------------------------------------------------------ #

    def reverse_engineer(self, binary: Path | str, language: str = "python") -> GhostBuild:
        """Reconstruct editable source from a binary (static analysis only)."""
        path = Path(binary)
        if not path.is_file():
            raise FileNotFoundError(f"binary not found: {path}")
        analysis = self._reverser.analyze_binary(path)
        reconstructed = self._reverser.reconstruct_source(path, language)
        decompiled = self._reverser.decompile(path)
        lang = _safe_language(language)
        build = GhostBuild(
            mode=BuildMode.REVERSE,
            language=lang,
            name=f"{path.stem}_reconstructed",
            files={
                f"{path.stem}_reconstructed.txt": reconstructed.code,
                f"{path.stem}_listing.txt": decompiled.code,
            },
            notes=reconstructed.notes,
            evidence={"analysis": analysis.model_dump(mode="json")},
        )
        self._log.info("ghost.reverse", binary=str(path))
        return build

    # ------------------------------------------------------------------ #
    # FORK
    # ------------------------------------------------------------------ #

    def fork_repo(
        self, repo_url: str, changes: str | dict[str, str], dest: Path | str | None = None
    ) -> GhostBuild:
        """Clone a repo and apply text changes ({relative_path: new_content})."""
        _require_git_url(repo_url)
        if isinstance(changes, str):
            if not changes.strip():
                raise ValueError("changes must not be empty")
            change_map: dict[str, str] = {"CHANGES.md": changes}
        else:
            change_map = dict(changes)
            if not change_map:
                raise ValueError("changes must not be empty")
        target = (
            Path(dest)
            if dest
            else Path(tempfile.mkdtemp(prefix="ghost-fork-")) / _repo_name(repo_url)
        )
        if target.exists():
            raise FileExistsError(f"destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        clone = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(target)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if clone.returncode != 0:
            raise RuntimeError(f"git clone failed: {clone.stderr.strip()}")
        applied: list[str] = []
        for rel, content in change_map.items():
            dest_file = target / rel
            if ".." in Path(rel).parts:
                raise ValueError(f"unsafe change path: {rel!r}")
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            dest_file.write_text(content, encoding="utf-8")
            applied.append(rel)
        self._log.info("ghost.fork", repo_url=repo_url, applied=applied)
        return GhostBuild(
            mode=BuildMode.FORK,
            language=Language.CUSTOM,
            name=_repo_name(repo_url),
            files=dict(change_map),
            notes=[f"Forked {repo_url} into {target}.", f"Applied {len(applied)} change(s)."],
            evidence={"repo_url": repo_url, "checkout": str(target), "applied": applied},
        )

    # ------------------------------------------------------------------ #
    # CONTRIBUTE
    # ------------------------------------------------------------------ #

    def contribute_pr(self, repo_url: str, pr: str | dict[str, Any]) -> GhostBuild:
        """Prepare a structured pull-request payload (does not push)."""
        _require_git_url(repo_url)
        payload = {"description": pr} if isinstance(pr, str) else dict(pr)
        title = str(payload.get("title", "Ghost Factory contribution"))
        if not title.strip():
            raise ValueError("PR title must not be empty")
        body = str(payload.get("body", payload.get("description", "")))
        branch = str(payload.get("branch", "ghost-factory/contribution"))
        raw_files: Any = payload.get("files", {})
        if not isinstance(raw_files, dict):
            raise TypeError("PR files must be a mapping")
        patch_files: dict[str, str] = {str(k): str(v) for k, v in raw_files.items()}
        pr_text = (
            f"# {title}\n\nUpstream: {repo_url}\nBranch: `{branch}`\n\n{body}\n\n"
            f"## Files ({len(patch_files)})\n" + "".join(f"- `{name}`\n" for name in patch_files)
        )
        files = {"PULL_REQUEST.md": pr_text, **patch_files}
        self._log.info("ghost.contribute", repo_url=repo_url, title=title)
        return GhostBuild(
            mode=BuildMode.CONTRIBUTE,
            language=Language.CUSTOM,
            name=title,
            files=files,
            notes=["PR payload prepared locally — push and open the PR with `gh`."],
            evidence={"repo_url": repo_url, "branch": branch, "title": title},
        )

    # ------------------------------------------------------------------ #
    # REWORK
    # ------------------------------------------------------------------ #

    def rework_code(self, code: str, target_format: str | Language) -> GhostBuild:
        """Convert code into a target shape (language or ``'athena'`` style)."""
        if not code.strip():
            raise ValueError("code must not be empty")
        target = (
            str(target_format.value if isinstance(target_format, Language) else target_format)
            .strip()
            .lower()
        )
        if target == "athena":
            converted = _to_athena_style(code)
            lang = Language.PYTHON
        else:
            lang = _safe_language(target)
            lang_spec = self._languages.get_language_spec(lang)
            converted = f"# Reworked into {lang.value} (from source snippet)\n{lang_spec.hello_world}# Original below:\n{_comment_out(code, lang)}\n"
        self._log.info("ghost.rework", target=target)
        return GhostBuild(
            mode=BuildMode.REWORK,
            language=lang,
            name=f"reworked_{lang.value}",
            files={f"reworked{_ext(lang)}": converted},
            notes=[f"Reworked source into {target}. Manual review required."],
            evidence={"target": target, "source_chars": len(code)},
        )

    # ------------------------------------------------------------------ #
    # STUDY
    # ------------------------------------------------------------------ #

    def study_language(self, language: Language | str, docs_url: str | None = None) -> GhostBuild:
        """Produce a study plan + starter scaffold for learning a language."""
        lang = self._resolve_language(language)
        lang_spec = self._languages.get_language_spec(lang)
        if docs_url is not None and not docs_url.strip().lower().startswith(
            ("http://", "https://")
        ):
            raise ValueError("docs_url must be an http(s) URL")
        plan = (
            f"# Study plan: {lang.value}\n\n"
            "1. Setup — install the toolchain and verify `--version`.\n"
            "2. Basics — variables, control flow, functions, modules.\n"
            "3. Practice — port the starter scaffold and extend it.\n"
            "4. Verify — run the language test command until green.\n"
            + (f"\nDocs: {docs_url}\n" if docs_url else "\n")
        )
        files = {
            "STUDY_PLAN.md": plan,
            f"starter{lang_spec.extension}": lang_spec.hello_world or "# starter\n",
        }
        self._log.info("ghost.study", language=lang.value)
        return GhostBuild(
            mode=BuildMode.STUDY,
            language=lang,
            name=f"study_{lang.value}",
            files=files,
            notes=[f"Study scaffold for {lang.value} ready."],
            evidence={
                "docs_url": docs_url,
                "toolchain_available": self._languages.toolchain_available(lang),
            },
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _resolve_language(self, language: Language | str) -> Language:
        if isinstance(language, Language):
            return language
        try:
            return Language(language.strip().lower())
        except ValueError:
            raise ValueError(f"unsupported language: {language!r}") from None


def _safe_language(value: str) -> Language:
    try:
        return Language(value.strip().lower())
    except ValueError:
        return Language.CUSTOM


def _slug(name: str) -> str:
    slug = re.sub(r"\W+", "_", name.strip().lower()).strip("_")
    return slug or "ghost_app"


def _ext(lang: Language) -> str:
    try:
        from agency.addons.ghost_factory.languages import _SPECS

        return _SPECS[lang].extension
    except KeyError:
        return ".txt"


def _new_source(lang: Language, spec: LanguageSpec, parsed: NewSpec) -> str:
    header = f"Starter for {parsed.name}: {parsed.description or 'Ghost Factory scaffold.'}\n"
    stubs = "\n".join(f"# TODO: {feature}" for feature in parsed.features)
    if lang is Language.PYTHON:
        return (
            f'"""{header}"""\n\nfrom __future__ import annotations\n\n'
            "import structlog\n\nlogger = structlog.get_logger(__name__)\n\n\n"
            "def main() -> None:\n"
            f'    """Entry point for {parsed.name}."""\n'
            f'    logger.info("app.start", app={parsed.name!r})\n'
            f'    print("hello from {parsed.name}")\n'
            + (f"\n{stubs}\n" if stubs else "")
            + '\n\nif __name__ == "__main__":\n    main()\n'
        )
    return f"# {header}\n{spec.hello_world}" + (f"\n{stubs}\n" if stubs else "")


def _repo_name(repo_url: str) -> str:
    name = repo_url.rstrip("/").split("/")[-1]
    return (name.removesuffix(".git")) or "repo"


def _require_git_url(repo_url: str) -> None:
    if not re.match(r"^https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?$", repo_url.strip()):
        raise ValueError(f"not a valid GitHub repo URL: {repo_url!r}")


def _to_athena_style(code: str) -> str:
    out = code
    if "from __future__ import annotations" not in out:
        out = "from __future__ import annotations\n\n" + out.lstrip("\n")
    if "structlog" not in out:
        out = out.replace(
            "from __future__ import annotations\n",
            "from __future__ import annotations\n\nimport structlog\n\nlogger = structlog.get_logger(__name__)\n",
            1,
        )
    return out


def _comment_out(code: str, lang: Language) -> str:
    prefix = (
        "// "
        if lang
        in (
            Language.RUST,
            Language.GO,
            Language.C,
            Language.CPP,
            Language.JAVASCRIPT,
            Language.TYPESCRIPT,
        )
        else "# "
    )
    return "\n".join(f"{prefix}{line}" if line.strip() else prefix for line in code.splitlines())


__all__ = ["BuildMode", "GhostBuild", "GhostFactory", "NewSpec"]
