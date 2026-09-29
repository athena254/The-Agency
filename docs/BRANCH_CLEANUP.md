# Branch cleanup checkpoint

The owner requested merging completed work and retaining only active branches.
Completed work was already integrated at `fef7520`. This cleanup changes no
application code, tests, live settings or runtime data. It does not enable beta.

## Second pass (2026-09-29) — current state

The owner again asked to merge agency branches down to the ones in active
development and clean the finished ones. Verification first, deletion second:

- Nothing finished remained unmerged. Both finished branch tips were already
  ancestors of `main` (`git merge-base --is-ancestor` true and
  `git rev-list main..<branch>` = 0), so no merge commit was possible or needed.
- `docs/free-lattice-gateway` and `research/free-bft-lab` were therefore retired
  as `already-merged-ancestor-of-main`, using `git branch -d`, which refuses to
  delete a branch that is not fully merged. Both deletions succeeded.
- Their checkouts were only detached at the same commit, so no directory or file
  changed and a stale Freebuff process still holding one of them is unaffected.
- Three local branches remain: `main` plus the two unfinished candidates
  `feat/free-metering` and `feat/free-recovery`. These are the only lines with
  work that is not in `main`; they are preserved, not merged and not deleted.

Machine-readable evidence, backup path, bundle hash and recovery commands are in
`research-evidence/branch-cleanup-pass2.json`.

## Checkout folder cleanup (2026-09-29, same run)

The owner asked why so many folders remained and to delete them if already
merged. Each of the 43 non-canonical folders (37 registered checkouts plus 6
unregistered folders) was classified before anything was removed. Of the 31
checkouts selected for removal, 16 had HEAD already merged into `main` and 15
were patch-equivalent (`git cherry main <HEAD>` reported zero unique patches).
None held unique work. No folder was removed on the strength of its name.

- 30 merged checkouts were removed after their local content was copied to the
  backup: runtime `lattice.db` files, every untracked file (`.clineignore`,
  briefs, `.agent-input/*`, untracked docs and `recovered-evidence.md`), and a
  `tracked-mods.patch` that was empty in every case. Only reproducible `.venv`
  directories and ignored caches were not copied.
- Three non-checkout folders were also cleared: the empty `agency-combined-verify`
  scratch directory and the two brief folders (preserved first).
- `agency-deploy-next` was removed and then **restored** at the identical commit
  (`a997abc`): `docs/BETA_GAPS.md` B-09 and two pinned launchers still use it.
- `agency-free-bft-lab` lost its files but the empty directory cannot be deleted
  while a live `cline.exe` (PID 1568) uses it as its working directory. Nothing
  was lost: `18b9717` is in `main` and tagged.
- Two folders could not be deleted at all because live processes occupy them:
  `agency-free-bft-lab` (Cline) and `agency-free-lattice-gateway` (Freebuff).

Twelve folders remain, each for a stated reason: `theagency` (canonical),
`agency-deploy-3417c79` (live deployment), `agency-deploy-main` and
`agency-deploy-next` (rollback deployment sources), `agency-mvp` (unmerged local
source edits plus the running bot's virtual environment), the two active-branch
checkouts, the two process-bound checkouts, and the three backup folders
(`agency-backups`, `agency-runtime-backups`, `agency-consolidation-snapshot-20260927-105944`).

Documents that name removed checkouts (for example `agency-free-program` in
`SPEC_FREE_ONLY_DECENTRALIZED_PROGRAM.md` and the evidence snapshots) are
historical evidence statements, exactly like the retired branch names above.

## Deployment and archive consolidation (2026-09-29, same run)

The owner asked again to clear the remaining look-alike folders whose code is
finished. Two unused deployment checkouts and two archive folders were removed
without stopping any process, because nothing running depended on them:

- `agency-deploy-main` (`4cf28a7`) and `agency-deploy-next` (`a997abc`) were
  clean, their HEADs are ancestors of `main`, and no process was using them. Only
  dormant launchers (`agency_run_merged.py`, `agency_run_a997abc.py`,
  `agency_run_8399b4f.py`) named them; both are recreated with one
  `git worktree add --detach` command recorded in the backup and evidence file.
- `agency-runtime-backups` and `agency-consolidation-snapshot-20260927-105944`
  were moved inside `agency-backups/`, so the archives now occupy one folder
  instead of three. No archive content was deleted.
- `agency-mvp` was inspected rather than assumed: its local edits are an **older**
  pre-consolidation state that `main` already supersedes, not unfinished new work.
  Its only live dependency is the `.venv` that the running bot interpreter uses.

Eight folders remain, each blocked by a running process or holding the only copy
of something:

- `theagency`: canonical repository.
- `agency-deploy-3417c79`: deployment pinned by the launcher that started the
  running bot; deleting it breaks a pinned restart.
- `agency-mvp`: supplies the running bot's virtual environment.
- `agency-free-lattice-gateway`: cwd of live Freebuff processes.
- `agency-free-bft-lab`: empty directory whose deletion Windows blocks while a
  live `cline.exe` hub keeps it as its cwd.
- `agency-free-metering`, `agency-free-recovery`: the only branches with work not
  in `main`, so they are not finished code.
- `agency-backups`: now the single archive folder (bundles, local-content copies,
  runtime database backups, consolidation snapshot).

Removing any of the first five requires stopping the Telegram bot or the idle
agent processes, which was left to explicit owner approval.

## Remaining local branches — pass 1 snapshot (2026-09-28, historical)

This list described the state after the first cleanup. It is kept for provenance:
`docs/free-lattice-gateway` and `research/free-bft-lab` were retired by the
second pass above.

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
