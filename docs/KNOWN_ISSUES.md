# Known qualification issues

Updated October 15, 2026. These are tracked engineering findings, not waived warnings or GitHub issue closures. Full results/source trees are in `research-evidence/late-review-verification.json`. Production gates remain open.

## W-01 — SQLite worker threads after event-loop shutdown

Observed: integrated offline runs emit `PytestUnhandledThreadExceptionWarning`, with aiosqlite completion attempting `call_soon_threadsafe` after its event loop closed. Reports appear around Butler context/thread integration and research/handler fixtures; the point where pytest notices a worker exception is not definitive ownership evidence.

State: OPEN. Metering worker tests now explicitly close their budget/audit stores, but suite-wide lifecycle cleanup is not established. Do not label these warnings benign or suppress them to pass a release gate.

Closure: isolate each allocating fixture/service; inject pending DB work and cancellation, verify startup-failure/stop/teardown closes every owned connection and awaits work before the loop exits. Preserve shared-store ownership. Add deterministic focused regressions; run the full suite with thread warnings promoted to errors after baseline diagnosis. No broad global warning filter.

## W-02 — Deprecated FastAPI shutdown registration

Observed: `src/agency/telegram/server.py` uses `on_event("shutdown")`; FastAPI emits a lifespan migration warning.

State: OPEN. Move lifecycle handling to a tested lifespan context in a separate slice. Assert admitted/denied webhook behavior, startup failure cleanup and handler/store shutdown; do not enable unsupported beta webhooks as part of migration.

## W-03 — Test client dependency transition

Observed: installed Starlette warns about httpx-based TestClient and suggests httpx2. This is an environment/dependency compatibility finding, not proof that blindly changing the application HTTP client is safe.

State: OPEN. Pin and reproduce supported dependency matrices from a clean install, review upstream support and transitive requirements, then migrate only the necessary test transport with contract tests. Do not globally mute deprecations or edit lockfiles without verifying both supported Python versions/platforms.

## W-04 — Pytest TestStatus collection warning

Observed: imported `TestStatus` enum in `src/agency/security/red/executor.py` is considered a test class by pytest but has a constructor, producing a collection warning.

State: OPEN, small cleanup. Test an explicit non-collection marker or an import alias that does not masquerade as a test class. Preserve the public enum contract and actual security tests; never reduce collected coverage to silence output.

## W-05 — Recovery subprocess test stability/diagnostics

Observed: the free worker reported one transient child-spawn-related recovery failure in repeated local runs and subsequent focused passes. The initial worker used discarded child stderr, so it did not retain enough failure diagnostics to establish root cause. Current real-kill/lock/resume tests are meaningful, but a successful run is not proof of flake elimination.

State: OPEN, qualification debt. Preserve child stderr/exit codes in failed test diagnostics, detect premature child exit instead of waiting only on marker timeouts, stress repeated runs under bounded load on Windows/Ubuntu and diagnose the actual failing condition. Do not merely increase deadlines or retry until green.

## Late assessment follow-ups

Read `LATE_REVIEW_DISPOSITION.md` for the delayed assessments' exact source revision, superseded findings, closed strict-driver bypass and open R-01–R-06 product gaps. In particular, all-domain context continuity, actual specialist/DAG execution, blocking evidence verification, legacy memory-tool tenant isolation, runtime durability and authenticated capability publication remain unfinished. They are mandatory plan items where relevant, not capabilities inferred from passing tests or class names.

## Closed integration regressions in this pass

- Scripted/duck-typed models and MeteredModel subclasses could skip strict-driver metering and return an unaudited final answer. Four regressions reproduced the bypass; `28c9d09` enforces an exact concrete request-bound model. Existing audit/provider/policy failure assertions now use real temporary metering rather than granting test doubles a production exception.
- Completed recoverable runs returned data before checking actor/input binding. Fixed with binding checks before every completed return plus regression coverage.
- Provider adapter drift was checked only before awaits. Fixed by rechecking the bound provider/model immediately before dispatch; mutation-during-reservation regression proved RED then GREEN.
- Unknown cancellation/exception/empty replies wrote terminal audit outcomes, clearing the pending gate across restart. Fixed using nonterminal durable uncertainty evidence; retries remain blocked after audit reopen.
- New eager handler -> Butler import caused fresh ToolDriver/MeteredModel imports and spawned admission-test workers to fail, despite in-process test imports passing. Fixed with deferred command imports; fresh-interpreter tests cover supported entrypoints.

These fixes do not close P1 end-to-end metering/identity/audit or P2 peer application authority in the production plan.
