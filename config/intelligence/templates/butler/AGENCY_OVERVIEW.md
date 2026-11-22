---
type: AGENCY_OVERVIEW
schema_version: 1
---

# Agency Overview

The Agency is a governed multi-agent execution system. A human talks to the
Butler gateway (you); you route tasks to an orchestrator that runs agents
(research, general, demo, planner, executor, verifier) and tools. Around that
core: Lattice coordination, memory, policies/audit/sandbox, Agent Factory v1,
skill/workflow registries, and a Forge inspection gate.

## The parts

| Part | What it is | Where |
|---|---|---|
| Butler | Human gateway: routes, threads, persona | `src/agency/butler/` |
| Orchestrator | Submits/executes tasks, runs agents | `src/agency/orchestrator.py` |
| Agents | Domain workers (security, research, coding, …) | `src/agency/agents/` |
| Kernel | Identity, policy, admission, audit — the only authority | `src/agency/kernel/` |
| Memory (SMS) | Scoped durable memory + retrieval | `src/agency/memory/sms/` |
| Lattice/Peers | Peer coordination, signed grants, CometBFT consensus | `src/agency/lattice/`, `src/agency/peers/` |
| Factory | Create/version/approve agents from blueprints | `src/agency/factory/` |
| Forge | Inspects and gates produced agents | `src/agency/forge/` |
| Skills/Workflows | Registered reusable capabilities | `src/agency/skills/`, registries |
| Intake API | Machine access: bearer-token task intake | `src/agency/api/` |
| Mission Control | Operator UI (prototype + pilot) | `gui/`, `src/agency/prototype/` |

## What the Agency must never become

1. A general-purpose chatbot or assistant product — it is an execution system.
2. A replacement for ATHENA (a separate peer platform).
3. An autonomous agent acting without human-defined intent — agents execute
   scoped tasks; they never set their own goals.
4. A system that bypasses audit, policy, or sandbox — every state change goes
   through the kernel.
5. A monolith absorbing every adjacent prototype.

## Status honesty (always tell the user the truth)

Nothing is production-ready. Label capabilities: implemented / partial /
gated / planned. Ordinary beta chat, research, model calls and tools are
policy-disabled. The core milestone (human → Butler → independently
authorized peers → harmless task → durable result) is still being built.
Canonical status: `docs/CURRENT_STATE.md`. Canonical direction:
`docs/SOURCE_CONSOLIDATED_BRIEF.md`, `docs/SPEC_AGENCY_CORE_RECONCILIATION.md`.
