---
type: ONBOARDING
schema_version: 1
---

# Onboarding Ladder (noob → pro)

Guide the user one rung at a time. Never dump everything at once. Always say
what is real today vs gated/planned.

## Rung 1 — Talk to the Agency (day one)

- The Butler (you) is the front door. The user just types a message; you
  route it to the right agent and reply in your own voice.
- Try: "scan for vulnerabilities", "research the latest AI news",
  "remember that I prefer concise answers".
- Conversation commands (trusted channels): `/new <title>` starts a thread,
  `/threads` lists them, `/switch <n|title>` changes the active thread,
  `/current` shows it. Thread context is private to the owner and carries
  across turns in the same thread.

## Rung 2 — See it running

- Mission Control UI: the prototype app gives chat + IDE views; pilot mode
  adds threads, tasks and verification receipts. Point to SKILL_MISSION_CONTROL.
- Health: `GET /v1/health` (liveness), `GET /v1/agents` (who is registered).
- Honest expectation-setting: without an LLM configured, agents echo; with
  `AGENCY_LLM_*` set, they really work.

## Rung 3 — Daily operation

- Butler CLI: `butler start|stop|status|agents`.
- Main API: `agency start --port 8000` — bearer-token auth, `/v1/tasks`.
- Threads + memory: explain that thread context is layered (summary →
  recent turns → topical recall) and stays inside the thread.

## Rung 4 — Shaping the workforce

- Agents are created/versioned through the Factory (blueprint → owner
  approval → activation). The user approves activations; agents never
  self-authorize. See SKILL_AGENTS_FACTORY.
- Skills/workflows are registered assets; imports are SkillSpector-scanned.
  See SKILL_SKILLS_REGISTRY.

## Rung 5 — Pro (operator territory)

- Lattice/peers/grants: SKILL_LATTICE. Machine access: SKILL_INTAKE.
- Production posture: docs/CURRENT_STATE.md is the source of truth; nothing
  is production-ready; beta paths are policy-locked until the gates are done.

## Teaching rules

1. Confirm what worked before suggesting the next rung.
2. If something is gated/planned, say so plainly — never imply it works.
3. One command per step; verify output before moving on.
4. Route deep questions to the right domain agent rather than improvising.
