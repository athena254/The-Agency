# Forge Factory Plan — research-backed roadmap for The Agency

Sources studied:
- Ras Mic (Michael Shimeles): "software factory" = AGENTS.md + 4-6 skill markdown files. Steps: Isolate (git worktrees), Build (code-structure skill -> service layer), Prove (evidence-driven testing, before/after screenshots/video/numbers), Ship (Greptile AI reviewer loops to 5/5; human reviews visual proof, not raw code). Up to 15 parallel features. Model/harness agnostic.
- Cole Medin, AI Software Factory (github.com/coleam00/ai-software-factory): issue/PRD in -> merged PR out. Governance layer: MISSION.md, harness/END-TO-END.md (2-5 user journeys), .factory/holdout/HOLDOUT.md. All AI work via shared Archon SDLC pack (triage, build, validate, merge). Merge queue reads required checks + protected paths and seals plan by script. Scheduling via systemd timer. Cost: instrument tokens, medium models routine, large for reasoning.
- StrongDM Software Factory: humans define intent/scenarios/constraints; agents generate + validate against real behavior (Digital Twin Universe); "validation replaces code review"; iterate until converges.
- Dan Shapiro's 5 levels of AI coding (0-5). Cole: most should aim Level 3 (in the loop). Level 5 "dark factory" (no steering wheel) is experimental.
- Other dark factories: Dark Factory HQ (git-resident, markdown-native, agent-first CLI), nickfujita/dark-factory (spec-driven skills for Claude Code/Codex), geekforge (planner/builder/verifier seats), Dark Software Factory (decide/build/operate loop; humans tend harness + steer).

## What we already have
- Forge v1 inspection gate (bounded, read-only, no process execution).
- Forge v2 security scan (stdlib AST + regex).
- Forge v2 isolated test runner (subprocess pytest in a temp copy).
- Composed pipeline + SQLite persistence.
- NYX (separate critic service) usable as an independent reviewer.

## What we are missing (gaps)
Worktree isolation, coding agent, evidence capture, scenario/holdout runner, AI reviewer loop, merge queue + protected paths, scheduling, cost tracking, governance markdown files.

## Phased plan (simple English)

Phase 1 — Governance files (markdown, model-agnostic) [low risk, high leverage]
- Write MISSION.md (what the product is + out-of-scope list of >=5 plausible-but-wrong requests).
- Write AGENTS.md / FACTORY_RULES.md (triage rules, PR size limits, quality gates, mandatory browser testing).
- Write harness/END-TO-END.md (2-5 user journeys in plain English, naming expected value).
- Write .factory/holdout/HOLDOUT.md (independent scenarios; keep private from builder).

Phase 2 — Isolation
- Git worktree per feature/issue (Ras Mic "isolate"). Each agent gets its own station; never edit main directly.

Phase 3 — Build
- Bounded coding agent: takes a plan, applies edits via patch, then runs inspection + scan + tests. Our existing gate becomes the "build verification."
- Code-structure skill: service layer with explicit inputs/structured returns (readable by humans).

Phase 4 — Prove (evidence-driven)
- Before/after capture: screenshots, video, or numbers (Ras Mic).
- Scenario-based validation: run real user journeys against a running app (StrongDM "validation replaces review").
- Holdout runner: a separate validator workflow the builder cannot see (Cole/StrongDM holdout pattern).

Phase 5 — Ship
- AI reviewer loop: an independent reviewer (NYX or Greptile) scores the PR; loop back to build until threshold (e.g. 5/5, zero unresolved comments).
- Merge queue: reads required checks + protected paths, seals plan by script (Cole's pattern). Human approves merge.
- Protected paths list: factory never touches infra/secrets/config without approval.

Phase 6 — Autonomy (optional, last)
- Scheduling via systemd/cron timer; discovery publishes verified findings as new issues.
- PRD -> backlog slicing (archon-backlog style).
- Cost instrumentation: token tracking, medium models routine, large for reasoning.

## Safety rails to keep throughout
- Never let the builder see holdout scenarios.
- Keep inspection gate process-free; subprocess only in isolated test runner.
- Merge never auto-deploys without human approval (at least at first).
- Protected paths enforced by script, not by prompt.

## Suggested first concrete step
Do Phase 1 (the four markdown files) + Phase 2 (worktree isolation helper) first. These are cheap, immediately useful, and unlock everything else. Then Phase 4 (evidence + holdout runner) since we already have the test runner. Coding agent (Phase 3) comes after the gates exist.
