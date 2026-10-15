# Butler thread completion — Agent Implementation Brief

## PROJECT BRIEFS
The Agency is an MIT Python 3.11 security-first runtime. Existing Butler commands/context are implemented internally but commands_enabled is not wired from Telegram. Complete that existing feature only for nonbeta PRIVATE Telegram, retaining all closed security boundaries. Butler is a human gateway, not an agent or authority.

## PITFALLS CATALOG
- P-B1 commands_enabled=True enables owner-state persistence. Counter-check: opt in only nonbeta private messages with a valid stable numeric Telegram sender, never group chat/anonymous HTTP/beta.
- P-B2 Public record_turn accepts owner/thread_id without visibly checking ownership. Counter-check: foreign owner cannot write thread-scoped recall.
- P-B3 Bot-command suffixes and unknown commands must not reach the LLM. Counter-check /new@RemexMemorybot plus /unknown are deterministic; preserve existing /start,/status,/research,/name behavior.
- P-B4 Beta direct-dict calls are untrusted, models/tools disabled. Counter-check beta admission and request-ledger regressions remain unchanged and no beta thread path opens.
- P-B5 Worktree is not a sandbox: never read .env*, runtime data/, credentials or other worktrees; no network/service/model calls.

## PRE-EXECUTION COUNTER-CHECKS
Run git status, read handler.py, service.py, commands.py, context.py, threads.py and related tests. Post CHECK-1..N with file:line before editing.

## GOAL
Expose /new,/threads,/switch,/current through existing private nonbeta Telegram with stable per-user ownership and persistent active-thread continuation; finish context ownership checks. No new product architecture.

## NON-GOALS
Do not enable beta models/tools, HTTP routes, governance creation/votes, new provider calls, schema frameworks, dependencies or UI redesign. No .env/runtime reads or git branch/merge/rebase/reset/stash/push. One scoped local commit allowed. Do not modify metering/workflow modules/tests.

## TARGET ARCHITECTURE
Telegram handler -> Butler.handle_message(..., commands_enabled=True) only for validated private nonbeta owner path; numeric sender scope must be same on deterministic commands and regular follow-up. Existing explicit thread context and ThreadStore ownership remain authoritative. Unknown commands deterministic; suffix normalization only for this bot. Preserve typed/mock compatibility and legacy callers without adding untrusted flags. Commands requiring no reasoning use zero model calls. Raw history persists; capped extractive summary is not lossless memory.
Owned files ONLY: src/agency/butler/commands.py, context.py, service.py, threads.py (all under butler/); src/agency/telegram/handler.py; src/telegram_bot.py; tests/test_butler_context.py; NEW tests/test_butler_thread_transport.py.

## TASKS
1. Map current handler -> Butler flow and STOP to print before authorized implementation.
2. Add RED transport regressions for all commands, bot suffix, unknown command, two users, group/private separation, missing sender, reopen/restart persistence and no LLM on commands. Public context operations must deny foreign ownership. Use actual synthetic temp stores/real Butler where useful, not only mock keyword assertions.
3. Wire the smallest compatible path, keeping beta early guards and ordinary existing command handlers. Add new Telegram menu entries only if correctly scoped to nonbeta, and test; no bot restart/live menu change.
4. Format owned existing files, run focused tests including baseline admission/security tests. Close every resource you create; avoid leaking DB threads.

## ACCEPTANCE / VERIFICATION
Use C:/Users/alphi/theagency/.venv/Scripts/python.exe and this worktree/src in PYTHONPATH, AGENCY_LLM_PROVIDER=echo AGENCY_LLM_MODEL=echo. `python -m pytest tests/test_butler_context.py tests/test_butler_threads.py tests/test_butler_thread_integration.py tests/test_butler_thread_transport.py tests/test_telegram_handler.py tests/test_beta_telegram_admission.py tests/test_beta_telegram_delivery.py tests/test_beta_transport_admission_slice.py -q` passes. Ruff check/format on owned files, source mypy and git diff --check pass. Fix only owned source/tests; report cross-scope blockers.

## REPORT BACK
Commit only owned files as `feat(butler): finish private Telegram thread command integration`. Report RED/GREEN outputs, exact tests, changed entry paths, isolation proof and remaining limits. No production, beta readiness or live-provider claims.
