# Owner metering / budget checkpoint — October 19, 2026

## Delivered

Exclusive branch `feat/owner-metered-20261019`, base `8d0257c26a1b7c1cdea14036e78eb6878e2628de`. No other worker changes integrated.

- `MeteredModel(inner, binding: MeteredBinding | OwnerMeteredBinding, store: BetaBudgetStore | OwnerBudgetAdapter, audit: BetaAuditLog, timeout_s: float = 30.0)` requires exact matching concrete pairs. Owner grant must reference the exact supplied budget. `binding` property now exposes the union; downstream strict Driver integration must narrow it explicitly.
- Owner generation uses the existing actual `LLMAdapter.freeze_dispatch()` snapshot captured at construction and `generate_bound()` HTTP leaf. Same common quota → acknowledged intent → bounded dispatch → acknowledged outcome pipeline; no parallel assistant, retry, fallback, fabricated Telegram principal, adapter edit, or new ingress.
- Owner context permits only `owner_grant`, optional exact matching `task_id`, and `strict`. The grant must be the exact bound object. Original binding/adapter/budget/audit references, grant fields, issued lease, durable running state, cancellation, revocation and deadline are checked around awaited persistence and immediately inside dispatch. Binding liveness is also checked before outcome and before returning a response. Frozen effective HTTP configuration cannot redirect when mutable adapter/environment fields drift.
- Owner audit identity/task are neutral `owner-pilot`; no owner/subject/task identifiers, prompt, endpoint, API key or capability in its entries or model repr. Owner audit provider/model labels come from the frozen snapshot, including uncertainty after a replaced binding.
- Owner quota/intent acknowledgements require exact `True`; outcome acknowledgement must be the exact entry ID. Direct owner callers receive `MeteredDenied` before dispatch or `MeteredError` after uncertain dispatch/outcome failure, not raw storage success. Timeout/exception/cancellation/empty/malformed HTTP responses leave the durable pending gate blocking, including after audit reopen. Original beta semantics, including its existing outcome-storage exception contract, are deliberately retained.
- `OwnerBudgetAdapter.initialize()` initializes only its concrete quota backend, not the owner store. Read-only `limits -> BudgetLimits` returns a validated immutable copy of actual backend limits. `reserve_model_call(lease, *, limit: int = 8)` and `reserve_tool_call(lease, tool, *, limit: int = 4)` feed lower caps directly to the existing atomic durable `reserve_attempt`; invalid/bool/over-ceiling values cannot authorize effects. Nonboolean durable attempt acknowledgements fail closed. No refunds or cross-store transaction claim.

## Actual TDD evidence

Raw logs/JUnit and isolated runner:
`C:/Users/alphi/agency-evidence/limited-autonomy-20261019/owner-metered/`.
Scratch/data/temp:
`C:/Users/alphi/AppData/Local/hermes/cache/scratch/agency-build-20261019/owner-metered/`.

Strict vertical behavior slices were run RED then GREEN before the next implementation behavior. Parameterized cases exercise the same behavior at concrete boundaries. Parsed RED receipts: **13 slices, 39 executed cases, 38 expected failures, 1 already-passing audit/reservation-denial case, 0 collection errors**. Matching GREEN receipts pass all 39 cases. Slice prefixes: `tracer`, `limits`, `lower`, `grant-cap`, `liveness`, `outcome`, `acknowledgments`, `contract`, `completion`, `attempt-ack`, `prompt`, `replaced-binding`, `beta-contract`.

The tracer was rerun on untouched production first: exactly **1 failure**, concrete beta-store rejection at model construction, **0 collection errors** (`tracer-red`). First GREEN attempt exposed an eager-import cycle; retained as `tracer-green.log`, then fixed with TYPE_CHECKING/function-local budget imports (`tracer-green2`: 1 passed). Supplemental pre-existing-contract regressions are not misrepresented as new RED receipts.

## Final qualification

`release-focused.log/.xml`: **332 passed in 60.67s**, no full suite. Includes **79 tests in new `test_owner_metering.py`**, the unchanged admitted owner HTTP tracer, concrete owner budget/provider binding tests, original model metering/uncertainty, beta ingress denial, actual leaf typed blockers, and ToolDriver regressions. HTTP leaves use `httpx.MockTransport`; stores, adapter, wrapper and audit remain concrete.

Exact command targets:

```text
tests/test_owner_runtime.py tests/test_owner_metering.py tests/test_owner_budget.py
tests/test_owner_provider_binding.py tests/test_model_metering.py
tests/test_metering_uncertainty.py tests/test_pilot_metering_leaf.py
tests/test_beta_metering_entry.py tests/test_tools_driver.py
```

- Worktree-root Ruff `check --no-cache src tests`: passed (`release-ruff`).
- Worktree-root `ruff format --no-cache --check src tests`: **258 files already formatted** (`release-format`). Formatting/fix writes were restricted to owned source/test files.
- Strict project-config mypy for affected source `src/agency/llm/metered.py src/agency/admission/budget.py`: **2 source files clean for win32 and Linux targets** (`release-win`, `release-linux`). This is not an actual Linux runtime qualification or whole-source typecheck claim.
- Three fresh cold-import orders (metered-first, budget-first, models-first): passed, exact worker source paths verified (`final-cold1/2/3`).
- `PYTHON_DOTENV_DISABLED=1` set before Agency imports; credentials stripped, explicit echo defaults, absolute worker source/tests, scratch CWD/data/temp. Python audit hook denies non-scratch SQLite, private dotenv reads, writes outside scratch/evidence, external socket/DNS/bind (only asyncio internal socketpair exemption). This is process-level Python containment, not an OS sandbox.
- Initial mypy/style failures, the eager-import error, and an invalid optional test filename invocation (`focused-final`: no tests ran) are preserved, not counted green. Corrected final targets above all pass.

## Scope and open gates

Modified only `src/agency/llm/metered.py`, `src/agency/admission/budget.py`; added `tests/test_owner_metering.py` and this handoff. `tests/test_owner_runtime.py` remains unchanged and passes. No quarantined source or old CLI implementation read; no private config/runtime databases, provider call, install, daemon, push, merge or deployment.

No remaining concrete metering/budget blocker found in these focused checks. Registry/Driver owner wiring, policy, context/service/HTTP ingress, full-suite/integration qualification, independent review and approved live provider/owner trial remain parent-owned separate gates. Owner pending/uncertain execution remains deliberately blocking; no automatic clearing/retry recovery was added. The canonical interpreter was used read-only as toolchain, never as source or runtime-data location.
