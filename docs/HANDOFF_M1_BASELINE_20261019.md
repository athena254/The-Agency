# W1 baseline handoff — October 19, 2026

## Outcome and boundaries

- Worktree: `C:/Users/alphi/agency-worktrees/pilot-baseline-20261019`; branch `fix/pilot-baseline-20261019`; base `668d2fc9fa62c5d3216e5f31c4767ba8c5b30453`.
- Linux-target strict Mypy failure reproduced and repaired. Both Linux and Windows targets check all 151 source files successfully. Windows recovery/locking behavior remains green.
- Historical admission bootstrap timeout is **not attributed or declared repaired**. The untouched real mutant test passed on the first focused reproduction, and its preceding module tests also passed. No deadline, gate, retry policy, dispatch behavior or production audit implementation was changed.
- A separate, demonstrated diagnostic gap was repaired using RED/GREEN: protocol observations were an exception attribute absent from the retained mismatch/JUnit trace. Failures now attach a bounded, redacted event/PID/phase tail as an exception note; raw observations remain available programmatically. Note failures cannot mask the primary error.
- Focused qualification covers both entire owned test modules plus the existing positive real-process admission race: **67 passed**, including real process cleanup and fault-injected deadline/cleanup tests. Parent integration/full-suite qualification and independent review remain pending.

## Changes

1. `src/agency/workflows/recovery.py`: use `sys.platform == "win32"` for the real Windows branch in acquire/release; use typed `fcntl` APIs directly in the POSIX branches. Remove the existing `cast(Any, fcntl)` escape hatches, add no ignores and weaken no checks. Committed separately as `3dd03132e2e9a448bd402256fddbb3d96cedecdb`.
2. `tests/worker_support/admission.py`: attach only the last 16 event/PID/phase observations to nonempty protocol failures; allowlist event/phase values; omit output, exception text and provenance paths. Preserve original exception message, raw observations and cleanup behavior.
3. `tests/test_admission_worker_protocol.py`: focused bounded/redacted diagnostic regression and a deterministic clock-driven stalled-bootstrap case. The latter proves deadline failure preserves bootstrap-start observations, leaves gates closed, closes both owned workers and creates no audit DB.
4. This handoff. `tests/test_local_recovery_v1.py` was exercised but did not need modification.

## Root-cause evidence

### Strict Mypy portability

At base, the Linux-target gate produced exactly four `attr-defined` errors at recovery.py lines 124/156 for `msvcrt.locking`, `LK_NBLCK` and `LK_UNLCK`. The installed Mypy reachability analyzer recognizes literal `sys.platform` comparisons, while this code used `os.name`. Installed typeshed exposes the msvcrt APIs only under `sys.platform == "win32"`. Matching the genuine platform branch lets Mypy check each valid API rather than checking Windows attributes against the Linux stub. The POSIX casts were no longer necessary.

### Retained admission timeout

Original evidence is preserved without alteration at `C:/Users/alphi/agency-evidence/status-audit-20261018/full-diagnostic-corrected/` and copied into this worker's `retained-admission-failure/` evidence directory. The retained result records Python 3.11.16, source revision `6b1d4c4`, a real `bootstrap deadline expired; pids=[15472, 3980]` mismatch, and an incomplete full-suite diagnostic run stopped after that failure. This is a real failure, not an expected mutant rejection.

Ranked hypotheses and observations:

1. Delayed spawn/cold dependency bootstrap under the original machine/run conditions: possible, but the old trace does not retain event observations or timing inside bootstrap. No retrospective root cause is established.
2. Main-script reentry on Windows spawn: the fresh launcher has an explicit `__main__` guard. The saved historical command says `python -m pytest`, but does not establish every wrapper's behavior. Do not assert recursion without the original launcher evidence.
3. SQLite initialization/import failure: fresh real audit initialization succeeds; the protocol fails fast on an emitted worker error/nonzero exit. The old trace does not identify a child error. Do not patch SQLite or imports based on this timeout alone.
4. Preceding test patch leakage: all original 35 admission-module cases passed together, including the exact preceding tests and real mutant. The worker already restores class patches in `finally`.

The first unmodified mutant reproduction passed (two distinct PIDs reached held calls, two pending audit rows, no result before release). This is counterevidence to a deterministic baseline defect, not proof that the historical failure is resolved. Subsequent runs were regression checks after explicit diagnostic/type edits, not retry-until-green. Existing limits remain bootstrap=60s aggregate, observations=15s, start/release=10s, old-gap barrier=5s.

The actual diagnostic regression failed before the change because `AdmissionProtocolError.__notes__` contained no observation metadata. Its GREEN verifies bounded output and exclusion of synthetic private output/error/path fields. The injected stalled-bootstrap case is a deterministic reproduction of the deadline classification and cleanup, **not** a claim to reproduce the original OS/import stall.

## Commands and evidence

Interpreter: `C:/Users/alphi/theagency/.venv/Scripts/python.exe` (verified Python 3.11.16). Guarded runner:

`C:/Users/alphi/AppData/Local/hermes/cache/scratch/agency-build-20261019/baseline/qualify.py`

The runner creates per-label scratch CWD, `data/`, temp, caches and logs; sets `PYTHONPATH` to this worktree's absolute `src`; forces echo provider/model and scratch `LATTICE_SQLITE_PATH`; strips inherited key/token/secret/credential/password and live-provider configuration variables before launching test children. The worktree has no `.env`.

With that interpreter and runner as the first two command tokens:

| Remaining arguments | Observed result |
| --- | --- |
| `mypy-linux-red mypy linux` | RED: 4 expected attr-defined errors, 151 source files |
| `mypy-linux-green mypy linux` | GREEN: no issues in 151 source files |
| `mypy-windows-green mypy win32` | GREEN: no issues in 151 source files |
| `admission-retained-repro pytest <worktree>/tests/test_admission_worker_protocol.py::test_parent_rejects_real_split_claim_two_ran_mutant -s -vv` | First, untouched focused reproduction: 1 passed |
| `admission-module-baseline pytest <worktree>/tests/test_admission_worker_protocol.py -vv` | Untouched admission module: 35 passed |
| `admission-diagnostic-red pytest <worktree>/tests/test_admission_worker_protocol.py::test_protocol_error_retains_bounded_redacted_observations` | RED: missing observation note |
| `admission-diagnostic-green pytest <worktree>/tests/test_admission_worker_protocol.py::test_protocol_error_retains_bounded_redacted_observations` | GREEN: 1 passed |
| `focused-final-precommit pytest <worktree>/tests/test_local_recovery_v1.py <worktree>/tests/test_admission_worker_protocol.py <worktree>/tests/test_beta_tool_boundary.py::test_beta_cross_process_admission_allows_only_one_pending_call -vv` | 67 passed; zero owned active multiprocessing children afterward |
| `ruff-lint-final-precommit ruff check` | All checks passed, full src/tests |
| `ruff-format-green ruff format --check` | All 246 files already formatted |

`<worktree>` means the absolute worktree path above. Mypy uses the unchanged strict `pyproject.toml`, an explicit platform and scratch cache. Pytest promotes ResourceWarning, PytestUnhandledThreadExceptionWarning and PytestUnraisableExceptionWarning to errors. Final runs record the revision and before/after Python-source-tree fingerprint in `result.json`; precommit Python source fingerprint is `4867e298242481b323a1585a5e504447ca3b0124c505cc8a5945c27be9d19009`.

Durable evidence root: `C:/Users/alphi/agency-evidence/limited-autonomy-20261019/baseline/`. Each label retains raw output, result metadata and (pytest) JUnit XML; the final guarded runner is copied there. Postcommit verification uses additional `*-committed` labels; inspect their receipts rather than interpreting a precommit HEAD as a committed test tree.

Harness issues were retained, not relabeled product failures: the initial scratch-CWD Ruff run using an absolute `--config` resolved default source roots relative to scratch, misclassifying Agency imports (94 import-order findings). `--show-settings` established the mismatch. Supplying the same absolute two source roots as the normal worktree invocation corrected it without changing rule selection or source. A real formatting finding in the newly added note call was corrected only in the owned file; the original failed formatter log remains under `ruff-format/`.

## Cleanup, isolation and remaining gates

- Real recovery tests wait/reap the direct interpreter PID, test lock denial while held, reacquisition after kill, early exits and cleanup failure. Real admission partial-start tests assert both owned Process handles close only after exit; the successful race requires exitcodes `[0, 0]`. Every qualification pytest run reports `owned multiprocessing children after pytest: []`.
- Owned recovery/admission stores use explicit `tmp_path` paths (plus intentional in-memory negative cases); all qualification defaults/temporary files are scratch-only. No runtime DB, canonical source, credentials or dotenv file was read or modified.
- The Python audit-hook guard allows only loopback sockets and covers the pytest runner and spawned admission workers. It is **not an OS sandbox** and does not automatically cover raw recovery-script/native subprocesses. Those focused scripts perform synthetic local file/lock work; no live model, bot, external network or deployment was exercised.
- Full-suite behavior, original admission timeout attribution, Linux runtime execution and independent review are still open. The Linux Mypy target is static portability evidence, not a Linux process execution test. Parent should integrate the exact reviewed commits and run the single coordinated full suite. Do not claim M1's historical-timeout attribution gate closed from these focused passes.
