# The Agency — Parity Sprint 1 (Memory & Continuity) — Build Spec

**Status:** APPROVED (user directed immediate build)
**Date:** 2026-09-22
**Goal:** Fix the goldfish problem. Memory persists across restarts, the
butler remembers the conversation, and every agent can use tools. This is
parity with OpenClaw/Hermes on the fundamentals: persistent memory,
conversation context, working code execution.

---

## 1. Problem Statement

Three defects, found by audit:

1. **Memory is not persistent.** `AgencyOrchestrator.__init__` constructs
   `MemoryStore()` with no path → SQLite `:memory:` → everything (chat
   turns, research findings) is wiped on every bot restart.
2. **The butler is amnesiac mid-conversation.** `_store_turn` writes turns
   to memory but nothing ever reads them back. Each message is handled
   with zero context — "what did I just say?" fails. Additionally the
   butler constructs its OWN `MemoryStore()` (a second, separate store)
   rather than sharing the orchestrator's.
3. **Only the research domain uses tools.** `TOOL_CAPABLE_DOMAINS` is
   hardcoded to `{"research"}`; the general agent (the fallback for all
   unmatched chat) still does bare single LLM calls.

## 2. Fix Design

### 2.1 Persistent memory (orchestrator)

- `AgencyOrchestrator.__init__` accepts `memory_db_path: str | None`.
- Default: `data/memory.db` (created lazily; the `data/` dir already
  exists and is gitignored-adjacent — check .gitignore, add if missing).
- Constructor signature: `MemoryStore(db_path=...)` when path given,
  else `:memory:` (tests keep the fast default).
- `telegram_bot.py` passes nothing — the default kicks in.
- `ButlerService.__init__` **must share the orchestrator's memory
  store** instead of creating its own: `self._memory = memory_store or
  orchestrator's store`. Remove the standalone `MemoryStore()` default.

### 2.2 Conversation history in prompts (butler)

- `ButlerService.handle_message` gains a history-recall step before
  routing: `search_fts(text, limit=5)` (or `search` with tags
  `["conversation"]` + recency ordering — use `search_fts`, it exists
  and does content matching) against the shared store.
- Recall is filtered to the **same sender**: store turns with
  `agent_id=f"chat-{sender}"` so history is per-user. (Sender is the
  Telegram username — stable per user.)
- Inject the top 5 matches into the execution context as
  `context["memory_context"]`: formatted `"Earlier conversation:\n- ..."`
  block (content field, truncated to 300 chars each), only when
  non-empty.
- The orchestrator's classic executor path appends
  `context["memory_context"]` into the system-facts prompt when present
  (one sentence of framing: "Relevant earlier conversation (real,
  retrieved from memory):").
- The tool-driver path passes it into the research system prompt
  similarly.
- `_store_turn` writes with `agent_id=f"chat-{sender}"` and keeps the
  existing tags (add `"chat-{sender}"` to tags too for belt-and-braces
  filtering).

### 2.3 Tools for the general agent (orchestrator)

- `TOOL_CAPABLE_DOMAINS` → `{"research", "general"}`.
- The general agent needs a system prompt: create
  `src/agency/agents/general/__init__.py` + `agent.py` with
  `GENERAL_SYSTEM_PROMPT` (conversational assistant persona: honest,
  uses tools when they help — web_search for factual questions,
  memory_query to recall, memory_write to store facts the user shares;
  no fabricated sources; final JSON protocol identical to research).
- Orchestrator's tool branch selects the prompt by domain:
  research → RESEARCH_SYSTEM_PROMPT, general → GENERAL_SYSTEM_PROMPT.
- General agent gets `max_tool_iterations=4` (lighter than research's 6)
  via a per-domain map in the driver call.

### 2.4 Sandbox process backend (agent-built)

New `SandboxBackend.PROCESS` value + `ProcessSandboxBackend` class in
`src/agency/security/sandbox/` implementing the same surface as
`DockerSandboxBackend` (create_container/exec_command/
inspect_status/remove_container) using `subprocess` with:
- Per-sandbox temp workspace dir (already exists in manager)
- Windows-compatible limits: `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`
  on win32, `start_new_session=True` on POSIX; timeout via
  `subprocess.run(..., timeout=)`; output caps (64 KB stdout/stderr)
- **Windows note:** `resource.setrlimit` is POSIX-only — do NOT call it
  on win32; document the weaker isolation in the class docstring
  (process-level only: no CPU/mem rlimits on Windows, bounded by
  timeout + output caps)
- `SandboxManager._get_backend()` picks ProcessSandboxBackend when
  `default_config.backend is PROCESS`
- Orchestrator wires `self._sandbox_manager = SandboxManager(
  default_config=SandboxConfig(backend=SandboxBackend.PROCESS, timeout=30))
  ` so sandbox_exec works without Docker.

### 2.5 Out of scope

Skills, cron, Buddy UI, other domain agents, Neo4j.

---

## 3. File Plan

| File | Action | Owner |
|------|--------|-------|
| `src/agency/orchestrator.py` | MODIFY: memory path param, shared store, tool domains, prompt map, sandbox wiring | me |
| `src/agency/butler/service.py` | MODIFY: shared store, history recall, context injection | me |
| `src/agency/agents/general/` | CREATE: agent + prompt | OpenCode agent |
| `src/agency/security/sandbox/process.py` | CREATE: ProcessSandboxBackend | OpenCode agent |
| `src/agency/security/sandbox/config.py` | MODIFY: PROCESS enum value | me (tiny, avoids collision) |
| `src/agency/security/sandbox/manager.py` | MODIFY: backend selection | OpenCode agent |
| `tests/test_general_agent.py` | CREATE | OpenCode agent |
| `tests/test_sandbox_process.py` | CREATE | OpenCode agent |
| `tests/test_memory_continuity.py` | CREATE: persistence + recall integration | me |
| `.gitignore` | MODIFY: add `data/memory.db*` | me |

### 3.1 Parallel brief plan (2 agents)

- **Brief 1 — General agent**: `src/agency/agents/general/` +
  `tests/test_general_agent.py`. Pure create, mirrors research agent.
- **Brief 2 — Process sandbox backend**: `process.py` + manager backend
  selection + `tests/test_sandbox_process.py`.

I do all MODIFY work myself after they land (single-writer on shared
files, no merge conflicts).

## 4. Testing Strategy

- Persistence: create orchestrator with tmp db path → store item →
  new MemoryStore on same path → item present.
- Recall: two-turn conversation through butler → second turn's context
  contains first turn's content.
- General agent: tool loop runs with GENERAL_SYSTEM_PROMPT (fake LLM,
  assert prompt content + tool call).
- Process sandbox: echo code → stdout; failing code → exit_code +
  stderr; timeout kill; output truncation.
- Full suite green (397 existing + ~25 new).

## 5. Rollout

1. Spec commit.
2. 2 parallel OpenCode agents (muse-spark-1.3 free).
3. My integration: orchestrator + butler modifications, wiring tests.
4. Full suite, commit, restart bot, live checks: `/status`, chat
   continuity ("what did I just say?"), restart bot → memory survives.
