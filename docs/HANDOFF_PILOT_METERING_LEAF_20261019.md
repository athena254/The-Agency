# Pilot model-leaf propagation handoff — October 19, 2026

## Outcome and scope

Branch `fix/pilot-metering-leaf-20261019`, based on `668d2fc9fa62c5d3216e5f31c4767ba8c5b30453` and the committed `SPEC_LIMITED_AUTONOMY_RELEASE_20261019.md`. This is the authorized M1 maintenance slice, not new admission or beta enablement.

The real executor and both ToolDriver model-call sites now propagate `MeteredDenied` / `MeteredError` instead of retrying, returning an ordinary failed result, or replacing the blocker with `llm_error`. Executor status becomes `FAILED`, its running-task entry is removed, and only the exception class/task ID/attempt number is logged.

The unchanged concrete `MeteredModel` can also emit a raw exception when required post-dispatch audit acknowledgement fails. These consumers conservatively normalize such exceptions or interrupted acknowledgements into the fixed `MeteredError("metered: model unavailable")` before retry/error conversion. Executor normalization recognizes its captured bound callable's concrete `MeteredModel` owner; both object and bound-method construction are tested. ToolDriver normalization recognizes the concrete wrapper itself. This is not a new authorization path and does not accept duck-typed substitutes or subclasses at strict ToolDriver admission.

Executor uses `asyncio.timeout` in the executing task rather than a `wait_for` child. A reproduced Python 3.11 caller-cancellation path discarded the child's typed unknown outcome and left an unretrieved exception. Same-task timeout/cancellation retains the blocker. Legacy ordinary errors, timeout retry behavior, and ordinary cancellation remain covered by existing agent tests.

`ToolLoopResult.llm_calls` now counts generation attempts, including exceptional calls, rather than only completed generations. It remains telemetry, not quota authority or a count of authorized provider effects. Typed blockers escape without a fabricated loop result; tests separately count reservation attempts and actual synthetic provider dispatches.

### Repository files changed

- `src/agency/agents/executor.py`
- `src/agency/tools/driver.py`
- `tests/test_tools_driver.py` (typed-error expectation and exceptional-attempt counts)
- `tests/test_pilot_metering_leaf.py` (new)
- This handoff

`tests/test_executor.py` does not exist at this baseline; existing executor regressions are in `tests/test_agents.py`, which was exercised without modification. No changes to orchestrator, Butler, `llm/metered.py`, beta enablement, providers, resource policy, or other workers' files.

## Test boundary and acceptance evidence

New tests use real initialized per-test quota/audit databases and real request reservation/capability/running state. Provider responses/failures are injected below the unchanged concrete `MeteredModel`, normally at the concrete echo adapter's provider leaf; asynchronous timeout simulation replaces the inner adapter's `generate`, still below the metering boundary. No live provider/network calls occur.

The strict driver tests use actual `ToolRegistry`, `BetaToolPolicy`, `ToolContext`, matching principal and concrete `BetaAuditLog`, with an offline synthetic search tool. They exercise direct final answers and the real forced-final branch, not merely tool-call admission.

The gateway matrix runs real Butler methods through the real orchestrator task/isolated paths and the production executor/driver implementations. Test-only subclasses supply synthetic trusted in-process request context and call `super()`; they do not manufacture blocking exceptions or patch Butler/orchestrator methods. This composition proves propagation to the existing gateway and failed task cleanup, **not** an enabled or authorized Telegram/local beta ingress. Isolated calls still create no task. Failed paths produce no completed gateway turn or successful final artifact.

Assertions cover:

- Quota denial and unavailable budget/audit/intent storage: zero unauthorized provider calls; no retry.
- Provider error, timeout, and successful-provider/failed-outcome-write uncertainty: exact dispatch/reservation counts, no fallback final, pending intent retained.
- Audit-write `OSError`, `ValueError`, and `TimeoutError` after both successful and failing providers, for executor, normal driver and forced final.
- Cancellation and outer executor deadline during outcome acknowledgement: typed unknown outcome, no retry, no orphan executor task.
- Audit close/reopen plus an explicit replay: pending intent remains blocking; no second provider call.
- Authorized direct and forced final answers still complete with exact attempt counts.
- Existing strict non-concrete/subclass admission regressions still pass.

### RED → GREEN records

Raw evidence root: `C:/Users/alphi/agency-evidence/limited-autonomy-20261019/metering/`.

| Slice | Behavioral RED | GREEN |
| --- | --- | --- |
| Executor quota/provider blocker | `red-executor-verified.log`: 2 failed (DID NOT RAISE; four reservation attempts) | `green-executor.log`: 2 passed |
| Driver normal/forced quota/provider blocker | `red-driver.log`: 4 failed (DID NOT RAISE) | `green-driver.log`: 6 passed including executor regressions |
| Post-dispatch audit failure | `red-post-dispatch.log`: 18 failed (lost type and/or retry to later denial) | `green-post-dispatch.log`: 24 passed |
| Exceptional attempt telemetry | `red-attempt-counts.log`: 3 failed (0/1 reported instead of 1/2 attempts) | `green-attempt-counts.log`: 50 passed |
| Interrupted outcome acknowledgement | `red-interruption-confirmed.log`: 4 failed | `green-interruption-confirmed.log`: 4 passed |

Preserved intermediate failures are not hidden or counted as behavioral RED: `red-executor.log` had a fixture assertion on void `mark_running`; `controls.log` had three fixture reopen-path errors; both were corrected before final checks. `green-interruption.log` was an actual incomplete fix (3 passed/1 failed plus unretrieved child error), motivating same-task timeout. `red-interruption.log` preceded the refined timeout probe. Initial lint/style diagnostics and all subsequent logs remain preserved.

## Qualification

Interpreter verified as `C:/Users/alphi/theagency/.venv/Scripts/python.exe`, Python 3.11.16. Absolute worker `PYTHONPATH` is used, never canonical source. CWD/data/temp/basetemp are under `C:/Users/alphi/AppData/Local/hermes/cache/scratch/agency-build-20261019/metering/`; gateway fixtures additionally bind their own temporary Lattice/memory/audit/sandbox paths.

The guarded `run_checks.py` harness explicitly selects `AGENCY_LLM_PROVIDER=echo` / `AGENCY_LLM_MODEL=echo`, sets scratch `LATTICE_SQLITE_PATH`, refuses a worker checkout containing `.env`, disables dotenv loading, and denies external Python socket connect/DNS while permitting asyncio loopback. This is process-level Python containment, not an OS/native subprocess sandbox. No bot/server runtime was launched.

Final focused selection (JUnit XML parsed, no skips/failures/errors):

| File | Passed |
| --- | ---: |
| `test_pilot_metering_leaf.py` | 54 |
| `test_tools_driver.py` | 26 |
| `test_model_metering.py` | 17 |
| `test_metering_uncertainty.py` | 4 |
| `test_beta_metering_entry.py` | 4 |
| `test_agents.py` | 25 |
| `test_llm_wiring.py` | 5 |
| **Total** | **135** |

Final logs: `final-focused.log` / `final-focused.xml`; Ruff lint `ruff-final.log`; Ruff format `format-final.log`; strict affected-module mypy `mypy-final-windows.log` and `mypy-final-linux.log`; `final-cold-imports.log`. Both strict mypy platform targets pass for the two affected source modules. The Linux target is type analysis on Windows, not Linux runtime testing.

Ruff is run against the four changed Python files using the repository `pyproject.toml`; because CWD is scratch, its `src` setting is explicitly bound to the absolute worker source directory to preserve first-party import discovery. No rules or assertions are suppressed to qualify. Five separate fresh interpreters cold-import `agency.tools.driver`, `agency.llm.metered`, `agency.agents.executor`, `agency.telegram.handler`, and `telegram_bot` successfully. `git diff --check` passes.

A preserved harness and post-commit receipt identify the exact scoped revision/source hashes, commands, post-commit rerun and evidence totals outside the idle-pruned scratch tree. Parent must review/cherry-pick the scoped commit and rerun qualification after integration.

## Remaining gates and limitations

- No full suite was run by this worker, no independent review, no CI/remote operations, no live provider tests, no deployment/restart/enablement.
- Direct `MeteredModel.generate` callers remain unchanged: a raw audit-write exception there is not normalized by this consumer repair. Arbitrarily decorated/captured callables hiding the concrete bound owner are not inferred to be metered; callers must preserve typed errors. A shared wrapper-level normalization repair would need separate ownership/approval.
- Gateway-owned outer timeout/cancellation and transport delivery/replay behavior were not changed. The tested gateway cases are downstream typed blockers delivered before gateway timeout, not proof of every gateway interruption schedule.
- The request/audit stores remain independent; pending uncertainty is retained, not reconciled, refunded, cleared, or claimed transactional.
- Restricted-pilot M2/M3 admission/interface work and all release gates remain with the coordinator. This patch is propagation maintenance, not production readiness.
