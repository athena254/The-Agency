# PR #7 — Independent boundary review (read-only)

Project: The Agency, Python 3.11+ MIT. This worktree is detached at the draft PR head; separate from owner's uncommitted main. See docs/SPEC_PR7_READINESS.md and docs/SPEC_AGENCY_CORE_RECONCILIATION.md. This is an OFFLINE, defensive code review. No external requests, no exploit development, no writes, no commits.

PITFALLS: P-R1 public HTTP `sender` is untrusted; check that it cannot select persistent owner thread. P-R2 Forge directory entries can change during inspection; check symlink/path escaping and read-only claim. P-R3 absorber may write outside intended root or commit unexpectedly; check defaults/callers. P-R4 initial review was inconclusive; never call it approved without evidence.

PRE-CHECK: `git status --short`; `git rev-parse HEAD`; inspect docs/SPEC_PR7_READINESS.md, src/agency/butler/server.py, src/agency/butler/service.py, src/agency/butler/threads.py, src/agency/forge/inspection.py, src/agency/addons/dark_factory/absorption.py, src/agency/addons/dark_factory/factory.py and associated tests. Report CHECK-1..N and map file:symbol:caller before analysis.

GOAL: Independently find and reproduce any remaining integrity/security failure in the three named local boundaries. Use read-only analysis or in-memory/tmpdir probes; do not change tracked or untracked files in this worktree. No interaction with running services or actual user data.

NON-GOALS: no public network, no secrets, no edits to files, no changes to PR, no approval event.

TASKS: (1) Map the three call boundaries with file:line references. (2) Examine auth/ownership assumptions, race/path resolution, and write/commit defaults. (3) For suspected issues, reproduce with self-contained tempdir/SQLite tests or show exact triggering path. (4) Return JSON object with `passed` boolean, `findings` list of objects {severity,file_line,trigger,impact,evidence,recommended_fix}, `coverage` list, and `limitations` list. For no findings, explain negative test coverage. Do not make up results.

VERIFICATION: `git status --short` remains empty; report actual commands and outputs. This is not a product security audit.
