# SPEC_LOCAL_RECOVERY_V1 — minimal durable local recovery (CORRECTED)

Status: CORRECTED at `18b9717`, branch `feat/free-recovery`. Supersedes draft
with clock leases, omitted-input resume, magic reader compat. No peer
authority, no deployment. License MIT per `pyproject.toml`.

## 1. Goal / non-goals
Goal: crash-safe local bookkeeping for `agency.workflows`: explicit restart
reconciliation plus safe resumption of trusted replay-safe operations.
Non-goals: no new framework, no peer authority, no exactly-once effects,
no auto-retry of unknown effects, no model-text safety, no rollout.

## 2. Current state (base 18b9717)
Registry: SQLite `workflow_definitions(pk workflow_id,version)`,
`workflow_runs(pk run_id)`; `register/get/list_versions/record_run/
save_run/get_run/close`. Run status in `{PENDING,RUNNING,COMPLETED,FAILED}`;
old readers raise on other statuses (fail-closed, never magic compat).
Executor `run()` pins version, PENDING to COMPLETED/FAILED, fail-closed on
unknown op, denied, non-JSON, oversize; sanitized errors; no resume.
CLI entry is `agency.cli:main` (`src/agency/cli/main.py`, Typer over HTTP).
`src/agency/__main__.py` absent. No lock dep in `pyproject.toml`.


## 3. Design: extend, do not duplicate
Extend `WorkflowRegistry` plus `WorkflowExecutor`; new `workflows/recovery.py`
(lock plus digests plus `RecoverableRun`). Legacy `run()` unchanged. New
explicit `run_recoverable/resume/reconcile` only. Library is the entry; CLI
out of slice (if ever added: redacted read-only status/reconcile, never
dynamic operation import, never fake demo op as production feature).

## 4. Types / signatures (exact)
```python
class WorkflowExecutor:
  def __init__(self, registry, operations, authorizer=None,
               replay_safe=frozenset(), operation_versions=None) -> None: ...
  def run(self, workflow_id, version, inputs, actor_id) -> WorkflowRun: ...  # unchanged
  def run_recoverable(self, workflow_id, version, inputs, actor_id) -> RecoverableRun: ...
  def resume(self, run_id: str, inputs: dict, actor_id: str) -> RecoverableRun: ...
  def reconcile(self) -> list[RecoverableRun]: ...  # lock-gated, no clock arg
  def inspect_recoverable(self, run_id: str) -> RecoverableRun: ...
```
`replay_safe` (subset of `operations`) and `operation_versions` are
construction-time trusted config (reviewed code), never model/caller flags.
`resume()` takes no safety parameter. `RecoverableRun` status in
`{RUNNING,INDETERMINATE,COMPLETED,FAILED}`.

## 5. Ownership: OS lock, never clock stealing
Recoverable writers plus `reconcile/resume` serialize on a nonblocking OS
file lock adjacent to the DB (`<db>.recover.lock`; Windows `msvcrt`, POSIX
`flock LOCK_EX|LOCK_NB`, held open across execution). A live hung worker
retains the lock: NEVER steal on timeout. Kernel releases on process kill.
Lock held means any other recoverable entry raises `RuntimeError` (denied).
`reconcile()` runs only while the lock proves no concurrent owner. Reject
`:memory:` for all recoverable paths. SQLite `synchronous=FULL` plus commit
per intent/outcome write; final persistence error propagates, never success.

## 6. Schema: versioned, no silent adoption
New `recoverable_runs/recoverable_steps` tables (or one row plus per-step
commits) with `schema_version=1` and run binding. Idempotent
`CREATE TABLE IF NOT EXISTS`; never migrate or adopt legacy `workflow_runs`
rows. Legacy runs WITHOUT recovery metadata are never reconciled or resumed.
Legacy readers fail closed on `INDETERMINATE` (raise).

## 7. Inputs and binding: caller re-supplies
Persist input digest (`sha256` over canonical bounded JSON) only, never raw
private inputs. `resume(run_id,inputs,actor_id)` requires exact canonical
bounded inputs with matching digest, same `actor_id` (no resume-foreign),
pinned definition digest match, and equal `operation_versions` binding per
step op (rejects changed semantics under same op name). Persist completed
per-step JSON outputs (same privacy as registry); never re-run completed
steps. Reuse `MAX_JSON_BYTES=64KiB`, `MAX_STEPS=64`, canonical snapshots.

## 8. Replay safety and durability
Durable step intent BEFORE callback; outcome AFTER callback (each committed).
Before replaying any uncertain step require allowlist membership plus strict
`True` authorization (`authorizer(...) is True`; missing, exception, or
non-boolean denies) for every remaining permission. Unknown or unsafe op
stays `INDETERMINATE` (`error="replay refused: not replay-safe"`) with NO
call. Unknown outcomes visible, never `COMPLETED`. Sanitized type-name
errors; refuse NaN/Infinity, non-JSON, oversize.

## 9. Tests (RED to GREEN one at a time, real tempfile SQLite)
Durable `run_recoverable` success; `Popen.kill` child mid-replay-safe
callback then `reconcile` plus `resume` keeps acknowledged steps; wrong
inputs, actor, definition, operation-version denied; unsafe tail never
called; lock-held resume/worker denied; persistence failure propagates;
legacy rows refused; close/reopen persists. Keep `test_workflow_registry.py`
green. No stubs.

## 10. Budget and next ownership
Impl about 400-600 lines: registry extension plus `recovery.py` plus
executor methods. Next owner implements `src/agency/workflows/registry.py`,
`executor.py`, `recovery.py`, `tests/test_local_recovery_v1.py`.
PowerShell: `$env:AGENCY_LLM_PROVIDER='echo';
$env:AGENCY_LLM_MODEL='echo'; $env:PYTHONPATH='src'; &
C:/Users/alphi/agency-mvp/.venv/Scripts/python.exe -m pytest
tests/test_workflow_registry.py tests/test_local_recovery_v1.py -q`;
`ruff check/format`, `mypy src/agency/workflows`. Single-host only.
