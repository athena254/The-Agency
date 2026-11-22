---
type: CAPABILITY_CATALOG
schema_version: 1
---

# Capability Catalog (truthful status)

Label every answer with the honest status. Status words: **Implemented**
(works today, tested), **Partial** (works in bounded slices), **Gated**
(built but policy-locked), **Planned** (designed, not built).

## Conversation & routing — Butler

- Keyword routing: **Implemented** (deterministic, always-on fallback).
- LLM routing (OpenAI-compatible endpoint, e.g. CentAI): **Implemented**
  (configured via `BUTLER_LLM_*` in `.env`).
- Jev System-1 decision model: **Implemented** (code + tests; activate with
  `BUTLER_DECISION_PROVIDER=jev` once a System One endpoint is configured).
- Persona reply framing (SOUL.md voice): **Implemented** (LLM-voiced when an
  LLM is configured; verbatim otherwise).
- Threads/workspaces: **Implemented** in-process (`/new`, `/threads`,
  `/switch`, `/current`), owner-checked, private Telegram path tested.
  Public HTTP thread features: **Planned**.
- Butler HTTP API: `POST /v1/message`, `GET /v1/agents`, `GET /v1/health` —
  **Implemented**, bearer-token gated.

## Execution

- Orchestrator task lifecycle (submit → execute → result, dispatcher queue):
  **Implemented**.
- Echo/offline execution (no LLM): **Implemented** (default; tests, offline).
- Real-model agent execution: **Implemented** via the shared LLM adapter —
  configured with `AGENCY_LLM_PROVIDER/MODEL/API_KEY/BASE_URL` in `.env`
  (e.g. OpenAI-compatible CentAI). Without it agents echo.
- Tool use (search/fetch/memory/sandbox adapters): registry exists; owner
  pilot registers only three bounded read-only tools; beta tool use: **Gated**.

## Memory

- Scoped memory store, retrieval, principal-bound scopes: **Implemented**.
- Honcho mirror adapter: **Implemented** (optional mirror, never authority).

## Knowledge (this document system)

- Validated markdown documents, progressive loading, private overrides:
  **Implemented**.
- Per-agent profiles injected into execution: **Implemented** (private
  profiles only).
- Full principal TELOS: **Planned** (index scaffold only).
- Learning pipeline, shared-knowledge publication: **Planned**.

## Factory / Forge / Skills

- Blueprint → owner-approved agent lifecycle: **Implemented** (approve
  recorded in audit).
- Forge review gate: **Partial** — LLM review-gate leg qualified live
  (Google credentials path); full P2 authority: **Planned**.
- Skill/workflow registries + SkillSpector-gated imports: **Implemented**.

## Machine access

- Bearer-token task intake (`POST /v1/tasks`, `GET /v1/tasks`,
  `/v1/agent-requests`): **Implemented**, default-deny (503 unless enabled).
- §12 signed grant path on intake routes: **Implemented** (route tail live).
- End-to-end "external task → executed → result returned to submitter":
  **Partial** — result-return leg still open (A5 outbox work).

## Peers / Lattice

- Signed envelopes, identities, grants, fencing, CometBFT 0.38.26 lab:
  **Implemented** (foundation + live 4-node lab evidence).
- Live decentralized peer authority (P2): **Planned** — the core missing
  milestone.

## Mission Control

- Prototype chat/IDE (session-continuity fix landed): **Implemented**.
- Pilot mode (threads, calculator/coding tasks, verification receipts):
  **Implemented** (local pilot only).
- Locked panels (Memories/Evidence/Skills/Lattice/Forge) render honest gates:
  **Implemented**.
