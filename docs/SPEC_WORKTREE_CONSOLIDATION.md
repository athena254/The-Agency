# Agency worktree consolidation checkpoint

## Scope and source of truth

Consolidate the committed `feature/mvp-prototype` lineage and reviewed non-runtime dirty edits from `C:/Users/alphi/agency-mvp` and `C:/Users/alphi/theagency` into the canonical `C:/Users/alphi/theagency` worktree. Both branches share base `3df50d25f8c77afe1a62fec71d28651a0fb42138`; the MVP branch advances it with the Agency core, Butler threads, skills/workflows, Factory/Forge, Remex, beta gates and local prototype. The live bot runs separately from `C:/Users/alphi/agency-deploy-main` at `4cf28a7`; this operation does not restart it or change its working directory.

## Non-destructive rules

- Preserve dirty source/doc/test state before integration in `C:/Users/alphi/agency-consolidation-snapshot-20260927-105944` (manifest and binary git patches). Keep the original worktrees intact until integrated files and tests are verified.
- Do not commit, transplant, reset, or delete runtime SQLite databases, WAL/SHM files, `.env`, or other credentials. A checked-in `data/lattice.db` changed during tests is runtime state, not source. Leave the live deployment worktree untouched.
- Do not import the root worktree's blanket Ruff per-file suppression or replacement `uv.lock` merely to make checks green. Evaluate the standalone OpenClaw exception-type edit against the actual exception contract before porting it. Preserve `workspace/probe_pollinations.py` in the snapshot, not production source, unless it proves useful and credential-safe after review.
- Never launch coding agents in either dirty original worktree. Each agent gets a separate worktree and nonoverlapping file ownership. Human/owner approval is required for deployment, remote push, or destruction of backup worktrees.

## Integration streams

1. MVP dirty stream: bring the pending Telegram creation guard, command-menu changes, beta gap-log update and regression tests from the snapshot into `consolidation/agency-home`. Verify both beta and non-beta entry paths reject synthetic governance without hitting the Butler or Lattice.
2. Root dirty stream: review the root snapshot independently against the MVP baseline. Port only justified behavior (initial candidate: OpenClaw malformed status handling) to a separate `consolidation/root-delta` branch with a focused test. Document reasons for any skipped config/lint/lockfile changes. This stream must not edit Telegram or beta test files.
3. Integration: merge the reviewed root-delta commit into the home branch, run offline unit suite and CI's lint, format, mypy checks; then fast-forward canonical `main` when its dirty source files have been safely reconciled from the snapshot. Confirm file hashes/status and test result in the canonical folder.

## Acceptance and boundaries

- Canonical folder contains all committed MVP history plus both accepted dirty streams; no lost original user source/doc/test changes.
- Offline tests run with `AGENCY_LLM_PROVIDER=echo AGENCY_LLM_MODEL=echo`; quality gate failures are reported, not hidden.
- Runtime DBs, active Telegram poller, credentials, and remote branches remain unchanged. 'Merged' means local Git integration into canonical `main`, not pushed or deployed. The existing beta and peer-governance blockers remain blockers.
