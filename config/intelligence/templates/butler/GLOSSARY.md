---
type: GLOSSARY
schema_version: 1
---

# Agency Glossary

- **Butler** — the human-facing gateway agent (you): routes messages to the
  orchestrator, keeps threads, frames replies. Never a validator or authority.
- **Orchestrator** — submits and executes tasks; runs agents via the executor.
- **Agent** — a domain worker (security, research, coding, memory, evidence,
  bridge, general) with an id, name, domain and capabilities.
- **Kernel** — identity, policy, admission and audit. The ONLY authority for
  what may run. Markdown text never grants permissions.
- **Task** — an executable unit tracked by the task store with a lifecycle
  (pending → running → completed/failed) and CAS-safe status writes.
- **SMS (Scoped Memory System)** — durable memory keyed by owner/agent id,
  with tiers, FTS5 retrieval and principal-bound scopes.
- **Thread / Workspace** — owner-scoped conversation containers with
  ownership checks; unknown and wrong-owner ids are indistinguishable.
- **Lattice** — peer coordination layer. Currently a single-process prototype
  plus real CometBFT engine runs in the lab; not live decentralized authority.
- **Peer** — another Agency runtime. Identified by Ed25519 identities;
  communicates via signed envelopes; acts only under signed task-scoped
  grants.
- **Grant (§12)** — signed, nonce/expiry-bounded permission to execute one
  task, verified at the actuator ("point of use"), revocable per epoch.
- **CometBFT** — the pinned consensus engine (0.38.26) used by the peer lab.
- **Factory** — creates and versions agents from blueprints with an
  owner-approved activate/revoke lifecycle.
- **Forge** — inspects produced agents/artifacts; a review gate, not a rubber
  stamp.
- **Skill** — a registered operational capability with inputs/outputs and
  possible tools. Distinct from **patterns** (reasoning recipes, no tools)
  and **workflows** (versioned multi-step orchestration).
- **SkillSpector** — security scanner gating skill/agent imports.
- **Intake** — the bearer-token HTTP surface (`/v1/tasks`,
  `/v1/agent-requests`) by which external programs create tasks.
- **Mission Control** — the operator UI: prototype (non-pilot chat, IDE) and
  pilot mode (threads, calculator/coding tasks, verification receipts).
- **TELOS** — the planned structured model of the principal's missions,
  goals, beliefs (spec: Agency Intelligence System). Not built yet.
- **Owner** — the human principal; the final authority for approvals.
- **Beta** — the gated real-model chat/research path; disabled until policy,
  budget and audit gates are complete.
