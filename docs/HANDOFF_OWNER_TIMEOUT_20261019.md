# Owner public timeout ceiling — October 19, 2026 checkpoint

## Change

Branch `feat/owner-metered-20261019`, base `56fa68c053ee4272d66ae462070e1e1cb68d4b36`.

`MeteredModel.__init__` now rejects `timeout_s > 45` with `ValueError` only for an exact admitted owner binding. The existing finite/positive/type validation, default 30 seconds, beta timeout contract, dispatch timer, cancellation handling and all provider/model/task/admission deadlines are unchanged. Production change: two lines inside the existing owner constructor branch.

Owned files only:

- `src/agency/llm/metered.py`
- `tests/test_owner_metering.py`
- `docs/HANDOFF_OWNER_TIMEOUT_20261019.md`

## Strict TDD and concrete positive checks

Before any production edit, ran only the inherited parent-authored node:
`tests/test_owner_metering.py::test_owner_timeout_cannot_exceed_45_seconds`.

- `public-timeout-red-01`: **2 behavioral failures**, both `DID NOT RAISE ValueError` for 45.0001 and 60.0; zero collection errors. Untouched production fingerprint retained.
- Added the owner-only constructor rejection.
- `public-timeout-green-01`: **2 passed**.
- Then added four supplemental existing-contract positive cases: owner HTTP dispatch at 1.0 and exactly 45.0 seconds; legacy beta HTTP dispatch at 45.0001 and 60.0 seconds. These are compatibility checks, not claimed as additional RED slices. The original negative test is preserved unchanged, including zero-HTTP assertions.

Fixtures use concrete admission/quota/audit stores and adapters with `httpx.MockTransport`; no live provider connection.

## Final verification

`public-timeout-focused-final`: **338 passed in 53.06s**, zero failures/errors/skips, including **85 owner-metering cases** and all six new timeout cases. Final source/test SHA-256 fingerprints were captured before this run and checked before commit.

Focused targets:

```text
tests/test_owner_runtime.py tests/test_owner_metering.py tests/test_owner_budget.py
tests/test_owner_provider_binding.py tests/test_model_metering.py
tests/test_metering_uncertainty.py tests/test_pilot_metering_leaf.py
tests/test_beta_metering_entry.py tests/test_tools_driver.py
```

- `public-timeout-ruff-final`: `ruff check --no-cache src tests` passed.
- `public-timeout-format-final`: `ruff format --no-cache --check src tests`; **258 files already formatted**.
- `public-timeout-mypy-win` and `public-timeout-mypy-linux`: explicit `--strict --platform win32/linux` on `src/agency/llm/metered.py src/agency/admission/budget.py`; **2 source files clean per target**. Linux is a mypy target, not an actual Linux runtime test. Project strict configuration retained.
- `git diff --check` passed. Initial Ruff formatting diagnostic retained; corrected only the owned test's wrapping, then reran the focused suite against final formatting.

## Evidence, containment and remaining gates

Logs, JUnit, command/start/end/PID receipts and fingerprints:
`C:/Users/alphi/agency-evidence/limited-autonomy-20261019/owner-metered/public-timeout-*`.
Runner: `public_timeout_checks.py`, safely rebound from existing `run_checks.py`; existing `sitecustomize.py` guard unchanged. Scratch CWD/data/temp remain under the existing isolated owner-metered scratch directory. Canonical project interpreter used read-only; imports/tests explicitly bind this worktree. Credentials stripped, `PYTHON_DOTENV_DISABLED=1`, explicit echo defaults, Python audit denial of private dotenv reads/non-scratch SQLite and network egress preserved. This is Python-process containment, not an OS sandbox.

Terminal frontend failed with `No shell found`; standard-library Python subprocess execution provided the permitted fallback. Each owned qualification child had a real **900-second wall-clock allowance for slow startup**, tracked supervisor/launcher receipts, and bounded tree cleanup on timeout. No attempt used that allowance to change a product deadline; every launched attempt completed and every owned supervisor exited. Parent's earlier infrastructure timeouts remain incomplete, never counted as behavioral RED. Qualification times are October 19 UTC / October 20 local UTC+03; the requested checkpoint filename is retained.

No other source, Hermes/OS configuration, private configuration, canonical runtime data, install, daemon, push, merge or deployment changes. No full suite or live call. Parent integrated-runtime qualification and independent review remain required; this worker's existing runtime tracer is not a substitute for them.
