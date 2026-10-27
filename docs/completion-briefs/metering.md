# Metering completion — Agent Implementation Brief

## PROJECT BRIEFS
The Agency is MIT Python 3.11. Complete the existing dormant request-bound metered ToolDriver seam; do not enable beta or widen public ingress. Contracts: docs/SPEC_MODEL_METERING_V1.md and docs/SPEC_PRODUCTION_CONSOLIDATION.md.

## PITFALLS CATALOG
- P-M1 Constructor equality is not lifelong provider binding: inner adapter can be mutated after binding. Counter-check: change provider/model then assert zero dispatched calls and deterministic denial.
- P-M2 Caller dataclasses can carry inconsistent user_id/principal/request values. Counter-check: malformed identity/request bindings fail before provider, quota or audit side effects.
- P-M3 Cancellation/timeout/empty response have unknown outcomes, not safe retries. Counter-check: durable fail-closed audit/pending behavior, zero additional dispatch on uncertain retry, no success-shaped error text.
- P-M4 Worktree is not a sandbox. No .env*, runtime data/, secrets, other worktrees or live APIs.
- P-M5 Exhausted quota must use zero provider calls; failed outcome persistence cannot return a successful answer. Test concrete temporary budget/audit stores rather than replacing durability with duck types.

## PRE-EXECUTION COUNTER-CHECKS
Run git status; read metered.py, driver.py, budget_store.py, audit.py, adapter.py and relevant tests. Print CHECK-1..N and file:line findings before edits.

## GOAL
Validate immutable request/principal/provider binding, at-most-one dispatch per durable reservation, complete failure/cancellation/concurrency regressions, no regression to nonbeta behavior.

## NON-GOALS
No beta ingress enablement, provider configuration, adapter.py/budget_store.py/audit.py source edits, new models/dependencies, network calls, deployments, other workers' files, git branch/merge/rebase/reset/stash/push. A single scoped local commit is permitted.

## TARGET ARCHITECTURE
Existing MeteredBinding + MeteredModel -> concrete LLMAdapter, concrete BetaBudgetStore and BetaAuditLog. Every internal generate: validate -> reserve -> acknowledged pending intent -> bounded dispatch -> durable redacted outcome. Fixed provider/model; all ToolDriver iterations and forced final use this seam. No mutable global current-user slot, no fallback. Frozen binding provider/model overrides from caller contexts are refused.
Owned files ONLY: src/agency/llm/metered.py, src/agency/tools/driver.py, tests/test_model_metering.py, tests/test_tools_driver.py.

## TASKS
1. Map current callers and control flow read-only; STOP and print before authorized implementation.
2. Add RED regressions for binding identity/request type/values, principal private scope, mutated adapter drift, malformed context, cancel/timeout/concurrency/closed store/persistence failures/privacy. Inspect current tests first; extend not duplicate.
3. Make smallest fixes, preserve nonbeta behavior. Never automatically retry unknown provider outcomes. Report any frozen-contract discrepancy explicitly. Ensure test stores are closed with finally/fixtures so thread cleanup is not left pending.
4. Run focused tests and owned-file Ruff plus source mypy. Do not weaken existing denial assertions or enable ingress to satisfy tests.

## ACCEPTANCE / VERIFICATION
Interpreter C:/Users/alphi/theagency/.venv/Scripts/python.exe; PYTHONPATH points to THIS worktree/src; AGENCY_LLM_PROVIDER=echo AGENCY_LLM_MODEL=echo. Run `python -m pytest tests/test_model_metering.py tests/test_tools_driver.py -q`, Ruff check/format on owned files, `python -m mypy src/agency/llm/metered.py src/agency/tools/driver.py`, git diff --check. Tests are fake providers with real synthetic temp SQLite only. Record exact outputs.

## REPORT BACK
Commit only owned files as `fix(metering): enforce request binding and qualify failure paths`. Report commit, RED/GREEN proof, test count, confirmed pitfalls, deviations and remaining disabled beta scope. Never claim live provider/free pricing or production proof.
