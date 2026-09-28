# Branch cleanup checkpoint

The owner requested merging completed work and retaining only active branches.
Completed work was already integrated at `fef7520`. This cleanup changes no
application code, tests, live settings or runtime data. It does not enable beta.

## Remaining local branches

- `main`: integrated, verified baseline.
- `feat/free-metering`: unfinished candidate; focused tests fail.
- `feat/free-recovery`: unfinished candidate; executor integration missing.
- `research/free-bft-lab`: retained because a running Cline process uses its checkout.
- `docs/free-lattice-gateway`: retained because a running Freebuff process uses its checkout.

A running worker is not evidence of coding progress. These process-bound branches
are deliberately not removed while their owners may still use them.

## Retired branches

75 local branches became 5; 70 obsolete branch names were removed:
- 25: `already-merged`.
- 42: `patch-equivalent-in-main`.
- 1: `archived-unsafe-experiment`.
- 2: `superseded-reviewed-port-in-main`.

Patch equivalence was checked with `git cherry main <branch>`, not inferred
from names. The two non-identical older beta policy/integrity branches were
reviewed against their ports (`2baaab7`, `ce66f64`) and the earlier consolidation
specification. Main contains the reviewed versions and subsequent hardening.
The old beta orchestrator experiment (`0d86444`) remains unsafe: its audit-error
catch continues execution. It was archived, NOT merged or marked safe.
Do not reintroduce it simply to make every historical branch an ancestor.

References to retired branch names in older documents are historical. Resolve
those branches through the archive tags or verified bundle, not by assuming
that the original branch still exists.

## Recovery and preservation

- Backup: `C:/Users/alphi/agency-backups/branch-cleanup-20260928-215259`.
- `all-refs.bundle` contains every original local branch; Git verified the bundle.
- Archive tags: `archive/cleanup-20260928-215259/<original-branch>`.
- The private backup includes inventory, binary tracked/index patches, and
  hash-verified copies of changed/untracked files. No private/runtime files were committed.
- The existing stash was retained unchanged.
- Retired checkouts were detached at the SAME commit without changing their files.
- Only three clean temporary worktrees were removed; dirty, runtime-bearing and
  deployment directories remain available. The MVP virtual environment is retained.
- No bot restart, provider call, GitHub push or remote branch deletion occurred.

Recover an old branch locally with:

```bash
git branch recovered-beta-integration archive/cleanup-20260928-215259/feat/beta-integration
```

The recovered experiment is still unsafe; this command does not approve merging it.

## Verification

The `src` and `tests` Git tree IDs match the previously verified combined revision
(1,205 Python tests and 14 UI tests). No new test execution is implied by those
historical counts. Cleanup verification checks branch coverage, archive tips,
bundle contents, unchanged local files, stash identity and exact remaining branches.
Machine-readable dispositions are in `research-evidence/branch-cleanup.json`.
