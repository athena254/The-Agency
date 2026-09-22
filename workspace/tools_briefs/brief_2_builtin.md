# Tools Brief 2: Builtin Tools (web_search, web_fetch, memory_query, memory_write, sandbox_exec)

You are building the concrete tools for The Agency's tool layer.

## Context
Project: C:\Users\alphi\theagency\
Spec: C:\Users\alphi\theagency\docs\SPEC_TOOLS_RESEARCH.md (read section 2.3)
Core types: C:\Users\alphi\theagency\src\agency\tools\base.py (being built in parallel — import from `agency.tools.base`: ToolSpec, ToolResult, ToolContext, ToolRisk, Tool). If it does not exist yet, write your code against the interface described below and it will integrate:
  - ToolSpec(name, description, parameters, risk) — frozen dataclass
  - ToolResult(tool, ok, output, error, duration_ms, evidence) — pydantic model
  - ToolContext(agent_id, task_id, memory_store, sandbox_manager, lattice, audit, metadata) — dataclass
  - Tool Protocol: `.spec` attribute + `async def run(self, args, ctx) -> ToolResult`
Memory store: src/agency/memory/sms/store.py — `MemoryStore` with async `search(query: MemoryQuery) -> list[MemoryItem]`, `store(item: MemoryItem) -> MemoryItem`, `list_by_agent(agent_id, limit)`
Memory models: src/agency/memory/sms/models.py — `MemoryItem`, `MemoryTier`
Sandbox: src/agency/security/sandbox/manager.py — `SandboxManager` with `execute(sandbox_id, command) -> ExecutionResult`; study how sandboxes are created/destroyed first
httpx is already a project dependency (>=0.27)

## Task
Create 5 tool classes in `src/agency/tools/builtin/`.

## Files to Create

### 1. `src/agency/tools/builtin/__init__.py`
Export the 5 tool classes + `register_all(registry, ...)` helper that instantiates and registers each tool (memory tools need nothing at construction; they use ctx.memory_store at run time).

### 2. `src/agency/tools/builtin/web.py`
- `WebSearchTool` — spec name `web_search`, risk READ_ONLY, params: `query` (string, required), `max_results` (integer, optional, default 5)
  - GET `https://html.duckduckgo.com/html/?q=<urlencoded query>` with httpx.AsyncClient, timeout 15s, headers with a desktop User-Agent
  - Parse the HTML with `html.parser` (stdlib HTMLParser subclass — do NOT add bs4 dependency): extract result blocks (links class="result__a" and snippets class="result__snippet"). Return list of dicts {"title", "url", "snippet"}. DuckDuckGo HTML wraps URLs in a redirect (`//duckduckgo.com/l/?uddg=<urlencoded>`) — decode the `uddg` param back to the real URL.
  - Cap at max_results. On HTTP error / network error / empty parse → ok=False with clear error. On zero results but successful fetch → ok=True with empty list.
  - evidence: {"engine": "duckduckgo", "query": query, "result_count": N}
- `WebFetchTool` — spec name `web_fetch`, risk READ_ONLY, params: `url` (string, required, must start with http:// or https://)
  - httpx GET, timeout 15s, follow_redirects=True, cap response read at 200 KB (stream + read chunks, stop early)
  - Extract text: strip <script>/<style> blocks, then tags, collapse whitespace (stdlib HTMLParser-based extractor)
  - Return output dict {"url": final_url, "status": http_status, "text": first 8000 chars, "bytes_read": n}
  - evidence: {"url": final_url, "status": http_status, "bytes_read": n}
  - Non-2xx status → ok=False with status code in error
- Both tools must accept an optional `transport` constructor arg (httpx.AsyncClient transport, e.g. MockTransport) for testing — pass it to the AsyncClient when provided.

### 3. `src/agency/tools/builtin/memory.py`
- `MemoryQueryTool` — spec name `memory_query`, risk READ_ONLY, params: `query` (string, optional), `agent_id` (string, optional), `limit` (integer, optional, default 10)
  - If ctx.memory_store is None → ok=False "no memory store available"
  - With query: `await ctx.memory_store.search(MemoryQuery(query=query, limit=limit))` (check MemoryQuery's actual constructor in models.py and adapt). Without query but agent_id: list_by_agent. Neither → ok=False "query or agent_id required".
  - Output: list of {"id", "content", "tier", "agent_id", "created_at"} (map from MemoryItem fields — check the real model).
- `MemoryWriteTool` — spec name `memory_write`, risk MUTATES_STATE, params: `content` (string, required, min 1 char), `title` (string, optional)
  - Build MemoryItem (check its required fields — supply agent_id from ctx.agent_id, tier WORKING or the model's default) and store. Output: {"id": item.id}
  - evidence: {"memory_id": item.id}

### 4. `src/agency/tools/builtin/sandbox.py`
- `SandboxExecTool` — spec name `sandbox_exec`, risk EXECUTES_CODE, params: `code` (string, required), `language` (string, optional, default "python")
  - If ctx.sandbox_manager is None → ok=False "no sandbox manager available"
  - Study SandboxManager's API first: create sandbox (or reuse per-ctx sandbox id), write the code to a file, execute it, return {"stdout", "stderr", "exit_code", "duration"} from ExecutionResult fields (map the real fields)
  - Truncate stdout/stderr to 4000 chars each in output
  - Always destroy the sandbox if this tool created it (try/finally)
  - evidence: {"exit_code": ..., "language": ...}

### 5. `tests/test_tools_web.py`
- Use httpx.MockTransport to serve canned DuckDuckGo HTML (2 results with uddg-redirect links) and a canned article page
- Tests (~10): search parses results + decodes uddg URLs; search network error → ok=False; search zero results → ok=True empty; fetch extracts text, strips scripts, collapses whitespace; fetch 404 → ok=False; fetch caps at 200KB / 8000 chars; params schema (missing url rejected)

### 6. `tests/test_tools_memory.py`
- Real MemoryStore against a temp SQLite path (study how existing tests in tests/test_memory_sms.py construct it — copy that pattern)
- Tests (~8): query with text finds stored item; query with agent_id lists items; write stores and returns id; write with no memory store → ok=False; roundtrip write-then-query

### 7. `tests/test_tools_sandbox.py`
- Real SandboxManager process backend (study tests/test_security_sandbox.py for the pattern)
- Tests (~5): echo code returns stdout; failing code returns exit_code + stderr; no manager → ok=False; output truncation on large stdout

## Conventions
- Python 3.11+, pydantic v2, structlog, `from __future__ import annotations`
- NO new dependencies — stdlib + httpx + existing agency modules only
- Run: `PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_tools_web.py tests/test_tools_memory.py tests/test_tools_sandbox.py -v` — all must pass
- Also run `ruff check src/agency/tools/` and fix issues
- If src/agency/tools/base.py does not exist yet (parallel brief), create a MINIMAL placeholder at that exact path ONLY IF missing, containing exactly the types described in Context above, so your imports work. If it exists, do not touch it.

## Do NOT modify any existing files; only create the new ones listed in this brief.
