# Parity Brief 1: General Agent (tool-capable conversational agent)

You are building the general-purpose conversational agent for The Agency.

## Context
Project: C:\Users\alphi\theagency\
Spec: C:\Users\alphi\theagency\docs\SPEC_PARITY1.md (section 2.3)
Pattern to mirror EXACTLY: C:\Users\alphi\theagency\src\agency\agents\research\ — read agent.py and __init__.py there first. Your output must match that structure 1:1 (plain class, not pydantic; module-level prompt constant).
Tool layer exists: src/agency/tools/ (registry with web_search, web_fetch, memory_query, memory_write, sandbox_exec)
The general agent is the DEFAULT routing target — it handles all chat that doesn't match a specialist domain. It must be conversational first, tool-using when needed.

## Files to Create

### 1. `src/agency/agents/general/__init__.py`
Export GeneralAgent, GENERAL_SYSTEM_PROMPT (mirror research/__init__.py).

### 2. `src/agency/agents/general/agent.py`
- `GENERAL_SYSTEM_PROMPT` module constant — a conversational assistant persona with these rules:
  1. You are the General Agent of The Agency — a helpful, honest conversational assistant.
  2. For casual conversation, greetings, and questions you can confidently answer, respond directly with {"final": "..."} — do NOT call tools unnecessarily.
  3. For factual questions about current events, versions, prices, news, or anything time-sensitive, call web_search first.
  4. Use memory_query to recall what the user told you earlier when relevant.
  5. When the user shares a fact, preference, or instruction worth remembering, store it with memory_write.
  6. Do NOT use sandbox_exec unless the user explicitly asks to run code.
  7. Be efficient: at most 4 tool calls. Most conversations need zero.
  8. NEVER invent sources or facts. If you don't know and can't look it up, say so.
  9. Always end with {"final": "<your reply>"} — keep replies concise and natural.
- `GeneralAgent` class (mirror ResearchAgent exactly):
  - `__init__` sets `self.domain = "general"`, `self.capabilities = {"general", "respond", "tools"}`, `self.name = "General Agent"`
  - `system_prompt(self) -> str` returns GENERAL_SYSTEM_PROMPT
  - `async handle(self, message: str, context: dict | None = None) -> str` returns prompt + message (same pattern as ResearchAgent.handle)

### 3. `tests/test_general_agent.py` (~8 tests, mirror tests/test_research_agent.py's style)
- Prompt contains the key rules (web_search guidance, memory guidance, final protocol, no-invention rule)
- Prompt does NOT force tool use for casual chat (assert rule 2 present)
- GeneralAgent attributes (domain, capabilities, name)
- handle() returns prompt + message
- system_prompt() matches the module constant
- Prompt is under 2500 chars (keeps token cost sane)
- Capability set includes "tools"
- Instantiation is side-effect-free (no network, no stores touched)

## Conventions
- Python 3.11+, `from __future__ import annotations`, structlog for logging
- NO new dependencies
- Run: `PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_general_agent.py -v` — all must pass
- Run: `ruff check src/agency/agents/general/ tests/test_general_agent.py` — clean

## Do NOT modify any existing files; only create the new ones listed in this brief.
