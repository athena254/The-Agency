# Tools Brief 3: LLM Tool-Calling Driver + ResearchAgent

You are building the LLM tool-calling loop and the Research domain agent for The Agency.

## Context
Project: C:\Users\alphi\theagency\
Spec: C:\Users\alphi\theagency\docs\SPEC_TOOLS_RESEARCH.md (read sections 2.4 and 3)
Core types: C:\Users\alphi\theagency\src\agency\tools\base.py (being built in parallel — import ToolSpec, ToolResult, ToolContext, Tool from `agency.tools.base`; if missing, create a MINIMAL placeholder at that exact path ONLY IF missing with: ToolRisk enum READ_ONLY/MUTATES_STATE/EXECUTES_CODE; ToolSpec frozen dataclass name/description/parameters/risk; ToolContext dataclass agent_id/task_id/memory_store/sandbox_manager/lattice/audit/metadata; ToolResult pydantic model tool/ok/output/error/duration_ms/evidence; Tool Protocol with .spec and async run(args, ctx))
Registry: C:\Users\alphi\theagency\src\agency\tools\registry.py (parallel — ToolRegistry with async call(name, args, ctx) -> ToolResult and list_specs())
LLM adapter: src/agency/llm/adapter.py — `LLMAdapter` with `async generate(prompt, context) -> str`
Agent registry pattern: study src/agency/agents/demo/agent.py and src/agency/kernel/registry.py (Agent model — check its fields: id, name, domain, capabilities, etc.)

## Task
Create the tool-calling driver and the ResearchAgent.

## File to Create

### 1. `src/agency/tools/driver.py`
- `ToolLoopStep(BaseModel)`: tool (str), args (dict), ok (bool), duration_ms (int), error (str | None)
- `ToolLoopResult(BaseModel)`: final_answer (str), steps (list[ToolLoopStep]), evidence (dict — aggregated: all "urls"/"sources" keys from tool evidence dicts, plus "queries"), llm_calls (int), status (str: "completed" | "max_iterations" | "llm_error")
- `ToolDriver` class:
  - `__init__(self, registry, llm, max_tool_iterations: int = 6)`
  - `async run(self, task: str, system_prompt: str, ctx: ToolContext) -> ToolLoopResult`
  - Protocol (put in the system prompt template `_build_prompt(task, system_prompt, tool_specs, history)`):
    - Section 1: role/system prompt text
    - Section 2: AVAILABLE TOOLS — JSON list of tool specs (name, description, parameters)
    - Section 3: conversation history so far as numbered turns: "You: <previous assistant JSON>" / "Tool result: <truncated to 2000 chars, JSON-encoded>"
    - Section 4: the task + instructions:
      ```
      Respond with EXACTLY ONE JSON object, nothing else:
      - To use a tool: {"action": {"name": "<tool>", "args": {...}}}
      - When you have enough information to answer: {"final": "<your complete answer with citations>"}
      ```
  - Loop: call `llm.generate(prompt, {"task_id": ctx.task_id})` → extract JSON:
    - `_extract_json(text) -> dict | None`: find the first balanced `{...}` block (handle code fences ```json ... ```, leading prose, trailing prose). Use a brace-depth scanner, not regex-only. Return None if no valid JSON object.
    - If parsed has "final" (non-empty string) → done, status "completed"
    - If parsed has "action" with name+args → `registry.call(name, args, ctx)` → append ToolLoopStep + observation to history, continue
    - If neither (None, malformed, or missing keys) → append observation "Your last response was not valid JSON with 'action' or 'final'. Respond with exactly one JSON object." and retry; after 2 consecutive parse failures force final
    - After max_tool_iterations tool calls, append "You have used all tool calls. Answer now with {\"final\": ...}" and require final; if the next response still isn't final → status "max_iterations", final_answer = last raw LLM text or fallback message
    - LLM exception → status "llm_error", final_answer = "LLM error: <msg>", steps preserved
  - Evidence aggregation: collect from every ToolResult.evidence dict — union all list values under keys "urls"/"sources"/"queries" (dedupe, preserve order); copy scalar "engine" values.

### 2. `src/agency/agents/research/__init__.py`
Export ResearchAgent, RESEARCH_SYSTEM_PROMPT

### 3. `src/agency/agents/research/agent.py`
- `RESEARCH_SYSTEM_PROMPT` module constant:
  ```
  You are the Research Agent of The Agency — a rigorous research analyst.
  Rules:
  1. For any factual question, call web_search FIRST. Never answer from memory alone.
  2. Read promising sources with web_fetch before citing them. Cite every claim with [N] markers matching your source list.
  3. Store important findings with memory_write (title + concise summary with sources).
  4. When done, respond {"final": "<summary with [N] citations followed by a Sources list of URLs>"}.
  5. NEVER invent sources, URLs, or facts. If search fails or returns nothing, say so plainly in the final answer.
  6. Do not use sandbox_exec unless the task explicitly requires running code.
  ```
- `ResearchAgent` class (plain class, mirrors DemoAgent's style — NOT a pydantic model):
  - `__init__(self)` — sets `self.domain = "research"`, `self.capabilities = {"research", "tools"}`, `self.name = "Research Agent"`
  - `system_prompt(self) -> str` — returns RESEARCH_SYSTEM_PROMPT
  - `async handle(self, message: str, context: dict | None = None) -> str` — convenience wrapper used by tests: returns the system prompt concatenated with the message (the real execution path is the orchestrator's tool-driver branch; this keeps the agent usable standalone)

### 4. `tests/test_tools_driver.py` (~12 tests)
Fake LLM class in the test: takes a list of scripted responses (strings), pops one per call.
- Single tool call then final → status completed, steps has 1 entry, llm_calls == 2
- JSON in code fences parsed correctly
- JSON with leading prose ("Sure! {\"action\"...}") parsed
- Malformed response → retry observation appended, next response used; 2 consecutive malformed → forced final
- Tool error (ok=False) → observation contains error, loop continues
- max_tool_iterations reached → forced final prompt sent; refusal → status max_iterations
- LLM raises → status llm_error, steps preserved
- Evidence aggregation across steps (two fake tools returning evidence urls/sources/queries)
- Unknown tool action → observation says unknown tool, loop continues
- Prompt contains tool specs and task text
Use a fake registry (dict-backed) implementing call() and list_specs().

### 5. `tests/test_research_agent.py` (~6 tests)
- Prompt contains the key rules (web_search first, cite, never invent)
- ResearchAgent attributes (domain, capabilities, name)
- handle() returns prompt + message
- Prompt builds with tool specs via ToolDriver._build_prompt (instantiate driver with fake registry/llm, call _build_prompt, assert tool names present)

## Conventions
- Python 3.11+, pydantic v2, structlog, `from __future__ import annotations`
- NO new dependencies
- Run: `PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_tools_driver.py tests/test_research_agent.py -v` — all must pass
- Also run `ruff check src/agency/tools/driver.py src/agency/agents/research/` and fix issues

## Do NOT modify any existing files; only create the new ones listed in this brief.
