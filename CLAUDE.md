# CLAUDE.md — The Agency (OpenCode)

Agent rules for OpenCode sessions. Same rules as agents.md.

## Golden rule 1 — KISS and YAGNI (build and review)
Always choose the smallest change that fully solves the task. If a feature,
branch, file, or tool is not needed, remove it or defer it. Apply this on
build *and* review: a reviewer asks "why was this added?" and rejects
anything that can be removed without losing the goal.

## Golden rule 2 — Plan before executing anything
Always plan before executing. For every task, try all possible methods and
eliminate each until one is left — the most probable one — before proposing
or running anything. No first action without a plan.

## Known gaps — read `gaps.md`
`gaps.md` at the repo root records production gaps found during implementation
(the capability⇄factory import cycle, non-atomic self-reversion, unimplemented
learned skills, opt-in attestation, narrow orchestrator hook, and others).
Read it before touching `agency.capability`, `agency.factory`, `agency.skills`,
or `agency.selfimprove`. Avoid the patterns it flags — in particular, never add
module-level `agency.capability.*` imports under `agency/factory/` (import cycle).
All agents are free to fix any gap in that file; claim one and close it when done.
