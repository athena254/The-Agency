# Tools Brief 1: Tool Core Types + Registry

You are building the tool layer foundation for The Agency.

## Context
Project: C:\Users\alphi\theagency\
Spec: C:\Users\alphi\theagency\docs\SPEC_TOOLS_RESEARCH.md (read sections 2.1-2.2)
Existing code style: study src/agency/agents/loop.py and src/agency/lattice/models.py for conventions (pydantic v2, structlog, `from __future__ import annotations`, dataclasses where frozen is wanted)
Existing audit log: src/agency/kernel/ (AuditLog class — check src/agency/kernel/audit.py or similar)

## Task
Create the core tool types and the ToolRegistry.

## Files to Create

### 1. `src/agency/tools/__init__.py`
Export: ToolSpec, ToolResult, ToolContext, ToolRisk, Tool, ToolRegistry, ToolError

### 2. `src/agency/tools/base.py`
- `ToolRisk(str, Enum)`: READ_ONLY, MUTATES_STATE, EXECUTES_CODE
- `ToolSpec` frozen dataclass: name (str), description (str), parameters (dict JSON Schema, default empty), risk (ToolRisk, default READ_ONLY). Validate: name non-empty, matches `^[a-z][a-z0-9_]*$`.
- `ToolContext` dataclass: agent_id (str), task_id (str), memory_store (Any, optional), sandbox_manager (Any, optional), lattice (Any, optional), audit (Any, optional), metadata (dict, default empty)
- `ToolResult(BaseModel)`: tool (str), ok (bool), output (Any, default None), error (str, default None), duration_ms (int, default 0), evidence (dict, default empty). Add `model_config = ConfigDict(extra="forbid")`.
- `Tool` Protocol: attribute `spec: ToolSpec`; method `async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult`
- `ToolError(Exception)` with `.tool` attribute — raised by registry on unknown tool or invalid args (callers may catch it; registry also returns error ToolResults where a loop expects them)

### 3. `src/agency/tools/registry.py`
- `ToolRegistry` class:
  - `__init__(self, audit: Any | None = None, default_timeout_s: float = 30.0)` — audit is an optional AuditLog-like object with async `append(agent, action, result, target, evidence)` method; if None, use structlog only
  - `register(self, tool: Tool) -> None` — raises ToolError on duplicate name
  - `get(self, name: str) -> Tool` — raises ToolError if unknown
  - `list_specs(self) -> list[ToolSpec]` — sorted by name
  - `async call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult`:
    1. Look up tool (unknown → ToolResult ok=False error="unknown tool: <name>")
    2. Validate args against spec.parameters JSON Schema — implement a small validator `_validate_args(spec, args)` supporting: type (string, integer, number, boolean, array, object), required keys, additionalProperties. Invalid → ok=False error describing the violation. Do NOT add jsonschema as a dependency.
    3. Execute `tool.run(args, ctx)` wrapped in `asyncio.wait_for(..., timeout=per-tool timeout)` — timeout default 30s, override via registry method `set_timeout(name, seconds)`
    4. Measure duration_ms, on success audit-log `tool.call` with agent_id/task_id/tool/ok, on failure audit-log `tool.error` with the error string
    5. Any exception from run() → ToolResult ok=False error=f"{type}: {msg}" (never raise to caller)
- Module-level `build_default_registry(**ctx_kwargs)` placeholder: returns an empty ToolRegistry with a docstring noting builtin tools are registered by agency.tools.builtin (brief 2 wires this; keep it importable WITHOUT importing builtin modules — accept an optional list of Tool instances to register instead)

### 4. `tests/test_tools_base.py`
Cover (~15 tests):
- ToolSpec validation (bad name rejected, good name accepted, frozen)
- ToolResult defaults and extra="forbid"
- Registry register/get/list_specs, duplicate registration raises
- call() with unknown tool → ok=False
- call() with schema violations: missing required, wrong type, unknown extra property (when additionalProperties false)
- call() success path (register a simple echo tool) — returns ok=True, output set, duration_ms >= 0
- call() with a tool that raises → ok=False, error contains exception type
- call() with a tool that sleeps past a short timeout → ok=False, error mentions timeout
- audit object receives entries (use a fake async audit recorder class in the test)
- set_timeout override respected

## Conventions
- Python 3.11+, pydantic v2, structlog for logging
- Use `from __future__ import annotations`
- Type hints everywhere; docstrings on public classes/methods
- Run: `PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_tools_base.py -v` — all tests must pass before you finish
- Also run `ruff check src/agency/tools/` and fix issues

## Do NOT modify any existing files; only create the new ones listed in this brief.
