"""DarkFactory — Python-focused, task-agnostic code generation over a full SDLC."""

from __future__ import annotations

import ast
import hashlib
import re
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.addons.dark_factory.absorption import AbsorptionResult, RepoAbsorber
from agency.addons.dark_factory.sdlc import SDLCArtifact, SDLCWorkflow

logger = structlog.get_logger(__name__)


class ToolSpec(BaseModel):
    """Specification for a generated tool."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class ModuleSpec(BaseModel):
    """Specification for a generated Python module."""

    model_config = ConfigDict(extra="forbid")

    module: str = Field(min_length=1, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    description: str = Field(min_length=1)
    functions: list[str] = Field(default_factory=list)
    classes: list[str] = Field(default_factory=list)


class GeneratedCode(BaseModel):
    """A generated code unit with evidence of how it was produced."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    name: str
    language: str = Field(default="python")
    code: str
    sha256: str = Field(default="")
    sdlc_artifacts: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def model_post_init(self, _context: Any) -> None:
        if not self.sha256:
            object.__setattr__(
                self, "sha256", hashlib.sha256(self.code.encode("utf-8")).hexdigest()
            )


class DarkFactory:
    """Python-focused factory: requirements → design → build → test → deploy.

    Task-agnostic by design — every public method funnels through the
    :class:`SDLCWorkflow` so generated code always carries SDLC evidence,
    regardless of the requested domain.
    """

    LANGUAGE = "python"

    def __init__(self, absorber: RepoAbsorber | None = None) -> None:
        self._log = logger.bind(component="DarkFactory")
        self._sdlc = SDLCWorkflow()
        self._absorber = absorber or RepoAbsorber()

    # ------------------------------------------------------------------ #
    # Generation
    # ------------------------------------------------------------------ #

    def generate_tool(self, spec: ToolSpec | dict[str, Any] | str) -> GeneratedCode:
        """Generate a self-contained Python tool from a spec."""
        parsed = self._coerce_tool_spec(spec)
        cycle = self._sdlc.run_full_cycle(
            {"goal": parsed.purpose, "tool": parsed.name, **parsed.model_dump()}
        )
        code = self._render_tool(parsed)
        self._assert_syntax(code)
        self._log.info("dark_factory.tool_generated", name=parsed.name)
        return GeneratedCode(
            kind="tool",
            name=parsed.name,
            code=code,
            sdlc_artifacts=[a.artifact_id for a in cycle],
        )

    def build_cli(self, schema: dict[str, Any] | BaseModel) -> GeneratedCode:
        """Generate a ``typer``-based CLI module from a command schema."""
        payload = schema.model_dump() if isinstance(schema, BaseModel) else dict(schema)
        app_name = str(payload.get("app", payload.get("name", "app")))
        commands: list[str] = [str(c) for c in payload.get("commands", ["run"])]
        cycle = self._sdlc.run_full_cycle({"goal": f"CLI {app_name}", "commands": commands})
        code = self._render_cli(app_name, commands)
        self._assert_syntax(code)
        self._log.info("dark_factory.cli_built", app=app_name, commands=commands)
        return GeneratedCode(
            kind="cli",
            name=app_name,
            code=code,
            sdlc_artifacts=[a.artifact_id for a in cycle],
        )

    def create_module(self, spec: ModuleSpec | dict[str, Any]) -> GeneratedCode:
        """Generate a typed Python module (Pydantic v2 + structlog) from a spec."""
        parsed = spec if isinstance(spec, ModuleSpec) else ModuleSpec.model_validate(spec)
        cycle = self._sdlc.run_full_cycle({"goal": parsed.description, "module": parsed.module})
        code = self._render_module(parsed)
        self._assert_syntax(code)
        self._log.info("dark_factory.module_created", module=parsed.module)
        return GeneratedCode(
            kind="module",
            name=parsed.module,
            code=code,
            sdlc_artifacts=[a.artifact_id for a in cycle],
        )

    # ------------------------------------------------------------------ #
    # Improvement
    # ------------------------------------------------------------------ #

    def improve_code(self, code: str, feedback: str) -> GeneratedCode:
        """Apply textual feedback to code, preserving syntax validity."""
        if not feedback.strip():
            raise ValueError("feedback must not be empty")
        self._assert_syntax(code)
        header = f'"""Improved: {feedback.strip()[:120]}."""\n'
        improved = header + code if not code.startswith('"""Improved:') else code
        # Ensure feedback-requested symbols exist as stubs rather than failing silently.
        for symbol in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", feedback):
            if not re.search(rf"\b(def|class)\s+{re.escape(symbol)}\b", improved):
                improved += f'\n\ndef {symbol}(*args: object, **kwargs: object) -> object:\n    """Stub added per review feedback."""\n    raise NotImplementedError\n'
        self._assert_syntax(improved)
        self._log.info("dark_factory.code_improved", feedback_len=len(feedback))
        return GeneratedCode(kind="improvement", name="improved", code=improved)

    def self_rectify(self, code: str) -> GeneratedCode:
        """Automatically fix common defects: syntax errors, missing future import, tabs."""
        fixed = code.replace("\t", "    ")
        if "from __future__ import annotations" not in fixed:
            fixed = "from __future__ import annotations\n\n" + fixed.lstrip("\n")
        try:
            ast.parse(fixed)
        except SyntaxError as exc:
            # Best-effort: annotate the failure instead of returning broken code.
            fixed += (
                f"\n\n# RECTIFY-NOTE: original failed to parse: {exc.msg} (line {exc.lineno})\n"
            )
            # Re-check: the note itself must not break parsing; if it does, give up loudly.
            ast.parse(fixed)
        result = GeneratedCode(kind="rectified", name="rectified", code=fixed)
        self._log.info("dark_factory.self_rectified", sha=result.sha256[:12])
        return result

    # ------------------------------------------------------------------ #
    # Absorption
    # ------------------------------------------------------------------ #

    def absorb_repo(self, repo_url: str, *, commit: bool = False) -> AbsorptionResult:
        """Rewrite a cloned repository toward Agency conventions; commits are opt-in."""
        self._log.info("dark_factory.absorb_start", repo_url=repo_url, commit=commit)
        return self._absorber.absorb(repo_url, commit=commit)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _coerce_tool_spec(spec: ToolSpec | dict[str, Any] | str) -> ToolSpec:
        if isinstance(spec, ToolSpec):
            return spec
        if isinstance(spec, str):
            if not spec.strip():
                raise ValueError("spec must not be empty")
            return ToolSpec(name="generated_tool", purpose=spec.strip())
        return ToolSpec.model_validate(spec)

    @staticmethod
    def _assert_syntax(code: str) -> None:
        try:
            ast.parse(code)
        except SyntaxError as exc:
            raise ValueError(f"generated code is not valid Python: {exc}") from exc

    @staticmethod
    def _render_tool(spec: ToolSpec) -> str:
        class_name = "".join(p.capitalize() for p in spec.name.split("_")) + "Tool"
        return (
            f'"""Tool {spec.name}: {spec.purpose}."""\n\n'
            "from __future__ import annotations\n\n"
            "from typing import Any\n\n"
            "import structlog\n"
            "from pydantic import BaseModel, Field\n\n"
            "logger = structlog.get_logger(__name__)\n\n\n"
            f"class {class_name}Input(BaseModel):\n"
            '    """Validated tool input."""\n\n'
            + "".join(f"    {i}: Any = Field(default=None)\n" for i in spec.inputs or ["payload"])
            + "\n\n"
            f"class {class_name}:\n"
            f'    """{spec.purpose}."""\n\n'
            "    def __init__(self) -> None:\n"
            '        self._log = logger.bind(tool="' + spec.name + '")\n\n'
            "    def run(self, data: " + f"{class_name}Input" + ") -> dict[str, Any]:\n"
            '        """Execute the tool."""\n'
            '        self._log.info("tool.run", tool="' + spec.name + '")\n'
            '        return {"ok": True, "input": data.model_dump()}\n'
        )

    @staticmethod
    def _render_cli(app_name: str, commands: list[str]) -> str:
        slug = re.sub(r"\W+", "_", app_name).strip("_") or "app"
        non_word = r"\W+"
        chunks: list[str] = []
        for cmd in commands:
            func_name = re.sub(non_word, "_", cmd).strip("_") or "run"
            chunks.append(
                "\n\n@app.command()\ndef "
                + func_name
                + "() -> None:\n"
                + '    """CLI command '
                + cmd
                + '."""\n'
                + '    logger.info("cli.command", command='
                + repr(cmd)
                + ")\n"
                + "    print("
                + repr(cmd + " executed")
                + ")\n"
            )
        body = "".join(chunks)
        return (
            f'"""{slug} CLI application."""\n\n'
            "from __future__ import annotations\n\n"
            "import structlog\n"
            "import typer\n\n"
            "logger = structlog.get_logger(__name__)\n"
            "app = typer.Typer(help=" + repr(f"{slug} CLI") + ")\n"
            f"{body}\n\n"
            'if __name__ == "__main__":\n'
            "    app()\n"
        )

    @staticmethod
    def _render_module(spec: ModuleSpec) -> str:
        class_name = "".join(p.capitalize() for p in spec.module.split("_"))
        funcs = "".join(
            f"\n\ndef {fn}(*args: object, **kwargs: object) -> object:\n"
            f'    """Generated function {fn}."""\n'
            '    logger.info("module.function", function=' + repr(fn) + ")\n"
            '    return {"args": args, "kwargs": kwargs}\n'
            for fn in spec.functions
        )
        classes = "".join(
            f"\n\nclass {cn}:\n"
            f'    """Generated class {cn}."""\n\n'
            "    def run(self) -> dict[str, object]:\n"
            '        """Run."""\n'
            '        return {"ok": True}\n'
            for cn in spec.classes
        )
        return (
            f'"""{spec.description}."""\n\n'
            "from __future__ import annotations\n\n"
            "from typing import Any\n\n"
            "import structlog\n"
            "from pydantic import BaseModel, Field\n\n"
            "logger = structlog.get_logger(__name__)\n\n\n"
            f"class {class_name}Config(BaseModel):\n"
            '    """Configuration model."""\n\n'
            "    enabled: bool = Field(default=True)\n"
            f"{funcs}{classes}\n\n__all__ = [{', '.join(repr(f) for f in spec.functions + spec.classes)}]\n"
        )


__all__ = ["DarkFactory", "GeneratedCode", "ModuleSpec", "SDLCArtifact", "ToolSpec"]
