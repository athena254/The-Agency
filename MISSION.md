# MISSION.md — The Agency

## What The Agency is

A governed multi-agent execution system. A human talks to a Butler gateway, which
routes tasks to an orchestrator that runs agents (research, general, demo,
planner, executor, verifier) and tools (web search/fetch, memory, sandbox).
Around that core: Lattice coordination, memory, policies/audit/sandbox, Agent
Factory v1, skill/workflow registries, and a Forge inspection gate.

Canonical direction: `docs/SOURCE_CONSOLIDATED_BRIEF.md` and
`docs/SPEC_AGENCY_CORE_RECONCILIATION.md`. Status words are coarse
(Exists / Partial / Planned / Missing); nothing here is production-ready.

## What The Agency must NEVER become

The out-of-scope list is the part that does the work: it is how an agent
recognises that a plausible, well-argued, easy request is drift rather than a
good idea. Strike any that are wrong; propose more.

1. A general-purpose chatbot or assistant product. The Agency is an execution
   system, not a conversational companion.
2. A replacement for ATHENA. ATHENA is a separate peer platform; no Agency
   core capability depends on it, and no Agency feature may require ATHENA.
3. An autonomous agent that acts without a human-defined intent or mission.
   Agents execute scoped tasks; they do not set their own goals.
4. A system that bypasses audit, policy, or sandbox. Every state change goes
   through the kernel (FindingLifecycle etc.); no shortcut paths.
5. A monolith that absorbs every adjacent prototype (dark_factory, ghost
   factories, free-program experiments). Those stay separate unless explicitly
   promoted via spec.
6. A production-hardened security boundary. Isolation guarantees are not
   audited; do not claim or imply they are.
7. A feature that ships without evidence. No change merges without inspection
   gate + tests + (eventually) scenario proof.
8. A second source of truth for workflows/agents. Versioned registries are
   canonical; ad-hoc registration is drift.

## Success looks like

- A human files an issue or supplies a PRD.
- The factory checks it against this mission, plans, builds, proves, and
  (with human approval) merges.
- Every merge carries evidence: inspection report, security scan, test run,
  and (when available) scenario proof.
