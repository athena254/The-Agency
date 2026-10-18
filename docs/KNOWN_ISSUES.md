# Known qualification issues

Updated October 18, 2026. These are tracked engineering findings, not waived warnings. GitHub #11 and `STATUS_AND_RESUME_20261018.md` are the active resume checkpoint; fresh evidence is `research-evidence/status-audit-20261018.json`. Earlier qualification files remain historical. Production/daily-driver gates remain open.

## Q-18 — Fresh admission-worker bootstrap failure (release blocker)

At `6b1d4c4`, corrected fail-fast diagnostics collected 1,870 tests and stopped after 14 passed/1 failed. `test_parent_rejects_real_split_claim_two_ran_mutant` expected the two-tools-before-release mutant verdict but got `bootstrap deadline expired; pids=[15472, 3980]`. This prevents a fresh green-suite claim. An earlier full run had an unattributed failure marker and was interrupted by the outer 420-second tool timeout without final JUnit; do not assume it is the same defect. An omitted-target diagnostic collected zero tests and is retained as harness error, not product evidence.

State: OPEN. Preserve child/phase diagnostics and determine whether startup/resource/test harness or runtime behavior caused the deadline. Do not blindly lengthen deadlines, weaken assertions, suppress warnings or retry until green. Initial mypy also timed out at 180 seconds; a separately recorded repeat passed on 151 source files. Lint/format and 16 UI tests passed. Historical 1,870-test qualification is not the October 18 result.

## W-01 — SQLite worker threads after event-loop shutdown

Observed: integrated offline runs emit `PytestUnhandledThreadExceptionWarning`, with aiosqlite completion attempting `call_soon_threadsafe` after its event loop closed. Reports appear around Butler context/thread integration and research/handler fixtures; the point where pytest notices a worker exception is not definitive ownership evidence.

State: PARTIALLY REPAIRED, broad follow-up OPEN. Later service/webhook/Lattice lease and consumer-lifecycle slices repaired defined ownership/startup/cleanup boundaries; historical October 16 qualification passed with resource/thread/unraisable warnings treated as errors. This does not prove every store-internal publication/rollback boundary is covered or historical timeouts causally fixed. Fresh October 18 full qualification is blocked by Q-18. Do not label old warnings benign or suppress them.

Closure: isolate each allocating fixture/service; inject pending DB work and cancellation, verify startup-failure/stop/teardown closes every owned connection and awaits work before the loop exits. Preserve shared-store ownership. Add deterministic focused regressions; run the full suite with thread warnings promoted to errors after baseline diagnosis. No broad global warning filter.

## W-02 — Deprecated FastAPI shutdown registration

Observed: `src/agency/telegram/server.py` uses `on_event("shutdown")`; FastAPI emits a lifespan migration warning.

State: IMPLEMENTED in the accepted repair ancestry (`87fc288`), with lifespan/startup rollback/owned handler cleanup tests. The observation above describes the old baseline, not current source. No beta webhook enablement or fresh release approval follows from this repair.

## W-03 — Test client dependency transition

Observed: installed Starlette warns about httpx-based TestClient and suggests httpx2. This is an environment/dependency compatibility finding, not proof that blindly changing the application HTTP client is safe.

State: OPEN. Pin and reproduce supported dependency matrices from a clean install, review upstream support and transitive requirements, then migrate only the necessary test transport with contract tests. Do not globally mute deprecations or edit lockfiles without verifying both supported Python versions/platforms.

## W-04 — Pytest TestStatus collection warning

Observed: imported `TestStatus` enum in `src/agency/security/red/executor.py` is considered a test class by pytest but has a constructor, producing a collection warning.

State: IMPLEMENTED in accepted repair ancestry (`20603c6`), preserving intended security cases. The observation above describes the old baseline. This closure does not waive Q-18 or current CI/release gates.

## W-05 — Recovery subprocess test stability/diagnostics

Observed: the free worker reported one transient child-spawn-related recovery failure in repeated local runs and subsequent focused passes. The initial worker used discarded child stderr, so it did not retain enough failure diagnostics to establish root cause. Current real-kill/lock/resume tests are meaningful, but a successful run is not proof of flake elimination.

State: OPEN, qualification debt. Preserve child stderr/exit codes in failed test diagnostics, detect premature child exit instead of waiting only on marker timeouts, stress repeated runs under bounded load on Windows/Ubuntu and diagnose the actual failing condition. Do not merely increase deadlines or retry until green.

## Late assessment follow-ups

Read `LATE_REVIEW_DISPOSITION.md` as an assessment of its recorded older source revision. A1 has since repaired routed recall propagation; A2 repaired externally observable execution/check truthfulness, not independent verification; A3.1 repaired required gateway audit acknowledgement, not durable task admission/recovery. Actual specialist/DAG dispatch, independent checker/approval/publication, legacy memory-tool resource isolation/privacy, end-to-end provider budgets/audit, task durability and authenticated capability publication remain unfinished. Use `AUTONOMY_READINESS_20261016.md` and GitHub #11 rather than reclassifying already repaired slices as unimplemented.

## Closed integration regressions in this pass

- Scripted/duck-typed models and MeteredModel subclasses could skip strict-driver metering and return an unaudited final answer. Four regressions reproduced the bypass; `28c9d09` enforces an exact concrete request-bound model. Existing audit/provider/policy failure assertions now use real temporary metering rather than granting test doubles a production exception.
- Completed recoverable runs returned data before checking actor/input binding. Fixed with binding checks before every completed return plus regression coverage.
- Provider adapter drift was checked only before awaits. Fixed by rechecking the bound provider/model immediately before dispatch; mutation-during-reservation regression proved RED then GREEN.
- Unknown cancellation/exception/empty replies wrote terminal audit outcomes, clearing the pending gate across restart. Fixed using nonterminal durable uncertainty evidence; retries remain blocked after audit reopen.
- New eager handler -> Butler import caused fresh ToolDriver/MeteredModel imports and spawned admission-test workers to fail, despite in-process test imports passing. Fixed with deferred command imports; fresh-interpreter tests cover supported entrypoints.

These fixes do not close P1 end-to-end metering/identity/audit or P2 peer application authority in the production plan.
