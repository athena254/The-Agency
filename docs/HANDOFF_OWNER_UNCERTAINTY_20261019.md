# Owner durable uncertainty helper — October 19, 2026

## Scope and behavior

Exclusive worktree `owner-uncertainty-20261019`, branch
`feat/owner-uncertainty-20261019`, base `f605811`.

Added `async OwnerPilotStore.has_uncertainty(principal: OwnerPrincipal) -> bool`
as a 16-line helper. It reuses `_principal`, `_run`, the existing lifecycle gate,
transaction and SQLite-worker drain. A parameterized durable `SELECT EXISTS`
checks every `operation='turn'`, `state='UNCERTAIN'` row, joining request owner,
thread owner and the owner's workspace. It neither enumerates payloads nor
creates subjects, workspaces, requests, events or messages. No cache, schema,
backend, service SQL, clearance, reconciliation or execution-authority changes.

## TDD and qualification

Evidence directory:
`C:/Users/alphi/agency-evidence/limited-autonomy-20261019/owner-uncertainty/`.
The first real RED (`red.txt`) failed with the absent-method AttributeError only
after creating one old uncertain turn and 101 newer failed turns and proving
the newest 100 omit the old uncertainty. Minimal production code then passed
the same test (`green-initial.txt`: 1 passed), including close/reopen durability.
Additional characterization tests exercise empty/foreign owners, every other
state, request/thread/workspace/operation scope, invalid principals, lifecycle
failures, actual SQLite failure, sanitized OSError and connection closure.

Final verification on the unchanged source/tests:

- Required first RED: 1 failed for the missing method after real historical setup.
- Minimal GREEN: 1 passed in 5.94s (including durable close/reopen).
- Final focused run: **35 passed in 718.85s**, covering
  `tests/test_owner_uncertainty.py` and existing `tests/test_owner_store.py`.
  The runner used `--noconftest`; both modules supply their own required fixtures.
- Affected Ruff check: passed; affected format check: 2 files already formatted.
- Strict mypy with explicit project config, `--follow-imports silent`, source
  helper plus new tests: **2 source files clean** for both `--platform win32`
  and `--platform linux`. Linux is a type-check target, not a Linux runtime run.
- Clean cold import: `agency.admission.store` resolved to this exact worktree
  and exited 0 (`cold-final.txt`).
- Working and staged `git diff --check`: passed. Staged inventory is exactly
  the three owned files (`staged-files.txt`).

The final focused output is `green-final.txt`; final static outputs are
`ruff-check-final.txt`, `ruff-format-final.txt`, `mypy-strict-win32.txt` and
`mypy-strict-linux.txt`. Earlier non-strict mypy attempts are not the claimed
strict gates. Diagnostics with native `0xC0000005` exits and startup timeouts
remain historical failed attempts, superseded by the completed clean runs.

## Containment and limitations

Project Python 3.11 venv; exact worktree source; scratch CWD/data/temp. The
external scratch runner disables dotenv before imports, selects echo, removes
credential variables, rejects environment-file reads/non-scratch SQLite and
external socket connections, while allowing asyncio's local self-pipe.
No private configuration, runtime databases, provider calls, installation,
daemon, push, merge or full-suite run. Network containment covers the Python
process, not arbitrary native children. Cold store imports inherit the existing
Butler package dependency; this helper does not change that initializer.

The shell/file tools became intermittently unavailable during qualification.
Direct native subprocesses from the Python tool provided a fallback. An early
fallback inherited Hermes' Python 3.14 site-packages through PYTHONPATH and failed
with a pydantic_core import error; that invalid run is retained separately and
is not code evidence. Clean reruns use `-I`, clear inherited Python environment
and explicitly bind project source. Several startup timeouts/diagnostic dumps
are retained rather than reported as test failures or passing gates.

Parent integration and independent review remain required; this is not service,
API, live-provider, rollout, readiness clearance or Linux-runtime qualification.
