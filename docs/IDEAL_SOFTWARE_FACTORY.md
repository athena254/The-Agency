# The ideal software factory — what "fully working" looks like

**Audience:** the owner of The Agency and anyone deciding what to build next in Forge.
**Source notes:** `D:/software factory/DARK-FACTORY-DESIGN.md`, `dark-factory-agent-spec-v2.md`, `sdlc-roles-v3.md`, `LEARNINGS.md`.
**Status:** design description, not a claim that Forge does all of this today.

## One-paragraph answer

A fully working factory is a **dim factory**, not a lights-out one. You give it a repository and a goal. It learns the repository, writes a spec, asks a human only where judgment is needed, builds in isolated worktrees, verifies with tests it was not allowed to see, gets independent review from other model families, ships through staging with a rollback plan, and then keeps watching the software and improving it. No one reads every diff. Humans own *whether we built the right thing*; the factory owns *building it correctly and proving it*.

The output is shipped, verified, maintained software. The input is a goal plus a human-owned PRD. The factory never writes its own goals.

## The five pieces it must have

1. **Guidance layer** — `AGENTS.md`, `CLAUDE.md` and selected skills that describe how *this* repository is built. The factory follows the repo's existing process instead of replacing it.
2. **Validation harness** — tests, conformance checks, held-out tests, multi-model review and security audit. This is the real value; code generation is cheap, proof is not.
3. **Workflow-driven repository** — issue → plan → build → validate → merge, with every step recorded and resumable.
4. **Deployment** — staging first, smoke tests, canary, then production, with rollback ready. No deploy is a side effect of a green gate.
5. **Trigger** — cron, webhooks, issue labels or board polling. This is what makes it autonomous rather than a tool you must drive by hand.

If any one of these is missing, it is not a factory yet. It is a set of tools.

## The full loop, phase by phase

### Phase 0 — Onboarding any repository

The factory first reads the repo: structure, languages, frameworks, dependencies, test setup, CI, README and git history. If the repo is stale or dead, it enters revival mode: fix the build, get a test baseline, document what exists, then continue.

All repository-specific knowledge goes into one adapter, `.factory/adapter.yaml` plus `.factory/hooks/`. The engine stays product-agnostic. If there is no adapter, it refuses to run rather than guessing. Guidance files are generated from reality, not copied from a template. Skills are selected by language, framework, domain and task type — not all 311 loaded at once.

### Phase 1 — Planning

Research happens in a fresh session. A PRD is produced from the human's goal, with goals, non-goals and success criteria. A critic challenges the assumptions. The verification recipe is committed *before* code. Architecture and program design are written down down to types and call graphs, then sliced into small tasks, each owned by one agent and one worktree.

A plan is a claim set, not a guarantee. The factory checks its shape, then still verifies the result.

### Phase 2 — Building

Each task gets an isolated worktree and branch from `origin/main`; open PRs are checked for overlap. The worker follows TDD: red, green, refactor. It records the failing test before the fix and the passing run after. Hooks guarantee the rules that prose only asks for: secrets guard, dependency limits, audit log, tests-must-pass. AI tells are stripped from comments, commits and PR text.

### Phase 3 — Verification

This is where the factory earns trust.

- Unit, integration and end-to-end tests from a test inventory.
- Held-out tests in `.factory/holdout/` that the builder can never see. If the agent sees the test, it can game it.
- Multi-model review: two different model families review the same work, and a lead adjudicates. Agreement across families is the signal.
- A six-phase security audit: recon, coverage-led hunting, candidate validation, structured output, independent record verification, neutral reporting.
- Consolidation and entropy scans for duplication, drift and dead code.
- A review loop that keeps working until the score is satisfactory or a hard cap is reached, reporting the real score.

### Phase 4 — Shipping

The PR carries evidence: before/after output, screenshots where relevant, test numbers, risk class and caveats. Staging deploy runs smoke and canary checks. Production deploy happens only after staging is green and a human has approved the release. A rollback plan exists before the deploy starts. Reviewers get a concise visual outline instead of a raw diff dump.

### Phase 5 — Maintaining

A sensor loop watches tests, metrics, logs, error rates and CI. Signals become issues. Specialist agents work the issues one at a time: builder, QA, reviewer, handoff. Dependency updates, security re-audits, consolidation scans, performance checks and documentation updates run on a schedule. UI work runs its own diagnose → critique → audit → fix → polish loop. Every fix or incident becomes a skill or rule update, so the same lesson does not have to be learned twice. If a repo goes stale again, Phase 0 runs again.

Scheduled triggers: daily dependency and security scans, weekly consolidation and performance reviews, on-event triage for CI failures and new issues.

## Roles, not one giant agent

The design uses about 54 specialist roles inside six planes. The important idea is separation of judgement:

- **A01–A11** run the factory: orchestrator, context and ADRs, model router, tool/permission warden, budget governor, guardrail author, provenance/audit, eval engineer, self-improvement, pipeline reliability, drift detector.
- **B02–B06** write specs, tests, architecture and domain design.
- **C builders** implement, each with a narrow skill set.
- **D01–D08** are critics: code, tests, deep verification, performance/resilience, AppSec, supply chain, red team, UX/accessibility.
- **E deploy and infra**, **F humans/governance**, and domain packs for games, Web3, hardware, healthcare, fintech and so on.

The rule behind the roles: **builders never judge their own work**, and high-risk work gets a critic from a different model family.

## Human gates a real factory never crosses

A named human is required for: product strategy and priorities; legal, IP and regulatory decisions; safety-critical sign-off; pricing, vendors and spend; hiring; breach disclosure; release approval above the risk threshold; and any change to the factory's own guardrails, critics or gold set. The factory may prepare the decision packet. It may not make the decision.

## What "fully working" feels like day to day

- You state a goal and approve a short spec. You are not asked to choose every file.
- Work appears as small branches with evidence attached. Nothing lands on main unattended.
- Failures produce a diagnosis and a proposed fix, not a silent retry.
- The factory can tell you why a job is stuck, what it cost, which gate rejected it and who must decide.
- Security and quality checks happen before you look at the PR, not after.
- The system improves its own rules only through an approved, audited path.
- Humans review samples and decisions, not every diff.

## Honest comparison with Forge today

Forge already has a real skeleton of this factory: isolated worktrees, a durable job queue, plan gates, role skills, inspections, security scan, isolated tests, review, runtime checks, evidence reports, human approvals, gated merge/release tools, a two-family critic seam, a gold-set seam, regression-first proof, path-derived human gates, a CI-failure bridge, live job views and a load probe.

What it does **not** have yet:

- Repository onboarding and an `.factory/adapter.yaml` with revival mode.
- A real PRD/challenge/architecture stage with committed specs and held-out tests wired to each job.
- Real second-provider critics; the two-family seam exists but is not wired to two live providers.
- Held-out tests the worker cannot see.
- Six-phase security audit, consolidation/entropy scans and a bounded Greptile-style review loop.
- Staging → smoke → canary → production deployment with rehearsed rollback.
- Scheduled maintenance triggers installed and supervised; `forge schedule` currently only generates text.
- Multi-harness portability and per-repo skill profiles.
- A human approval UI and real metered cost/token reporting.

So Forge is a **governed builder with strong gates**. The ideal factory is a **self-maintaining system with a verified deployment and learning loop**. The gap is mostly wiring, onboarding, deployment and triggers — not missing ideas.

The migration plan is in `docs/plans/FORGE_TO_IDEAL_FACTORY.md`.
