# The Agency — Tool Layer & Research Agent (MVP Capability) — Build Spec

**Status:** DRAFT — awaiting approval
**Date:** 2026-09-22
**Goal:** Make agents *capable*, not just conversational. Today an agent's
"action" is one LLM call that returns text. This spec adds a real tool layer
(web search, web fetch, memory, sandboxed code execution, agent-to-agent
messaging) wired into the agent loop, plus one domain agent — **ResearchAgent**
— that uses the tools end-to-end from Telegram: plan → act with tools →
verify → store findings as evidence → report with citations.

---

## 1. Purpose & Scope

**In scope:**
1. `src/agency/tools/` — tool registry + 5 concrete tools + LLM tool-calling driver
2. ResearchAgent (`src/agency/agents/research/`) — tool-using domain agent
3. Orchestrator wiring — tool loop replaces bare LLM call for tool-capable tasks
4. Telegram UX — `/research <topic>` command + natural-language routing to research domain
5. Tests for everything above

**Out of scope (explicitly):** Buddy UI, Finance/Business/Coding/Personal
agents, Neo4j/Qdrant backends, Dream/CPR/Aether, Docker sandbox backend
improvements, MCP protocol support.

**Design principle — RPC pattern:** every tool call is a named function + JSON
args + serialized result (ToolRegistry paper, arXiv 2507.10593; matches
OpenAI function-calling convention). Tools are stateless callables behind a
uniform `Tool` interface: name, description, JSON-schema params, async
callable, risk metadata.

---

## 2. Tool Layer Architecture

### 2.1 Core types (`src/agency/tools/base.py` — NEW)

```python
class ToolRisk(str, Enum):
    READ_ONLY = "read_only"      # search, fetch, memory query — no side effects
    MUTATES_STATE = "mutates"    # memory write, agent spawn
    EXECUTES_CODE = "executes"   # sandbox exec — needs risk engine sign-off

@dataclass(frozen=True)
class ToolSpec:
    name: str                    # "web_search"
    description: str             # shown to the LLM
    parameters: dict[str, Any]   # JSON Schema for args
    risk: ToolRisk = ToolRisk.READ_ONLY

class ToolResult(BaseModel):
    tool: str
    ok: bool
    output: Any = None
    error: str | None = None
    duration_ms: int = 0
    evidence: dict[str, Any] = {}   # urls fetched, citations, bytes read

class Tool(Protocol):
    spec: ToolSpec
    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...
```

`ToolContext` (dataclass): `agent_id`, `task_id`, `memory_store`, `sandbox_manager`,
`lattice`, `audit` — injected by the executor, never global.

### 2.2 Registry (`src/agency/tools/registry.py` — NEW)

- `ToolRegistry`: `register(tool)`, `get(name)`, `list_specs() -> list[ToolSpec]`,
  `async call(name, args, ctx) -> ToolResult`
- Registry enforces: unknown tool → `ToolResult(ok=False, error="unknown tool")`,
  schema validation of args (jsonschema-lite: type + required checks), per-tool
  timeout (default 30s), every call audit-logged with agent_id + task_id.
- `build_default_registry(ctx_deps)` factory: returns registry with the 5 tools.

### 2.3 The 5 MVP tools

| Tool | Risk | Implementation |
|------|------|----------------|
| `web_search` | READ_ONLY | DuckDuckGo HTML endpoint (`https://html.duckduckgo.com/html/?q=...`) via httpx, parse top 5 results (title, url, snippet). No API key. Fallback: Pollinations `openai` model asked to return JSON links. |
| `web_fetch` | READ_ONLY | httpx GET, 15s timeout, max 200 KB, text-only extraction (strip tags via regex/`html.parser`), returns first 8,000 chars + final URL + HTTP status. |
| `memory_query` | READ_ONLY | Wraps existing `MemoryStore.search(MemoryQuery)` + `list_by_agent`. |
| `memory_write` | MUTATES_STATE | Wraps `MemoryStore.store(MemoryItem)`; tier=WORKING, agent_id from ctx. |
| `sandbox_exec` | EXECUTES_CODE | Wraps existing `SandboxManager.execute` (process backend); timeout + output caps from sandbox config. |

### 2.4 LLM tool-calling driver (`src/agency/tools/driver.py` — NEW)

The bridge between the LLM and the registry. Providers differ in tool-calling
support, so the driver uses a **unified JSON protocol** that works with ANY
text-completion LLM (including free Pollinations):

1. System prompt lists tools as JSON specs.
2. LLM responds either `{"action": {"name": ..., "args": {...}}}` or `{"final": "answer text"}`.
3. Driver parses (robust JSON extraction: first `{...}` block, tolerant of
   code fences), calls `registry.call`, appends observation, loops.
4. Max 6 tool iterations, then forced `{"final": ...}`.
5. Returns `ToolLoopResult`: final answer, steps (tool, args, ok, duration),
   evidence aggregates (urls, sources cited), total LLM calls.

**Why JSON protocol over native function calling:** Pollinations/Nous free
tier has no function-calling API. The JSON protocol is provider-agnostic —
same driver works when you later switch to OpenRouter/Anthropic native
tools. (Native function-calling can be a later adapter; out of scope.)

### 2.5 Integration point (`src/agency/orchestrator.py` — MODIFY, minimal)

`execute_task` gains one branch: if the task's agent is tool-capable
(`agent.capabilities` contains `"tools"` or domain == "research"), run the
tool driver loop instead of the bare executor LLM call. The existing
planner → executor → verifier → evidence → risk pipeline stays; only the
"execute subtask" step changes for tool-capable agents. Audit-log each tool
call. Non-tool agents keep today's path unchanged.

### 2.6 Risk & evidence integration

- `sandbox_exec` calls require the existing RiskEngine to score the code
  first; score > threshold → refused with audit entry (reuse existing risk
  assessment code path in orchestrator subtask loop).
- Every tool result's `evidence` dict is appended to the task's evidence
  trail via the existing EvidenceStore (source URLs, fetch status, excerpts).

---

## 3. ResearchAgent

### 3.1 Design

- Lives at `src/agency/agents/research/agent.py` — a thin domain specialist:
  `ResearchAgent` registers itself with domain `"research"`, capabilities
  `{"research", "tools"}`, and provides the research system prompt (below).
  It is NOT a new execution engine — execution goes through the orchestrator
  tool-driver path, keeping one loop implementation.
- **System prompt (core of the agent):** role = research analyst; MUST use
  `web_search` before answering factual questions; MUST cite URLs in
  `[source]` markers; MUST call `memory_write` to store findings; MUST end
  with `{"final": ...}` containing a summary with citations; NEVER invent
  sources; if search fails, say so.

### 3.2 Telegram UX

- `/research <topic>` command in `telegram/handler.py` → creates task for
  research agent, runs tool loop, returns final answer with citations.
  Long answers (>3800 chars) split into multiple Telegram messages.
- Natural language routing: add "research" to Butler router `DOMAIN_KEYWORDS`
  (research, find, look up, latest, news, who is, what is) so plain messages
  route to research agent; Butler LLM routing already handles nuance.

### 3.3 Example flow (acceptance scenario)

User: `/research latest news on Rust 2026`
1. Router → research agent → orchestrator creates task.
2. Tool loop: `web_search("Rust 2026 news")` → 5 results → `web_fetch` 2-3
   top URLs → synthesize.
3. `memory_write` stores finding: "Rust 2026 news summary" + sources.
4. Evidence trail: search query, URLs fetched, excerpts.
5. Telegram reply: summary + `[1] url...` citations + "stored to memory".

---

## 4. File Plan

| File | Action | Brief |
|------|--------|-------|
| `src/agency/tools/__init__.py` | CREATE | exports |
| `src/agency/tools/base.py` | CREATE | ToolSpec, ToolResult, ToolContext, ToolRisk |
| `src/agency/tools/registry.py` | CREATE | ToolRegistry + default factory |
| `src/agency/tools/driver.py` | CREATE | LLM tool-calling loop |
| `src/agency/tools/builtin/web.py` | CREATE | web_search + web_fetch |
| `src/agency/tools/builtin/memory.py` | CREATE | memory_query + memory_write |
| `src/agency/tools/builtin/sandbox.py` | CREATE | sandbox_exec |
| `src/agency/agents/research/__init__.py` | CREATE | exports |
| `src/agency/agents/research/agent.py` | CREATE | ResearchAgent + system prompt |
| `tests/test_tools_base.py` | CREATE | spec/registry validation |
| `tests/test_tools_web.py` | CREATE | search/fetch with mocked httpx |
| `tests/test_tools_memory.py` | CREATE | memory tools against real store |
| `tests/test_tools_driver.py` | CREATE | driver loop with fake LLM |
| `tests/test_research_agent.py` | CREATE | end-to-end with fake LLM + fake web |
| `tests/test_research_telegram.py` | CREATE | /research command handler |

**Modified (by me, not agents — keeps briefs pure-create):**
- `src/agency/orchestrator.py` — tool-driver branch (+~60 lines)
- `src/agency/telegram/handler.py` — `/research` command (+~50 lines)
- `src/agency/butler/router.py` — research keywords (+~10 lines)
- `src/agency/butler/service.py` — seed research agent (+~5 lines)

### 4.1 Parallel brief plan (3 agents)

- **Brief 1 — Tool core + registry** (`base.py`, `registry.py`): types, schema
  validation, audit, timeout. No LLM, no network. ~15 tests.
- **Brief 2 — Web + memory + sandbox tools** (`builtin/web.py`, `builtin/memory.py`,
  `builtin/sandbox.py`): the 5 concrete tools wrapping existing services.
  ~20 tests.
- **Brief 3 — Driver + ResearchAgent** (`driver.py`, `agents/research/`): JSON
  protocol loop, robust parsing, ResearchAgent + prompt. ~18 tests.

I integrate + write the wiring tests (`test_research_telegram.py`,
orchestrator branch tests) myself after merging all three.

---

## 5. Testing Strategy

- Unit: each tool with mocked transport (httpx `MockTransport`) / real
  MemoryStore (tmp SQLite) / real SandboxManager (process backend, echo hello).
- Driver: fake LLM scripted to emit tool calls then final — asserts loop
  order, max-iteration forcing, malformed-JSON recovery, evidence aggregation.
- E2E: fake LLM + fake web → `/research` handler test — asserts citations
  present, memory item stored, evidence trail non-empty, audit entries exist.
- Full existing suite (321 tests) must stay green.
- **Live smoke test after integration** (manual, by me): restart bot, run
  `/research` from Telegram, verify real answer with real citations.

## 6. Rollout

1. Spec commit (this file).
2. 3 parallel OpenCode agents (model: `opencode/muse-spark-1.3-contributor-free`).
3. Integrate, run full suite, fix seams.
4. Commit with test count.
5. Restart bot, live smoke test, report.

## 7. Open Questions

1. Do you want `sandbox_exec` in the MVP ResearchAgent prompt? Default: **tool
   registered but not offered in research system prompt** (research rarely
   needs code exec; risk engine still gates it if requested).
2. Web search via DuckDuckGo HTML scraping can rate-limit under heavy use —
   acceptable for MVP? Fallback to Pollinations-as-search is in the design.
3. Telegram long-answer splitting: 3800-char chunks OK?
