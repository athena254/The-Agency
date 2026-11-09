"""Forge ToolDesigner minimal: build + verify + register a single tool.

Worktree-less on purpose (KISS/YAGNI): there is no git checkout, no
merge queue, and no deployment here. The tool is materialized as a
single ``tool.py`` plus ``test_tool.py`` in an ephemeral temp dir and
run through the existing Forge modules:

* :func:`agency.forge.inspection.inspect_changes` — bounded inventory +
  Python syntax gate (via :func:`run_pipeline`).
* :func:`agency.forge.security_scan.scan_changes` — static scan for
  dangerous calls / secrets (via :func:`run_pipeline`).
* :func:`agency.forge.test_runner.run_tests` — pytest in an isolated
  subprocess copy (via :func:`run_pipeline`).

Only a ``PASS`` gate that also satisfies
:func:`agency.autonomy.toolforge.can_self_publish` (artifact sha256
equals manifest sha256) is instantiated and registered into a
:class:`~agency.tools.registry.ToolRegistry`.

Trust boundary: the generated code is executed in-process exactly once,
only *after* the Forge gates pass, to resolve the ``Tool`` object for
registration (``tool`` instance, ``Tool`` class, or a ``run()`` function
adapter). Forge's own rule still holds — inspection/scan never execute
code; tests run in the subprocess copy. This module is for internally
generated tools (the agency needs no external capability); never build
tools from untrusted input.
"""

from __future__ import annotations

import hashlib
import inspect
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agency.autonomy.toolforge import can_self_publish
from agency.forge.pipeline import run_pipeline
from agency.tools.base import ToolContext, ToolResult, ToolSpec

MAX_CODE_BYTES = 256 * 1024
MAX_TESTS_BYTES = 256 * 1024
_TOOL_FILE = "tool.py"
_TEST_FILE = "test_tool.py"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ToolBuildResult:
    """Outcome of :func:`build_tool`."""

    name: str
    gate: str
    sha256: str
    report_id: str = ""
    registered: bool = False
    detail: str = ""


def _check_name(name: str) -> str:
    if not isinstance(name, str) or _NAME_RE.match(name) is None:
        raise ValueError(f"invalid tool name {name!r}: must match ^[a-z][a-z0-9_]*$.")
    return name


def _check_source(text: str, kind: str, limit: int) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{kind} must be a nonblank string.")
    if len(text.encode("utf-8")) > limit:
        raise ValueError(f"{kind} exceeds {limit} bytes.")
    return text


def _call_run(fn: Any, args: dict[str, Any], ctx: ToolContext) -> Any:
    result = fn(args, ctx)
    return result


class _FunctionTool:
    """Adapter exposing a plain ``run(args, ctx)`` function as a Tool."""

    def __init__(self, name: str, fn: Any) -> None:
        self.spec = ToolSpec(
            name=name,
            description=f"Forge-built tool {name}",
            parameters={},
        )
        self._fn = fn

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        result = _call_run(self._fn, args, ctx)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, ToolResult):
            if not result.tool:
                result.tool = self.spec.name
            return result
        return ToolResult(tool=self.spec.name, ok=True, output=result)


def _resolve_tool(code: str, name: str) -> Any:
    """Resolve a Tool object from generated code (post-gate, trusted only)."""
    namespace: dict[str, Any] = {"__name__": f"forge_tool_{name}", "__file__": _TOOL_FILE}
    exec(compile(code, _TOOL_FILE, "exec"), namespace)  # noqa: S102 - trusted post-gate code
    for key in ("tool", "Tool", "TOOL"):
        candidate = namespace.get(key)
        if candidate is None:
            continue
        if isinstance(candidate, type):
            try:
                candidate = candidate()
            except TypeError:
                continue
        spec = getattr(candidate, "spec", None)
        if spec is not None and callable(getattr(candidate, "run", None)):
            if getattr(spec, "name", None) != name:
                raise ValueError(f"tool spec name {spec.name!r} != {name!r} (fail closed).")
            return candidate
    for value in namespace.values():
        if value is None or isinstance(value, type):
            continue
        spec = getattr(value, "spec", None)
        if (
            spec is not None
            and getattr(spec, "name", None) == name
            and callable(getattr(value, "run", None))
        ):
            return value
    fn = namespace.get("run")
    if callable(fn):
        return _FunctionTool(name, fn)
    raise ValueError("tool code must define a tool instance, Tool class, or run() function.")


def build_tool(
    name: str,
    code: str,
    tests: str,
    registry: Any = None,
    *,
    timeout_s: int = 120,
) -> ToolBuildResult:
    """Build a tool through Forge gates; register iff PASS + self-publish.

    ``registry`` is an optional :class:`ToolRegistry`. When omitted, the
    tool is only verified (nothing is registered). Registration is
    idempotent: if ``name`` is already registered, no duplicate is
    attempted.
    """
    _check_name(name)
    _check_source(code, "code", MAX_CODE_BYTES)
    _check_source(tests, "tests", MAX_TESTS_BYTES)
    artifact_sha = hashlib.sha256(code.encode("utf-8")).hexdigest()
    with tempfile.TemporaryDirectory(prefix="forge-auto-") as tmp:
        root = Path(tmp)
        tool_path = root / _TOOL_FILE
        test_path = root / _TEST_FILE
        tool_path.write_bytes(code.encode("utf-8"))
        test_path.write_bytes(tests.encode("utf-8"))
        manifest_sha = hashlib.sha256(tool_path.read_bytes()).hexdigest()
        report = run_pipeline(
            root,
            [_TOOL_FILE],
            run_tests_flag=True,
            test_paths=[_TEST_FILE],
            test_timeout_s=timeout_s,
        )
    if report.gate != "PASS":
        return ToolBuildResult(
            name=name,
            gate=report.gate,
            sha256=artifact_sha,
            report_id=report.report_id,
            registered=False,
            detail="forge gate failed; tool not registered",
        )
    if not can_self_publish(report.gate, artifact_sha, manifest_sha):
        return ToolBuildResult(
            name=name,
            gate=report.gate,
            sha256=artifact_sha,
            report_id=report.report_id,
            registered=False,
            detail="sha mismatch; self-publish refused",
        )
    if registry is None:
        return ToolBuildResult(
            name=name,
            gate=report.gate,
            sha256=artifact_sha,
            report_id=report.report_id,
            registered=False,
            detail="verified; no registry supplied",
        )
    try:
        existing = {spec.name for spec in registry.list_specs()}
    except Exception:  # noqa: BLE001 - idempotency probe must not crash builds
        existing = set()
    if name in existing:
        return ToolBuildResult(
            name=name,
            gate=report.gate,
            sha256=artifact_sha,
            report_id=report.report_id,
            registered=True,
            detail="already registered (idempotent)",
        )
    tool = _resolve_tool(code, name)
    registry.register(tool)
    return ToolBuildResult(
        name=name,
        gate=report.gate,
        sha256=artifact_sha,
        report_id=report.report_id,
        registered=True,
        detail="registered",
    )


__all__ = [
    "MAX_CODE_BYTES",
    "MAX_TESTS_BYTES",
    "ToolBuildResult",
    "build_tool",
]
