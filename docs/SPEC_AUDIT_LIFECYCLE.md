# AuditLog Initialize/Close Lifecycle Spec (Same-Instance)

Status: SPEC — Wave A approved scope. No deployment or production-authority claim.
Base: `18b9717` branch `feat/free-audit-lifecycle`. License: MIT (`pyproject.toml`).
Owner: `src/agency/kernel/audit.py`, `tests/test_audit_lifecycle.py` (new),
`docs/SPEC_AUDIT_LIFECYCLE.md` (this file). No other edits. No governance additions.

## 1. CHECK map (pre-execution, P-1..P-3 confirmed)

- P-1: tests use `AGENCY_LLM_PROVIDER=echo AGENCY_LLM_MODEL=echo PYTHONPATH=src`,
  interpreter `C:/Users/alphi/agency-mvp/.venv/Scripts/python.exe`.
- P-2: worktree is not a sandbox. No `.env*`/`data/` reads, no live models,
  installs, bots, provider changes, push/merge/deploy.
- P-3: green tests are not deployment proof. Exact red/green outputs only.

- CHECK-1 `src/agency/kernel/audit.py:AuditLog.__init__` — stores `_db_path`,
  `_table`, `_synchronous NORMAL|FULL`, `_conn=None`, structlog `_log`. No lock.
- CHECK-2 `AuditLog.initialize` — `if _conn is not None: return`; `mkdir`,
  `aiosqlite.connect`, WAL 4-attempt locked-retry with sleep backoff,
  `PRAGMA synchronous`, `CREATE TABLE/INDEX IF NOT EXISTS`, `commit`;
  `except BaseException: await conn.close(); raise`; publish `self._conn=conn`.
- CHECK-3 `AuditLog.close/__aenter__/__aexit__` — `close`: if `_conn` commit,
  close, clear to None. Enter calls init, exit calls close.
- CHECK-4 `append/query/count/_require_ready` — raises RuntimeError when
  `_conn is None`. Append INSERT+commit, ValueError on PK collision.
- CHECK-5 `BetaAuditLog` — rejects empty/`":memory:"`/`file:` paths, forces FULL.
  Inherits lifecycle. `claim_intent` uses separate BEGIN IMMEDIATE connection;
  `append_beta` verifies WAL+FULL+disk plus read-back ack. Preserve FULL.
- CHECK-6 `tests/test_audit.py` — 7 tests: roundtrip, filters, immutable,
  duplicate-id, reopen, not-initialized, action roundtrips.
- CHECK-7 `tests/test_kernel_audit.py` — 9 tests incl. context-manager and
  `test_beta_initialize_recovers_from_transient_wal_lock` (one locked WAL retry).
- CHECK-8 program `docs/SPEC_FREE_ONLY_DECENTRALIZED_PROGRAM.md` — research only.
- CHECK-9 `pyproject.toml` — MIT, py>=3.11, asyncio_mode=auto, ruff py311/100,
  mypy strict.
- CHECK-10 `tests/test_audit_lifecycle.py` + this spec did not exist pre-task.

## 2. Same-instance races in current code (base 18b9717)

- R-1 concurrent `initialize`: both see `_conn is None`, each opens own
  connection, loser never closed/published — leaked unpublished connection.
- R-2 `initialize`-vs-`close`: close can clear/close mid-setup, or init can
  publish after close returned, leaving live `_conn` after close.
- R-3 concurrent `close`: both pass `is not None`, double commit/close same object.
- R-4 failure/cancel: local `except BaseException: close` covers setup failure,
  but R-1 losers escape scope; sleep backoff is retry tolerance, not exclusion.
- R-5 close-failure ack: current code keeps `_conn` on exception incidentally,
  untested; a clear-first refactor would falsely ack failure as durable.

## 3. Contract (normative)

- C-1 one live connection per instance: at most one published `_conn`; losers
  never leak; unpublished connections always closed on failure/contention/cancel.
- C-2 serialize same-instance `initialize`/`close` via instance `asyncio.Lock`
  (no new deps). Recheck `_conn` after acquiring lock; enter/exit inherit it.
- C-3 repeated `close` safe/idempotent: post-success `close` is no-op None;
  concurrent closes serialize, single commit/close.
- C-4 failed `initialize` publishes nothing, closes unpublished conn, including
  `CancelledError`/any `BaseException`; `_conn` stays None; original exc raised.
- C-5 `close` failure never a durable ack: commit/close raise -> propagate,
  `_conn` NOT cleared, no success log/status. Clear only after both succeed.
- C-6 preserve BetaAuditLog FULL + existing API: no change to append/query/count/
  claim/append_beta/has_pending_calls; disk path, WAL+FULL, read-back ack stay.

## 4. Minimal design (implements C-1..C-6, nothing more)

- Add `self._lifecycle_lock = asyncio.Lock()` in `AuditLog.__init__`.
- `initialize`: `async with lock:` recheck `_conn`; local `conn`; existing
  WAL-retry + schema + commit; `except BaseException:` best-effort close of
  unpublished `conn` (suppress close errors, re-raise original incl. Cancelled)
  without publishing; else publish `self._conn=conn`. Pre-lock fast-path check
  is optimization only; post-lock recheck authoritative.
- `close`: `async with lock:` `if _conn is None: return None`; snapshot local
  `conn`; `await commit(); await close()`; only then `_conn=None` + success log.
  Any exception propagates with `_conn` unchanged. No swallow, no success log.
- Cancellation: lock releases on cancel; unpublished `conn` closed in the
  BaseException path; published `_conn` never cleared on cancelled close (C-5).
- No table-name change, no new retry policy, no sleep-based mutual exclusion.

## 5. TDD test plan (`tests/test_audit_lifecycle.py`, temp SQLite, no new deps)

Deterministic, no sleeps as correctness mechanisms; Barrier/Event + monkeypatched
`aiosqlite.connect` / `Connection.execute` to force interleavings:

1. concurrent-initialize single connection — N inits at once; one live `_conn`,
   opener count 1 (losers closed); usable append/query; clean close. RED on base.
2. initialize-vs-close serialized — init blocked mid-setup while close runs;
   terminal state clean-closed or fully-ready, never half-published.
3. repeated close idempotent — init, close, close -> None no raise; concurrent
   double-close serializes without double-close error.
4. failed init closes unpublished — forced schema failure; raises, `_conn None`,
   unpublished closed, later init can succeed.
5. close failure not acked — forced commit (then close) failure; raises,
   `_conn` retained, retry can succeed; beta failed close not a durable ack.
6. cancel init no leak — cancel mid-setup; CancelledError, `_conn None`,
   unpublished closed.
7. beta preservation — existing WAL-retry + FULL/read-back ack tests stay green.

Then focused `test_audit.py + test_kernel_audit.py + test_audit_lifecycle.py`,
`ruff check`, `ruff format --check`, `mypy` strict. Exact outputs reported.

## 6. Risks / non-goals

Cross-process WAL contention stays best-effort retry. No cross-instance
coordination, no crash-durability beyond SQLite commit+read-back, no tamper
evidence, no deployment readiness. Lock is per-instance, not per-path.
  No governance additions. Existing audit tests stay green unmodified.
