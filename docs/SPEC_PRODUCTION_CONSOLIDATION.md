# Consolidation and completion checkpoint

Date: October 15, 2026. Owner authorized local branch/worktree consolidation, committing completed source, free OpenCode workers for unfinished work, reviewed integration, corrected documentation, and a production deployment plan. No push, deployment, live-provider enablement or consequential-action authorization is implied.

## Preserve before changing history

All four local branch tips and all three registered worktree source snapshots were preserved outside the repository, with binary patches, file SHA-256 manifests and consistent SQLite backups. A Git bundle was recovered into a separate bare repository and every branch tip compared. Private archive location is recorded in the completion evidence, not published credentials. Do not commit credentials, runtime databases, sidecars, worker scratch inputs or exploratory live-network probes.

`main` was fast-forwarded from `c2da9fd` to `7bc5987`, retaining the Butler context checkpoint and both reviewed candidate merges. `feat/free-metering` and `feat/free-recovery` are already ancestors of that commit; no second implementation merge is needed. The runtime database blob is unchanged across those source revisions. Original branch refs may be removed only after preservation and ancestry verification. Retire the old worker worktrees after confirming no process owns them and all untracked/private inputs are preserved. Disposable environments/caches are not product work.

## Exact completion scope

This pass finishes and qualifies **the already-started local v1 slices**, not every long-term feature in the 51-section product brief. Work uses frozen `SPEC_LOCAL_RECOVERY_V1.md`, `SPEC_MODEL_METERING_V1.md`, the implemented Butler thread boundaries, and the owner authorization above. Do not implement an unapproved consensus policy, autonomous factory, public authentication service or production release pipeline merely to claim all work done. Outstanding product capabilities become explicit gated items in `PRODUCTION_READINESS_PLAN.md`.

### Recovery worker

Owned: `src/agency/workflows/{executor,recovery,registry}.py`, `tests/test_local_recovery_v1.py`.

Keep legacy `run()` behavior separate. Complete and test the existing exact APIs `run_recoverable`, `resume`, `reconcile`, `inspect_recoverable`. Require file-backed SQLite, OS lock ownership (no time-based stealing), FULL durability, acknowledged step outcomes, digest-bound input/actor/definition/operation versions, strict boolean authorization and trusted replay-safe configuration. Check identity/input binding even when resuming a completed run. Prove real child kill -> OS-lock release -> reconcile -> safe resume, completed steps not repeated, unsafe tail refused, concurrent process lock denial, wrong binding denial, persistence failure propagation and legacy/in-memory refusal. Unknown effect is never exactly-once success. Every discovered bug gets a failing regression first.

### Metering worker

Owned: `src/agency/llm/metered.py`, `src/agency/tools/driver.py`, `tests/test_model_metering.py`, `tests/test_tools_driver.py`.

Keep beta ingress disabled. Complete the **internal** request-bound metered ToolDriver seam; fixed concrete adapter/budget/audit, immutable valid identity/request binding, no context overrides or silent provider drift, no automatic fallback. Reserve before intent before one dispatch before durable redacted outcome. Quota/identity/audit denial spends zero calls; unknown timeout/cancellation stays fail-closed, no repeated unknown provider call. Validate configured provider/model remains bound at dispatch, positive request identifiers and principal agreement. Test forged/inconsistent binding, mutation, cancellation, concurrent requests, quota ceilings, persistence failures and absence of prompts/capabilities in errors/audit/repr. Reconcile any discrepancy with the frozen contract explicitly; never enable Telegram models/tools to make a test pass.

### Butler worker

Owned: `src/agency/butler/{commands,context,service,threads}.py`, `src/agency/telegram/handler.py`, `src/telegram_bot.py`, `tests/test_butler_context.py`, new `tests/test_butler_thread_transport.py`.

Finish the existing thread feature as a **nonbeta private Telegram** path, with `/new`, `/threads`, `/switch`, `/current`, stable numeric-user ownership and explicit trusted-channel opt-in. Handle bot-command suffixes consistently, never switch by shared username/chat identity. Prove persistence and two-user isolation, no private thread context in groups, deterministic commands require zero model calls, ambiguity/unknown commands do not ask an LLM. Keep existing beta admission, request accounting, model/tool-disabled gates, HTTP health-only gates and isolated browser chat unchanged. Preserve non-thread callers and test doubles. Harden thread-context public methods against foreign owner reads/writes. Bounded context is not lossless history: raw transcript remains durable; summarize/recall limitations must be stated. Fix formatting only in owned files.

## Integration and acceptance

Workers must read current code and post a usage map before editing, add focused failing tests before fixes, stay in non-overlapping ownership, use only the explicitly selected verified free model, and commit only owned paths. They may not inspect `.env*`, runtime `data/`, credentials, other worktrees, or change branches/remotes or rewrite Git history. No live API calls, installs or service launches. Coordinator owns integration commits, Git cleanup, `.gitignore`, status docs, final formatter pass and integrated verification. Temporary branches/worktrees are allowed during parallel work; final state is one canonical worktree on `main` and at most one working branch.

Verification must use an isolated test working directory with `data/`, the verified Python 3.11 project interpreter and `PYTHONPATH` to the integration source. `AGENCY_LLM_PROVIDER=echo`, `AGENCY_LLM_MODEL=echo`; external network blocked, Windows loopback preserved. A fresh single full suite must pass; focused passes do not replace it. Run Node prototype UI tests, repo-wide Ruff lint/format, strict mypy and `git diff --check`. Preserve warning counts rather than hiding resource cleanup debt. Record exact tested source commit and distinguish subsequent documentation-only commits. No runtime-data rewrite.

## Research and design rationale

Prefer fast-forward integration over synthetic merge commits when history is already contained; recoverable Git bundle plus source snapshots over destructive resets; kernel-owned file locks over clock leases; explicit unknown states over retrying unknown side effects; durable request capability binding over caller identity dictionaries; bounded test-backed completion over speculative framework expansion. Local free-model comparison measures correctness and elapsed time on one identical small Python/security task; it cannot establish universal model superiority or long-term free pricing.
