# Worktree consolidation — 2026-10-22

## Owner authorization and scope

The owner requested: “start cleaning the worktrees in agency to a few unique ones, others merge.” This checkpoint specifies repository housekeeping and consolidation, not new product implementation, deployment, beta enablement or permission to discard conflicting work.

Starting inventory: **26 worktrees**. Integration baseline: `760b9a99d3d3860bb8c27e4fa19f5456b7de9bf6` (`feat/limited-autonomy-20261019`). Canonical `main` and its runtime databases/shared venv stay in place and are not switched or fast-forwarded.

## Four retained worktrees

1. `C:/Users/alphi/theagency` — original canonical checkout; credentials, runtime state and shared `.venv` remain untouched. Two existing shell processes reference it.
2. `C:/Users/alphi/agency-worktrees/consolidated-20261022` — consolidated integration/housekeeping branch `consolidation/worktrees-20261022`, starting from `760b9a99`.
3. `C:/Users/alphi/agency-worktrees/daily-launcher-20261020` — distinct unfinished launcher candidate, preserved dirty and unchanged.
4. `C:/Users/alphi/agency-worktrees/pilot-launcher-completion-20261020` — distinct unfinished launcher candidate, preserved dirty and unchanged.

The final count is verified from Git after removal, not assumed from this plan. Original branch refs, the existing public recovery branches/tags and stashes remain available; physical worktree retirement does not delete their histories.

## Preservation and safety gates

- Complete remote backup was already independently downloaded, authenticated, decrypted and restored: private `athena254/hermes-machine-backup-20261021`, release `machine-migration-20261021`; publication receipt on private main `e971bd97942d65a8e420b1f4857c36db53eb06a6`.
- Fresh pre-cleanup snapshot: `C:/Users/alphi/agency-backups/worktree-consolidation-20261022/`.
- All **19 uncommitted source files** and the one non-regenerable ignored file in retiring trees were copied and SHA-256 checked. Their bytes also match the already verified remote Agency archive. Binary tracked diffs and the original statuses are retained outside the repository.
- A fresh `pre-consolidation.bundle` was verified, cloned into a bare recovery repository, checked against **216 refs**, and passed `git fsck --full --no-reflogs`.
- Owned-process command/cwd/environment path selectors and wrapper references were inspected; no retiring worktree was referenced. The original canonical checkout is retained. Four Windows helper processes denied cwd/environment inspection (`ctfmon.exe`, `taskhostw.exe`, `DefenderSessionHelper.exe`, `SynTPHelper.exe`); this is an explicit inspection limit, not proof of complete OS-wide visibility.
- Current default-profile cron/service/config definitions contain no retiring-worktree path references. No production process is stopped or restarted.
- A synthetic test proved that `git worktree remove` **can delete ignored files even without `--force`**. Therefore retirement requires its own ignored-state gate: no private/runtime files may remain in a retiring tree. Only recognized regenerated Python/pytest/mypy/Ruff caches may be discarded.
- Dirty historical notes/tests in retiring trees are first consolidated or preserved, then explicitly moved to the checked private retirement archive; their disappearance is not a blanket `git clean`, reset or forced removal. The two distinct launcher candidates are not cleaned.
- No branch deletion, force-push, unrelated-history merge or `--ours` resolution is part of this operation.

## Per-worktree decision matrix

| Original worktree | Classification | Action after preservation |
|---|---|---|
| `theagency` | Ancestor baseline, owns canonical runtime/venv | Keep unchanged |
| `agency-status-20261018` | Ancestor of baseline | Retire directory |
| `autonomy-admission-a3-20261016` | Ancestor | Retire directory |
| `autonomy-context-a1-20261016` | Ancestor | Retire directory |
| `autonomy-verification-a2-20261016` | Ancestor | Retire directory |
| `bounded-autonomy-20261016` | Ancestor | Retire directory |
| `daily-harness-20261020` | Unique repaired historical harness | Merge final harness/history, then retire directory |
| `daily-launcher-20261020` | Distinct unqualified dirty launcher | Keep unchanged |
| `daily-route-qualification-20261019` | Original harness superseded by its repaired descendant | Preserve through repaired harness merge, retire directory |
| `limited-autonomy-20261019` | Exact baseline of consolidated branch | Retire duplicate directory |
| `owner-admission-20261019` | Patch-equivalent to baseline integration | Retire directory; keep refs |
| `owner-api-red-20261020` | Assertion already exactly present in expanded integration test | Retire directory; preserve RED history, do not regress tests |
| `owner-context-20261019` | Patch-equivalent | Retire directory |
| `owner-core-20261019` | Ancestor; older untracked test is subsumed, tracer note unique | Consolidate note, archive old test as inactive text, retire directory |
| `owner-httpguard-20261019` | Patch-equivalent | Retire directory |
| `owner-metered-20261019` | Patch-equivalent | Retire directory |
| `owner-policy-20261019` | Patch-equivalent; unique untracked brief/RED JUnit plus ignored raw log | Consolidate notes/evidence; retain raw log privately; retire directory |
| `owner-provider-snapshot-20261019` | Patch-equivalent | Retire directory |
| `owner-service-20261020` | Semantically incorporated; integration adds required shutdown fence | Retire directory; never overwrite stronger integration service |
| `owner-tools-20261019` | Patch-equivalent | Retire directory |
| `owner-ui-races-20261019` | Patch-equivalent | Retire directory |
| `owner-uncertainty-20261019` | Patch-equivalent | Retire directory |
| `pilot-baseline-20261019` | Patch-equivalent | Retire directory |
| `pilot-launcher-completion-20261020` | Distinct unqualified dirty launcher | Keep unchanged |
| `pilot-metering-leaf-20261019` | Patch-equivalent | Retire directory |
| `pilot-ui-20261019` | Patch-equivalent; research/build briefs unique | Consolidate briefs, retire directory |

## Safe consolidation payload

- Merge repaired historical route tooling from `bf214e8fdb673aed21e863e7c1b6d0a917ef1072`, including its ancestor `97c72f29f3fa7d69421e9d0ab72ccf820f3b3ba3`. The entire three-dot diff is four files under `workspace/daily-route-qualification/`.
- Preserve the harness as **historical and baseline-bound**. Its embedded 165-file trusted manifest is stale for current integration; its baseline-dependent self-check fixtures must not be relabeled as current qualification. Do not regenerate trusted pins during cleanup.
- Take only the two-file notes/evidence checkpoint `0abb32ae30eb4cadd569ca0c6f79f64ae16b985f` and the two-file UI notes checkpoint `a97da70b784576db605385d472eb519e84618e0a`.
- Preserve `workspace/owner_core_tracer_red_20261019.md` verbatim. Preserve the older untracked owner runtime test under `workspace/worktree-retirement-history/owner-core-20261019/test_owner_runtime.py.txt`, outside the active test suite.
- Keep `src/`, `tests/`, dependency manifests and runtime/config implementation exactly equal to baseline `760b9a99`.

## Why the older source variants are not merged

Direct parent verification confirmed:

- Both the dirty daily launcher’s `admission/service.py` and `59648d14` differ from integration only by **omitting** `self._store.fence_publication()` in shutdown. Integration's stronger fence stays.
- Integration’s owner runtime test equals the older untracked test plus `assert admitted.task_id is not None`. No useful assertion is missing.
- The complete `test_unconfigured_pilot_capabilities_is_disabled_503` AST from the API RED commit is already present in integration.
- The two dirty launchers are not behaviorally identical. Completion implements corrupt-token validation, but its narrowed validation catches omit `PilotError`, which `OwnerPrincipal` can raise for invalid identifiers. Daily's broad catch handles that case but lacks the token-validation implementation. These are static findings, not newly executed product-test results.

## Verification and reporting

After merging, verify all baseline runtime/test/config/dependency paths are unchanged. Syntax-parse the preserved Python tooling without importing Agency or touching runtime databases. Preserve and label historical RED/unqualified evidence rather than advertising a new full-suite pass.

Before each directory retirement, recheck branch HEAD/status, process references, backup file hashes and the ignored-state gate. Remove without `--force`, verify its absence from both Git's worktree list and the filesystem, and verify its branch ref remains unchanged. Finally verify retained launcher files/statuses and canonical runtime/venv, publish this checkpoint and read back the exact remote SHA.

This cleanup does not merge into canonical `main`, deploy the Agency or qualify either unfinished launcher.
