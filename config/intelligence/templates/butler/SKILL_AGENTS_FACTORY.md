---
type: SKILL_AGENTS_FACTORY
schema_version: 1
---

# Skill — Agents, Factory and Forge

**Agent identity.** An agent is not a prompt string: it has a stable id, a
domain (security, research, coding, memory, evidence, bridge, general, …)
and capabilities. The Butler seeds one agent per default domain at startup
(`agent_id` visible via `GET /v1/agents` / `butler agents`).

**Agent intelligence profiles.** Each agent may have a profile:
`config/intelligence/templates/agent/AGENT.md` is the generic template; a
private copy at `<AGENCY_PRIVATE_DATA_DIR>/intelligence/agents/<agent-id>/AGENT.md`
overrides it and is injected (appended) into that agent's task prompts.
Profiles are descriptive context — authority comes only from the kernel.

**Factory lifecycle** (`src/agency/factory/`):

1. **Blueprint** — the owner defines an agent (name, domain, capabilities,
   `prompt_refs`, tool requirements). Import paths exist for GitHub
   skill/agent packs (`scripts/import_github_skills.py`, governed pipeline).
2. **Owner approval** — the factory's governed approve/activate path applies;
   approvals are audited with the human actor recorded.
3. **Activation/revocation** — versioned; reversion activates the previous
   version atomically (revoke old + activate new) and is journaled (SQLite
   WAL). Unset actors or missing approvals fail closed.

**Forge** (`src/agency/forge/`) inspects produced artifacts before they are
trusted: it is a review gate. Status: the LLM review-gate leg has been
qualified live; the full inspection gate is partial — never present Forge
output as full qualification.

**Per-agent memory.** Memory writes during execution bind to the executing
agent's id; `PrincipalMemoryScope` SQL-binds reads to the principal. One
agent cannot read another's private memory; nobody reaches the principal's
private records by guessing ids.

**Guidance.** To create an agent: draft the blueprint → show the user the
definition → require explicit owner approval → activate → verify with a
harmless task. Never promise an agent exists before its activation audit
entry exists.
