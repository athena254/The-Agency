"""Forge MCP server — expose the Forge factory over MCP (stdio or HTTP).

The Agency already *consumes* MCP servers (``agency/tools/mcp_bridge.py``). This
module is the other direction: it exposes the Forge's own capabilities as MCP
tools so an external agent (Claude Code, Codex, opencode, a remote client) can
drive a governed build without shelling into this machine.

Design constraints (all deliberate, all enforced):

- **Stdlib JSON-RPC only.** No ``mcp`` / ``fastmcp`` dependency is added; the
  wire protocol is ~200 lines and the surface stays reviewable. The same tool
  set is served over stdio (local agents) and streamable HTTP (remote agents).
- **The bridge stays thin.** Every tool is a bounded wrapper over the existing
  pure Forge functions (``inspect_changes``, ``scan_changes``, ``run_pipeline``,
  ``run_review``, ``BlacksmithOps``, ``build_from_spec``, ...). No new verdict
  logic lives here: a PASS from MCP is a PASS from the same pipeline.
- **Fail closed on authority.** The HTTP transport requires a bearer token
  configured out-of-band (``AGENCY_MCP_TOKEN``); no token, no server. stdio has
  no auth by nature of the transport (the caller already owns this process).
- **Never authorizes merge/deploy/release.** Reports always carry the Forge
  caveats, and ``forge_ship`` is not exposed.
- **Bounded.** Task/spec text is capped, changed paths are capped, one job per
  request, and every mutating call carries the cost of the real runner it wraps.

Caveats are data: each response repeats the applicable caveat list so a caller
cannot mistake a PASS for a release authorization.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sys
import threading
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from fastapi import Request as _McpRequest
    from fastapi.responses import JSONResponse as _McpJSONResponse

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "agency-forge"
SERVER_VERSION = "0.3.0"

MAX_TEXT_CHARS = 24_000
MAX_PATHS = 200
MAX_TOOL_NAME_CHARS = 96
MAX_CALLS_PER_REQUEST = 20

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

CAVEATS = (
    "tests_not_run",
    "security_review_not_run",
    "release_not_authorized",
)


class McpError(Exception):
    """A JSON-RPC error with a stable public code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _err(code: int, message: str) -> McpError:
    return McpError(code, message)


# --------------------------------------------------------------------------- #
# Tool definitions (name, description, JSON Schema)
# --------------------------------------------------------------------------- #


class ToolDef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if _TOOL_NAME_RE.match(value) is None:
            raise ValueError(f"invalid MCP tool name {value!r}")
        return value


def _text_field(max_chars: int = MAX_TEXT_CHARS, required: bool = True) -> dict[str, Any]:
    field_schema: dict[str, Any] = {"type": "string", "maxLength": max_chars}
    return field_schema


def _schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


def _paths_prop() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "string", "maxLength": 512},
        "maxItems": MAX_PATHS,
    }


TOOL_DEFS: tuple[ToolDef, ...] = (
    ToolDef(
        name="forge_status",
        description=(
            "Read-only queue picture from the Blacksmith Forge job database "
            "(job counts by state, blocked jobs, recent jobs). No authority."
        ),
        input_schema=_schema({"db": {"type": "string"}, "repo": {"type": "string"}}, []),
    ),
    ToolDef(
        name="forge_inspect",
        description=(
            "Read-only file inventory gate: syntax-check the changed Python files "
            "under a repository root and return PASS/FAIL plus per-file records. "
            "Never executes the code."
        ),
        input_schema=_schema(
            {"root": {"type": "string"}, "changed_paths": _paths_prop()}, ["root", "changed_paths"]
        ),
    ),
    ToolDef(
        name="forge_security_scan",
        description=(
            "Read-only heuristic security scan over changed paths (eval/exec, "
            "subprocess, pickle, hardcoded secrets). PASS means no HIGH/CRITICAL "
            "finding; it is not a certified security review."
        ),
        input_schema=_schema(
            {"root": {"type": "string"}, "changed_paths": _paths_prop()}, ["root", "changed_paths"]
        ),
    ),
    ToolDef(
        name="forge_run_tests",
        description=(
            "Run the repository's tests in a temporary copy (never mutates the "
            "repo) with a wall-clock timeout. Returns PASS/FAIL/SKIP/ERROR/TIMEOUT."
        ),
        input_schema=_schema(
            {
                "root": {"type": "string"},
                "test_paths": _paths_prop(),
                "timeout_s": {"type": "integer", "minimum": 1, "maximum": 300},
            },
            ["root"],
        ),
    ),
    ToolDef(
        name="forge_review",
        description=(
            "Review the changed diff: deterministic heuristics plus, when an LLM "
            "provider is configured, an LLM judge with the diff in context. "
            "Returns findings, score and gate. Does not authorize merge."
        ),
        input_schema=_schema(
            {
                "root": {"type": "string"},
                "changed_paths": _paths_prop(),
                "use_llm": {"type": "boolean"},
                "threshold": {"type": "integer", "minimum": 0, "maximum": 5},
            },
            ["root", "changed_paths"],
        ),
    ),
    ToolDef(
        name="forge_pipeline",
        description=(
            "Compose the full Forge verdict for a change set: inspection + "
            "security + optional tests + review in one report. This is the gate "
            "an external agent should call before claiming work is done."
        ),
        input_schema=_schema(
            {
                "root": {"type": "string"},
                "changed_paths": _paths_prop(),
                "run_tests": {"type": "boolean"},
                "run_review": {"type": "boolean"},
                "test_paths": _paths_prop(),
            },
            ["root", "changed_paths"],
        ),
    ),
    ToolDef(
        name="forge_build",
        description=(
            "Build a spec end to end: run a coding worker in an isolated git "
            "worktree, then verify with the real Forge pipeline. Expensive and "
            "slow (minutes); prefer forge_pipeline on a change set you already "
            "have. Never merges and never edits the original checkout."
        ),
        input_schema=_schema(
            {
                "repo": {"type": "string"},
                "spec": _text_field(),
                "model": {"type": "string"},
                "run_tests": {"type": "boolean"},
            },
            ["repo", "spec"],
        ),
    ),
    ToolDef(
        name="forge_submit_job",
        description=(
            "Submit one bounded build job to the Blacksmith queue (QUEUED). "
            "Does not dispatch it; a human or the Blacksmith loop does that."
        ),
        input_schema=_schema(
            {
                "db": {"type": "string"},
                "title": {"type": "string", "maxLength": 200},
                "task": _text_field(8000),
                "repo": {"type": "string"},
                "acceptance": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                "test_paths": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                "language": {"type": "string"},
            },
            ["db", "title", "task", "repo"],
        ),
    ),
    ToolDef(
        name="forge_get_job",
        description="Read one Forge job by id (state, attempts, verdict, caveats).",
        input_schema=_schema({"db": {"type": "string"}, "job_id": {"type": "string"}}, ["job_id"]),
    ),
    ToolDef(
        name="forge_list_reports",
        description="List stored Forge reports (pipeline / external-agent / review) by kind.",
        input_schema=_schema(
            {"db": {"type": "string"}, "kind": {"type": "string"}}, ["db", "kind"]
        ),
    ),
    ToolDef(
        name="forge_metrics",
        description="Operational metrics computed from the Forge job database (read-only).",
        input_schema=_schema({"db": {"type": "string"}}, []),
    ),
    ToolDef(
        name="forge_capabilities",
        description=(
            "Report what this MCP server exposes and the honest limits of the "
            "Forge (no merge authority, no release authority, reviewer "
            "calibration pending)."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
)

_TOOL_BY_NAME = {tool.name: tool for tool in TOOL_DEFS}


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #

_SYNC_TOOLS = frozenset(
    {
        "forge_status",
        "forge_inspect",
        "forge_security_scan",
        "forge_run_tests",
        "forge_review",
        "forge_pipeline",
        "forge_build",
        "forge_submit_job",
        "forge_get_job",
        "forge_list_reports",
        "forge_metrics",
        "forge_capabilities",
    }
)


class McpConfig(BaseModel):
    """Configuration for the Forge MCP surface."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str = ""
    default_db_dir: str = "."
    max_concurrent_calls: int = 4

    @field_validator("token")
    @classmethod
    def _token(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("token must be NUL-free")
        return value


class ForgeMcpServer:
    """JSON-RPC dispatcher for the Forge tool surface.

    Transport-agnostic: the same instance serves stdio and HTTP. Callers hand
    it a decoded ``tools/call`` params dict and get a result dict back.
    """

    def __init__(self, config: McpConfig | None = None) -> None:
        self.config = config or McpConfig()
        self._lock = threading.RLock()
        self._calls_in_flight = 0
        self._semaphore = threading.BoundedSemaphore(self.config.max_concurrent_calls)

    # -- discovery ---------------------------------------------------------- #

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in TOOL_DEFS
        ]

    def server_info(self) -> dict[str, Any]:
        return {"name": SERVER_NAME, "version": SERVER_VERSION}

    def protocol_version(self) -> str:
        return PROTOCOL_VERSION

    # -- dispatch ----------------------------------------------------------- #

    async def handle(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Handle one JSON-RPC message; returns a response dict or None for notifications."""
        if not isinstance(payload, dict):
            raise _err(-32600, "invalid request")
        rpc_id = payload.get("id")
        method = payload.get("method")
        if not isinstance(method, str) or not method:
            raise _err(-32600, "invalid request")
        params = payload.get("params")
        if params is not None and not isinstance(params, dict):
            raise _err(-32600, "invalid params")
        params = params or {}

        result: dict[str, Any]
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": self.server_info(),
            }
        elif method == "notifications/initialized":
            return None
        elif method == "tools/list":
            result = {"tools": self.tools()}
        elif method == "tools/call":
            result = await self._call_tool(params)
        elif method == "ping":
            result = {}
        else:
            raise _err(-32601, f"method not found: {method}")

        if rpc_id is None:
            return None
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}

    async def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(name, str) or name not in _TOOL_BY_NAME:
            raise _err(-32602, f"unknown tool: {name!r}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise _err(-32602, "arguments must be an object")
        tool = _TOOL_BY_NAME[name]
        _validate_args(tool, arguments)

        if not self._semaphore.acquire(blocking=False):
            raise _err(-32000, "too many concurrent calls")
        try:
            if name in _SYNC_TOOLS:
                output = await asyncio.to_thread(self._run_tool, name, arguments)
            else:  # pragma: no cover - defensive
                output = self._run_tool(name, arguments)
        finally:
            self._semaphore.release()

        return {
            "content": [{"type": "text", "text": _dumps(output)}],
            "structuredContent": _sanitize(output),
            "isError": bool(output.get("ok") is False),
        }

    # -- implementations ---------------------------------------------------- #

    def _run_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one tool handler; a failure becomes ``ok=False`` tool content.

        A handler that raises (bad root, relative path, missing config) must not
        leak a traceback to an MCP client and must not abort the session: MCP
        models a failing tool as a normal response with ``isError: true``. The
        exception class is named for diagnosis; the message never carries
        internals.
        """
        handler = getattr(self, f"_t_{name}")
        with self._lock:
            self._calls_in_flight += 1
        try:
            return dict(handler(arguments))
        except Exception as exc:  # noqa: BLE001 - fail closed, never leak internals
            return {
                "ok": False,
                "error": f"{name} failed: {type(exc).__name__}",
                "caveats": ["call_failed"],
            }
        finally:
            with self._lock:
                self._calls_in_flight -= 1

    @staticmethod
    def _abs(path: str, *, field: str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            raise _err(-32602, f"{field} must be an absolute path")
        return candidate

    def _t_forge_status(self, args: dict[str, Any]) -> dict[str, Any]:
        from agency.forge.orchestration import BlacksmithOps

        db = str(args.get("db") or "")
        ops = BlacksmithOps(db) if db else BlacksmithOps(":memory:")
        try:
            status = ops.status()
        finally:
            ops.close()
        return {"ok": True, "status": status.model_dump(mode="python"), "caveats": list(CAVEATS)}

    def _t_forge_inspect(self, args: dict[str, Any]) -> dict[str, Any]:
        from agency.forge.inspection import inspect_changes

        root = self._abs(str(args["root"]), field="root")
        paths = [str(p) for p in args["changed_paths"]]
        report = inspect_changes(root, paths)
        return {"ok": report.gate == "PASS", "report": report.model_dump(mode="python")}

    def _t_forge_security_scan(self, args: dict[str, Any]) -> dict[str, Any]:
        from agency.forge.security_scan import scan_changes

        root = self._abs(str(args["root"]), field="root")
        paths = [str(p) for p in args["changed_paths"]]
        report = scan_changes(root, paths)
        return {
            "ok": report.gate == "PASS",
            "report": report.model_dump(mode="python"),
            "caveats": ["heuristic_only", "not_a_certified_review"],
        }

    def _t_forge_run_tests(self, args: dict[str, Any]) -> dict[str, Any]:
        from agency.forge.test_runner import run_tests

        root = self._abs(str(args["root"]), field="root")
        paths = [str(p) for p in args.get("test_paths", [])]
        timeout_s = int(args.get("timeout_s", 120))
        report = run_tests(root, paths, timeout_s=timeout_s)
        return {
            "ok": report.gate == "PASS",
            "report": report.model_dump(mode="python"),
            "caveats": ["bounded_isolated_run", "not_full_ci"],
        }

    def _t_forge_review(self, args: dict[str, Any]) -> dict[str, Any]:
        from agency.forge.reviewer import run_review

        root = self._abs(str(args["root"]), field="root")
        paths = [str(p) for p in args["changed_paths"]]
        use_llm = bool(args.get("use_llm", True))
        threshold = int(args.get("threshold", 4))
        report = run_review(root, paths, use_llm=use_llm, threshold=threshold)
        return {
            "ok": report.gate == "PASS",
            "report": report.model_dump(mode="python"),
            "caveats": ["reviewer_calibration_pending"],
        }

    def _t_forge_pipeline(self, args: dict[str, Any]) -> dict[str, Any]:
        from agency.forge.pipeline import run_pipeline

        root = self._abs(str(args["root"]), field="root")
        paths = [str(p) for p in args["changed_paths"]]
        report = run_pipeline(
            root,
            paths,
            run_tests_flag=bool(args.get("run_tests", False)),
            test_paths=[str(p) for p in args.get("test_paths", [])],
            run_review_flag=bool(args.get("run_review", True)),
        )
        return {
            "ok": report.gate == "PASS",
            "report": report.model_dump(mode="python"),
            "caveats": list(report.caveats),
        }

    def _t_forge_build(self, args: dict[str, Any]) -> dict[str, Any]:
        from agency.tui.forge_app import BUILD_BRANCH, build_from_spec

        repo = self._abs(str(args["repo"]), field="repo")
        spec = str(args["spec"])
        result = build_from_spec(
            repo,
            spec,
            model=str(args.get("model", "")) or "opencode/beam-free",
            run_tests_flag=bool(args.get("run_tests", True)),
        )
        return {
            "ok": result.gate == "PASS",
            "build": result.model_dump(mode="python"),
            "branch": BUILD_BRANCH,
            "caveats": list(CAVEATS),
        }

    def _t_forge_submit_job(self, args: dict[str, Any]) -> dict[str, Any]:
        from agency.forge.orchestration import BlacksmithOps

        db = str(args["db"])
        ops = BlacksmithOps(db)
        try:
            job = ops.submit(
                str(args["title"]),
                str(args["task"]),
                str(args["repo"]),
                acceptance=tuple(str(a) for a in args.get("acceptance", ())),
                test_paths=tuple(str(t) for t in args.get("test_paths", ())),
                language=str(args.get("language", "")),
            )
        finally:
            ops.close()
        return {
            "ok": True,
            "job": job.model_dump(mode="python"),
            "caveats": ["queued_not_dispatched"],
        }

    def _t_forge_get_job(self, args: dict[str, Any]) -> dict[str, Any]:
        from agency.forge.orchestration import BlacksmithOps

        db = str(args.get("db") or "")
        job_id = str(args["job_id"])
        ops = BlacksmithOps(db) if db else BlacksmithOps(":memory:")
        try:
            job = ops.get(job_id)
        except KeyError:
            return {"ok": False, "error": f"unknown job {job_id!r}"}
        finally:
            ops.close()
        return {"ok": True, "job": job.model_dump(mode="python")}

    def _t_forge_list_reports(self, args: dict[str, Any]) -> dict[str, Any]:
        kind = str(args["kind"])
        db = str(args["db"])
        loaders = {
            "pipeline": ("agency.forge.pipeline", "ForgePipelineStore"),
            "external_agent": ("agency.forge.external_agent", "ExternalAgentStore"),
            "review": ("agency.forge.reviewer", "ReviewStore"),
        }
        if kind not in loaders:
            raise _err(-32602, f"kind must be one of {sorted(loaders)}")
        import importlib

        module_name, class_name = loaders[kind]
        store_cls = getattr(importlib.import_module(module_name), class_name)
        store = store_cls(db)
        try:
            rows = store.list_reports()
        finally:
            store.close()
        return {
            "ok": True,
            "reports": [row.model_dump(mode="python") for row in rows][:50],
            "count": len(rows),
        }

    def _t_forge_metrics(self, args: dict[str, Any]) -> dict[str, Any]:
        from agency.forge.metrics import compute_metrics

        db = str(args.get("db") or "")
        metrics = compute_metrics(db) if db else compute_metrics(":memory:")
        return {"ok": True, "metrics": metrics.model_dump(mode="python")}

    def _t_forge_capabilities(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "server": self.server_info(),
            "protocol": PROTOCOL_VERSION,
            "tools": [tool.name for tool in TOOL_DEFS],
            "limits": {
                "max_text_chars": MAX_TEXT_CHARS,
                "max_paths": MAX_PATHS,
                "max_test_timeout_s": 300,
                "max_concurrent_calls": self.config.max_concurrent_calls,
            },
            "authority": {
                "merge": "not_authorized",
                "deploy": "not_authorized",
                "release": "not_authorized",
                "ship_exposed": False,
            },
            "caveats": list(CAVEATS) + ["reviewer_calibration_pending"],
        }


# --------------------------------------------------------------------------- #
# Argument validation (bounded, fail-closed)
# --------------------------------------------------------------------------- #


def _validate_args(tool: ToolDef, args: dict[str, Any]) -> None:
    schema = tool.input_schema
    allowed = set(schema.get("properties", {}))
    missing = [key for key in schema.get("required", []) if key not in args]
    extra = sorted(set(args) - allowed)
    if missing:
        raise _err(-32602, f"missing required argument(s): {', '.join(missing)}")
    if extra:
        raise _err(-32602, f"unexpected argument(s): {', '.join(extra)}")
    for key, value in args.items():
        spec = schema.get("properties", {}).get(key, {})
        _check_type(tool.name, key, value, spec)


def _check_type(tool_name: str, key: str, value: Any, spec: dict[str, Any]) -> None:
    expected = spec.get("type")
    if expected == "string":
        if not isinstance(value, str):
            raise _err(-32602, f"{tool_name}.{key} must be a string")
        max_chars = spec.get("maxLength")
        if isinstance(max_chars, int) and len(value) > max_chars:
            raise _err(-32602, f"{tool_name}.{key} exceeds {max_chars} characters")
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _err(-32602, f"{tool_name}.{key} must be an integer")
        if "minimum" in spec and value < spec["minimum"]:
            raise _err(-32602, f"{tool_name}.{key} below minimum")
        if "maximum" in spec and value > spec["maximum"]:
            raise _err(-32602, f"{tool_name}.{key} above maximum")
    elif expected == "boolean":
        if type(value) is not bool:
            raise _err(-32602, f"{tool_name}.{key} must be a boolean")
    elif expected == "array":
        if not isinstance(value, list):
            raise _err(-32602, f"{tool_name}.{key} must be an array")
        max_items = spec.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            raise _err(-32602, f"{tool_name}.{key} exceeds {max_items} items")
        item_spec = spec.get("items", {})
        for index, item in enumerate(value):
            _check_type(tool_name, f"{key}[{index}]", item, item_spec)
    elif expected == "object":
        if not isinstance(value, dict):
            raise _err(-32602, f"{tool_name}.{key} must be an object")


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #


def _load_token() -> str:
    token = os.environ.get("AGENCY_MCP_TOKEN", "")
    if not token:
        token_path = os.environ.get("AGENCY_MCP_TOKEN_FILE", "")
        if token_path:
            try:
                token = Path(token_path).read_text(encoding="utf-8").strip()
            except OSError:
                token = ""
    return token


def _json_default(value: Any) -> str | list[str] | dict[str, Any]:
    """Serialize pydantic/Forge report values (datetimes, enums, models, sets).

    Plain ``json.dumps`` cannot encode the datetimes and nested models inside a
    Forge report; this is used as the encoder's ``default`` hook so the same
    ``structuredContent`` is both JSON-safe and byte-identical to its text form.
    """
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return dict(value.model_dump(mode="python"))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(str(item) for item in value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=_json_default)


def _sanitize(value: Any, _depth: int = 0) -> Any:
    """Return a copy of *value* that plain ``json.dumps`` can always encode.

    The payload has already been through :func:`_dumps`; re-parsing it yields
    only JSON-native types, which is what makes the final encode safe without a
    custom ``default`` hook. Depth-bounded because reports are finite trees.
    """
    if _depth > 32:  # pragma: no cover - defensive against pathological input
        raise McpError(-32603, "payload nesting too deep")
    if isinstance(value, dict):
        return {str(key): _sanitize(item, _depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, _depth + 1) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date, time, Path)):
        return str(value)
    return str(value)


def serve_stdio(config: McpConfig | None = None) -> int:
    """Serve MCP over stdio (one JSON-RPC message per line). Local agents only.

    structlog's default processor writes human-readable lines to STDOUT, which
    would corrupt the JSON-RPC stream. Route logs to STDERR for the lifetime of
    this server so stdout stays machine-parseable.
    """
    import structlog

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    server = ForgeMcpServer(config)
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            _emit(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
            )
            continue
        try:
            response = asyncio.run(server.handle(payload))
        except McpError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": payload.get("id") if isinstance(payload, dict) else None,
                "error": {"code": exc.code, "message": exc.message},
            }
        except Exception as exc:  # noqa: BLE001 - a crash must become a JSON-RPC error
            response = {
                "jsonrpc": "2.0",
                "id": payload.get("id") if isinstance(payload, dict) else None,
                "error": {"code": -32603, "message": f"internal error: {type(exc).__name__}"},
            }
        if response is not None:
            _emit(response)
    return 0


def _emit(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, default=_json_default) + "\n")
    sys.stdout.flush()


def create_http_app(config: McpConfig | None = None) -> Any:
    """Create a FastAPI app exposing the same surface over streamable HTTP.

    Requires a bearer token (``config.token`` or ``AGENCY_MCP_TOKEN``); with no
    token the factory refuses to build rather than serving unauthenticated.
    """
    import fastapi

    cfg = config or McpConfig()
    token = cfg.token or _load_token()
    if not token:
        raise RuntimeError(
            "refusing to serve Forge MCP over HTTP without AGENCY_MCP_TOKEN "
            "(set it in the environment or pass McpConfig(token=...))"
        )

    # ``from __future__ import annotations`` at module scope turns every local
    # annotation into a string resolved against this function's globals, so a
    # function-local ``from fastapi import Request`` never reaches FastAPI's
    # resolver and the request parameter is misread as a query param. Bind the
    # real classes to module-level aliases instead.
    globals()["_McpRequest"] = fastapi.Request
    globals()["_McpJSONResponse"] = fastapi.responses.JSONResponse
    json_response = fastapi.responses.JSONResponse

    server = ForgeMcpServer(cfg)
    app = fastapi.FastAPI(title=SERVER_NAME, version=SERVER_VERSION)

    @app.post("/mcp")
    async def mcp_endpoint(request: _McpRequest) -> _McpJSONResponse:
        # The body is read manually (not as a typed parameter) so a malformed or
        # oversized request is answered with our JSON-RPC error, never FastAPI's
        # default 422 validation page.
        auth = request.headers.get("authorization", "")
        if not secrets.compare_digest(auth, f"Bearer {token}"):
            return json_response(status_code=401, content={"error": "unauthorized"})
        raw = await request.body()
        if len(raw) > 64 * 1024:
            return json_response(
                status_code=413,
                content={
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "payload too large"},
                },
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            return json_response(
                status_code=400,
                content={
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "parse error"},
                },
            )
        try:
            response = await server.handle(payload)
        except McpError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": payload.get("id") if isinstance(payload, dict) else None,
                "error": {"code": exc.code, "message": exc.message},
            }
        except Exception as exc:  # noqa: BLE001
            response = {
                "jsonrpc": "2.0",
                "id": payload.get("id") if isinstance(payload, dict) else None,
                "error": {"code": -32603, "message": f"internal error: {type(exc).__name__}"},
            }
        return json_response(content=response if response is not None else {})

    @app.get("/health")
    async def health() -> _McpJSONResponse:
        return json_response(
            content={"ok": True, "server": server.server_info(), "tools": len(TOOL_DEFS)}
        )

    return app


__all__ = [
    "CAVEATS",
    "MAX_PATHS",
    "MAX_TEXT_CHARS",
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "TOOL_DEFS",
    "ForgeMcpServer",
    "McpConfig",
    "McpError",
    "create_http_app",
    "serve_stdio",
]
