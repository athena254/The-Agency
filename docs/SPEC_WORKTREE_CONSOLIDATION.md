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
3. Branch inventory: compare the other local branches to MVP by ancestry and patch identity. Most are ancestors or equivalent patches; preserve branch refs, not stale whole-tree snapshots. Independently review and port unique `feat/beta-tool-policy` and `feat/beta-web-security` work into isolated branches. The `feat/beta-integrity` change is already present in MVP (`ce66f64`).
4. Integration: cherry-pick the reviewed independent commits into the home branch, run offline unit suite and CI's lint, format, mypy checks; then fast-forward canonical `main` when its dirty source files have been safely reconciled from the snapshot. Confirm file hashes/status and test result in the canonical folder.

## Accepted and deferred work

- The MVP dirty Telegram change and tests prevent synthetic Butler/user governance votes in all modes. The root OpenClaw dirty `ValueError` → `TypeError` edit was not ported: MVP already raises `TypeError` and catches it correctly; the old edit would have made it uncaught. Two regression tests were ported instead.
- Root dirty Ruff blanket suppressions, redundant inline suppressions, unmotivated lockfile re-resolution, and an exploratory Pollinations probe were saved outside the repository but not included in production. The root Git stash records the tracked edits for possible review; no stash was applied atop newer MVP code.
- The tool-policy `ToolRegistry.call()` hook and SSRF preflight/redirect checks were ported with tests. The beta tool policy is **not connected** to the production orchestrator; `web_fetch` DNS preflight has a rebinding gap and is **not approved for beta use**.
- `feat/beta-integration` commit `0d86444` remains a WIP branch, not a safe whole-branch merge: it requires trusted Telegram principal propagation and fails to block execution on audit-write failure. Its orchestrator and integration tests need a separate review and wiring checkpoint before production. This is a deliberate non-merge, not lost data.

## Acceptance and boundaries

- Canonical folder contains all committed MVP history and reviewed, tested changes; superseded, unsafe, or unfinished work remains recoverable in its original branch, snapshot, or stash. No claim is made that every experimental branch is production-ready.
- Offline tests run with `AGENCY_LLM_PROVIDER=echo AGENCY_LLM_MODEL=echo`; quality gate failures are reported, not hidden.
- Runtime DBs, active Telegram poller, credentials, and remote branches remain unchanged. 'Merged' means local Git integration into canonical `main`, not pushed or deployed. The existing beta and peer-governance blockers remain blockers.
