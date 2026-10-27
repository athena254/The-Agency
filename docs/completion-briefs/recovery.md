# Recovery completion — Agent Implementation Brief

## PROJECT BRIEFS
The Agency is an MIT Python 3.11 security-first agent runtime. This task completes only the existing local workflow recovery slice, not peer authority. Source contract: docs/SPEC_PRODUCTION_CONSOLIDATION.md and docs/SPEC_LOCAL_RECOVERY_V1.md.

## PITFALLS CATALOG
- P-R1 Tests currently contain only one happy-path recovery test. Counter-check: enumerate tests and compare frozen spec section 9.
- P-R2 `resume` returns COMPLETED before checking actor/input binding. Counter-check: regression must deny foreign actor/wrong inputs even for a completed record.
- P-R3 A worktree is NOT a sandbox. Never read .env*, runtime data/, credentials or other worktrees. Use only synthetic tmp_path databases.
- P-R4 Timeout is not lock ownership. Prove cross-process OS locking and kernel release after kill; no time-based lock stealing.
- P-R5 A passing callback is not durable completion. Inject intent/outcome persistence errors; do not fabricate success or automatically repeat unknown effects.

## PRE-EXECUTION COUNTER-CHECKS
Run `git status --short`; inspect the three workflow modules and existing tests. Post CHECK-1..N with file:line evidence BEFORE editing.

## GOAL
Meet the existing recovery contract with real tempfile SQLite and real child-process failure tests; fix concrete bugs discovered by regressions without changing legacy run semantics.

## NON-GOALS
No new CLI/framework/dependencies, production/beta enablement, models/tools, runtime DB access, clock leases, exactly-once claims, unrelated formatting, git branch/merge/rebase/reset/stash/push or network calls. You may make ONE scoped commit after verification. Do not modify another worker's files.

## TARGET ARCHITECTURE
Keep signatures: WorkflowExecutor.run_recoverable(workflow_id,version,inputs,actor_id), resume(run_id,inputs,actor_id), reconcile(), inspect_recoverable(run_id). Configured replay_safe and operation_versions are trusted construction inputs; permissions require exact True. File-backed FULL-sync SQLite and OS lock gate every recoverable writer.
Owned files ONLY: src/agency/workflows/executor.py, src/agency/workflows/recovery.py, src/agency/workflows/registry.py, tests/test_local_recovery_v1.py.

## TASKS
1. Read-only map file -> symbol -> role; STOP at that checkpoint and print the map before proceeding with the already-authorized implementation.
2. Add focused failing regressions, run them and record RED. Cover completed actor/input bypass; real child killed mid-callback, lock denial while alive, release after kill, reconcile/resume without repeating acknowledged prefix; unsafe remainder no callback; wrong input/actor/definition/op-version; strict authorizer; persistence failure; memory/legacy refusal; reopen durability; JSON bounds.
3. Fix smallest verified gaps; preserve contracts. If additional defect falls outside ownership, report it rather than editing another module.
4. Run focused tests, Ruff check/format and workflow mypy. Remove unused test imports; close every created DB and terminate/wait every child in finally. Do not hide failures by weakening tests.

## ACCEPTANCE / VERIFICATION
Use C:/Users/alphi/theagency/.venv/Scripts/python.exe as interpreter, with PYTHONPATH pointing to THIS worktree's src, AGENCY_LLM_PROVIDER=echo AGENCY_LLM_MODEL=echo. `python -m pytest tests/test_local_recovery_v1.py tests/test_workflow_registry.py -q` passes; `python -m ruff check` and `ruff format --check` on owned files pass; `python -m mypy src/agency/workflows` passes; `git diff --check` passes. Never run the full suite in the canonical runtime checkout.

## REPORT BACK
Commit only listed owned paths as `fix(recovery): qualify durable crash recovery and bound resume identity`. Report commit, RED/GREEN outputs, exact test count, decisions, file:line evidence, and remaining limits. Do not include private data or claim production readiness.
