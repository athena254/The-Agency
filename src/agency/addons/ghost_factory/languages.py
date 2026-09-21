"""Language support — specs, compile, run, and test across many languages.

Supported: Python, Rust, Go, C, C++, JavaScript, TypeScript, F#, Malbolge,
plus open-ended ``custom`` toolchains defined by the caller.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from enum import Enum
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)


class Language(str, Enum):
    """Languages the Ghost Factory can target."""

    PYTHON = "python"
    RUST = "rust"
    GO = "go"
    C = "c"
    CPP = "cpp"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    FSHARP = "fsharp"
    MALBOLGE = "malbolge"
    CUSTOM = "custom"


class LanguageSpec(BaseModel):
    """Toolchain description for a language."""

    model_config = ConfigDict(extra="forbid")

    language: Language
    extension: str
    compile_cmd: list[str] = Field(default_factory=list)
    run_cmd: list[str] = Field(default_factory=list)
    test_cmd: list[str] = Field(default_factory=list)
    version_cmd: list[str] = Field(default_factory=list)
    hello_world: str = Field(default="")
    notes: str = Field(default="")


class ExecutionResult(BaseModel):
    """Result of a compile / run / test invocation."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    language: Language
    command: list[str] = Field(default_factory=list)
    stdout: str = Field(default="")
    stderr: str = Field(default="")
    returncode: int = Field(default=0)
    skipped_reason: str | None = Field(default=None)


_SPECS: dict[Language, LanguageSpec] = {
    Language.PYTHON: LanguageSpec(
        language=Language.PYTHON,
        extension=".py",
        run_cmd=["python3", "{file}"],
        test_cmd=["python3", "-m", "pytest", "-q"],
        version_cmd=["python3", "--version"],
        hello_world='print("hello from ghost factory")\n',
    ),
    Language.RUST: LanguageSpec(
        language=Language.RUST,
        extension=".rs",
        compile_cmd=["rustc", "{file}", "-o", "{stem}"],
        run_cmd=["{stem}"],
        test_cmd=["cargo", "test", "-q"],
        version_cmd=["rustc", "--version"],
        hello_world='fn main() { println!("hello from ghost factory"); }\n',
    ),
    Language.GO: LanguageSpec(
        language=Language.GO,
        extension=".go",
        compile_cmd=["go", "build", "-o", "{stem}", "{file}"],
        run_cmd=["go", "run", "{file}"],
        test_cmd=["go", "test", "./..."],
        version_cmd=["go", "version"],
        hello_world='package main\n\nimport "fmt"\n\nfunc main() { fmt.Println("hello from ghost factory") }\n',
    ),
    Language.C: LanguageSpec(
        language=Language.C,
        extension=".c",
        compile_cmd=["cc", "{file}", "-o", "{stem}"],
        run_cmd=["{stem}"],
        version_cmd=["cc", "--version"],
        hello_world='#include <stdio.h>\nint main(void) { printf("hello from ghost factory\\n"); return 0; }\n',
    ),
    Language.CPP: LanguageSpec(
        language=Language.CPP,
        extension=".cpp",
        compile_cmd=["c++", "{file}", "-o", "{stem}"],
        run_cmd=["{stem}"],
        version_cmd=["c++", "--version"],
        hello_world='#include <iostream>\nint main() { std::cout << "hello from ghost factory\\n"; }\n',
    ),
    Language.JAVASCRIPT: LanguageSpec(
        language=Language.JAVASCRIPT,
        extension=".js",
        run_cmd=["node", "{file}"],
        test_cmd=["npm", "test"],
        version_cmd=["node", "--version"],
        hello_world='console.log("hello from ghost factory");\n',
    ),
    Language.TYPESCRIPT: LanguageSpec(
        language=Language.TYPESCRIPT,
        extension=".ts",
        compile_cmd=["tsc", "{file}"],
        run_cmd=["node", "{stem}.js"],
        test_cmd=["npm", "test"],
        version_cmd=["tsc", "--version"],
        hello_world='console.log("hello from ghost factory");\n',
    ),
    Language.FSHARP: LanguageSpec(
        language=Language.FSHARP,
        extension=".fs",
        compile_cmd=["dotnet", "build"],
        run_cmd=["dotnet", "run"],
        test_cmd=["dotnet", "test"],
        version_cmd=["dotnet", "--version"],
        hello_world='printfn "hello from ghost factory"\n',
    ),
    Language.MALBOLGE: LanguageSpec(
        language=Language.MALBOLGE,
        extension=".mb",
        notes="Esoteric language: no standard toolchain; generation only, execution unsupported.",
        hello_world="(esoteric placeholder — Malbolge has no portable hello-world toolchain)\n",
    ),
    Language.CUSTOM: LanguageSpec(
        language=Language.CUSTOM,
        extension=".txt",
        notes="Caller-provided toolchain; override compile_cmd/run_cmd/test_cmd at runtime.",
    ),
}


class LanguageSupport:
    """Registry + execution harness for supported languages."""

    def __init__(self, timeout: int = 60) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        self._log = logger.bind(component="LanguageSupport")
        self._custom: dict[str, LanguageSpec] = {}

    # ------------------------------------------------------------------ #
    # Registry
    # ------------------------------------------------------------------ #

    def supported(self) -> list[Language]:
        """All built-in languages."""
        return list(_SPECS.keys())

    def register_custom(self, name: str, spec: LanguageSpec) -> LanguageSpec:
        """Register a custom toolchain under ``name``. Returns the stored spec."""
        if not name.strip():
            raise ValueError("custom language name must not be empty")
        key = name.strip().lower()
        self._custom[key] = spec
        self._log.info("language.custom_registered", name=key)
        return spec

    def get_language_spec(self, language: Language | str) -> LanguageSpec:
        """Fetch the toolchain spec for a language (built-in or custom)."""
        if isinstance(language, str):
            key = language.strip().lower()
            try:
                lang = Language(key)
            except ValueError:
                if key in self._custom:
                    return self._custom[key]
                raise ValueError(f"unknown language: {language!r}") from None
            return _SPECS[lang]
        if language is Language.CUSTOM and Language.CUSTOM in _SPECS:
            return _SPECS[language]
        try:
            return _SPECS[language]
        except KeyError:
            raise ValueError(f"unknown language: {language!r}") from None

    def toolchain_available(self, language: Language | str) -> bool:
        """Whether the primary binary for a language exists on PATH."""
        spec = self.get_language_spec(language)
        cmds = spec.compile_cmd[:1] + spec.run_cmd[:1] + spec.version_cmd[:1]
        binaries = [c for c in cmds if c and not c.startswith("{")]
        if not binaries:
            return False
        return any(shutil.which(b) for b in binaries)

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    def compile_code(
        self,
        code: str,
        language: Language | str,
        extra_cmd: list[str] | None = None,
    ) -> ExecutionResult:
        """Compile ``code``; skipped (not failed) when no toolchain is configured."""
        spec = self.get_language_spec(language)
        cmd = list(extra_cmd) if extra_cmd else list(spec.compile_cmd)
        if not cmd:
            return ExecutionResult(
                ok=False,
                language=spec.language,
                skipped_reason=f"no compile toolchain for {spec.language.value}",
            )
        with tempfile.TemporaryDirectory(prefix="ghost-compile-") as tmp:
            src = Path(tmp) / f"main{spec.extension}"
            src.write_text(code, encoding="utf-8")
            resolved = [_fill(token, src) for token in cmd]
            return self._run(resolved, spec.language, cwd=Path(tmp))

    def run_code(self, code: str, language: Language | str) -> ExecutionResult:
        """Write ``code`` to a temp file and execute it via the language runner."""
        spec = self.get_language_spec(language)
        if not spec.run_cmd:
            return ExecutionResult(
                ok=False,
                language=spec.language,
                skipped_reason=f"no run toolchain for {spec.language.value}",
            )
        if not self.toolchain_available(language):
            binary = next((c for c in spec.run_cmd if not c.startswith("{")), "?")
            return ExecutionResult(
                ok=False,
                language=spec.language,
                command=list(spec.run_cmd),
                skipped_reason=f"toolchain not installed: {binary}",
            )
        with tempfile.TemporaryDirectory(prefix="ghost-run-") as tmp:
            src = Path(tmp) / f"main{spec.extension}"
            src.write_text(code, encoding="utf-8")
            resolved = [_fill(token, src) for token in spec.run_cmd]
            return self._run(resolved, spec.language, cwd=Path(tmp))

    def test_code(
        self,
        code: str,
        language: Language | str,
        test_code: str | None = None,
    ) -> ExecutionResult:
        """Run the language test command against ``code`` in an isolated dir."""
        spec = self.get_language_spec(language)
        if not spec.test_cmd:
            # Fallback: at least prove the snippet executes.
            result = self.run_code(code, language)
            return ExecutionResult(
                ok=result.ok,
                language=spec.language,
                command=result.command,
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
                skipped_reason=result.skipped_reason or ("run-as-test fallback"),
            )
        if not self.toolchain_available(language):
            return ExecutionResult(
                ok=False,
                language=spec.language,
                command=list(spec.test_cmd),
                skipped_reason=f"test toolchain not installed for {spec.language.value}",
            )
        with tempfile.TemporaryDirectory(prefix="ghost-test-") as tmp:
            src = Path(tmp) / f"main{spec.extension}"
            src.write_text(code, encoding="utf-8")
            if test_code:
                (Path(tmp) / f"test_main{spec.extension}").write_text(test_code, encoding="utf-8")
            resolved = [_fill(token, src) for token in spec.test_cmd]
            return self._run(resolved, spec.language, cwd=Path(tmp))

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _run(self, cmd: list[str], language: Language, cwd: Path) -> ExecutionResult:
        self._log.info("language.exec", language=language.value, cmd=cmd)
        resolved = _resolve_binary(cmd)
        try:
            proc = subprocess.run(
                resolved, capture_output=True, text=True, timeout=self.timeout, cwd=cwd, check=False
            )
        except FileNotFoundError:
            return ExecutionResult(
                ok=False,
                language=language,
                command=cmd,
                skipped_reason=f"binary not found: {cmd[0]}",
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                ok=False,
                language=language,
                command=cmd,
                skipped_reason=f"timed out after {self.timeout}s",
            )
        return ExecutionResult(
            ok=proc.returncode == 0,
            language=language,
            command=cmd,
            stdout=proc.stdout[-4000:],
            stderr=proc.stderr[-4000:],
            returncode=proc.returncode,
        )


def _resolve_binary(cmd: list[str]) -> list[str]:
    """Swap well-known binary names for PATH-compatible equivalents."""
    if not cmd:
        return cmd
    if cmd[0] == "python3":
        # sys.executable is always the running interpreter; the bare
        # ``python3`` alias is a broken Microsoft Store stub on some Windows hosts.
        return [sys.executable, *cmd[1:]]
    fallbacks = {"cc": ["cl", "gcc"], "c++": ["cl", "g++"]}
    if shutil.which(cmd[0]):
        return cmd
    for alt in fallbacks.get(cmd[0], []):
        if shutil.which(alt):
            return [alt, *cmd[1:]]
    return cmd


def _fill(token: str, src: Path) -> str:
    """Expand ``{file}`` / ``{stem}`` placeholders for a temp source file."""
    binary = str(src.with_name("main_exec"))
    mapping = {"file": str(src), "stem": binary}
    if src.suffix == ".go":
        # Go tooling operates on the source file directly, not a bare stem.
        mapping["stem"] = str(src)
    try:
        return token.format(**mapping)
    except KeyError:
        return token


__all__ = ["ExecutionResult", "Language", "LanguageSpec", "LanguageSupport"]
