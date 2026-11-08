# Known qualification issues

Updated November 9, 2026. These are tracked engineering findings, not waived warnings. The active status reference is `CURRENT_STATE.md`; the approved policy inputs are `OWNER_DECISIONS_D01_D05.md`. Older qualification files remain historical. Production/daily-driver gates remain open.

## KI-01 — Windows-only harness errors from oversized test IDs (harness, not product)

At `cafd683`, the hermetic suite records 3453 passed, 3 skipped, 4 errors. All four errors are setup/teardown failures on two parameterized tests whose IDs are tens of thousands of characters long:

- `tests/test_factory_coding_models.py::test_canonical_rejects_non_json_or_unbounded_values` with the `"x" * 65537` parameter.
- `tests/test_factory_coding_publication.py::test_original_long_or_calculator_looking_task_never_selects_calculator` with the `"雪" * 8000` parameter.

On Windows the pytest harness cannot set its per-test environment variable once the ID exceeds the OS environment-block limit; the same tests pass on Linux. State: OPEN (harness/test-ID limit). Do not shorten product strings to fix it; add explicit short parameter IDs or a harness-level workaround, then rerun on Linux and Windows. This is not a product failure and does not block the Python gate, but it must not be relabeled as a product defect.

## KI-02 — SQLite worker threads after event-loop shutdown (carried from W-01)

Observed: integrated offline runs emit `PytestUnhandledThreadExceptionWarning`, with aiosqlite completion attempting `call_soon_threadsafe` after its event loop closed. Reports appear around Butler context/thread integration and research/handler fixtures; the point where pytest notices a worker exception is not definitive ownership evidence.

State: PARTIALLY REPAIRED, broad follow-up OPEN. Later service/webhook/Lattice lease and consumer-lifecycle slices repaired defined ownership/startup/cleanup boundaries. This does not prove every store-internal publication/rollback boundary is covered. Do not label old warnings benign or suppress them.

Closure: isolate each allocating fixture/service; inject pending DB work and cancellation; verify startup-failure/stop/teardown closes every owned connection and awaits work before the loop exits. Preserve shared-store ownership. Add deterministic focused regressions; run the full suite with thread warnings promoted to errors after baseline diagnosis. No broad global warning filter.

## KI-03 — Deprecated Starlette TestClient transport (carried from W-03)

Observed: installed Starlette warns that the `httpx`-based `TestClient` is deprecated and suggests `httpx2`; the collection run emits `StarletteDeprecationWarning` and an anyio `BlockingPortal` alias deprecation.

State: OPEN. Pin and reproduce supported dependency matrices from a clean install, review upstream support and transitive requirements, then migrate only the necessary test transport with contract tests. Do not globally mute deprecations or edit lockfiles without verifying both supported Python versions/platforms.

## KI-04 — Recovery subprocess stability/diagnostics (carried from W-05)

Observed: the free worker reported one transient child-spawn-related recovery failure in repeated local runs and subsequent focused passes. The initial worker used discarded child stderr, so it did not retain enough failure diagnostics to establish root cause. Current real-kill/lock/resume tests are meaningful, but a successful run is not proof of flake elimination.

State: OPEN, qualification debt. Preserve child stderr/exit codes in failed test diagnostics, detect premature child exit instead of waiting only on marker timeouts, stress repeated runs under bounded load on Windows/Ubuntu and diagnose the actual failing condition. Do not merely increase deadlines or retry until green.

## KI-05 — Mission Control wiring and telemetry gaps

Observed: `gui/` is a React 18 + TypeScript + Vite + Tailwind console served at `/mission-control`. Its API client calls `/api/health`, `/api/agents`, `/api/session` and `/api/chat` without the owner bearer; only the `pilotRequest` helper attaches the in-memory bearer (for `/api/pilot/*`). In pilot-configured mode the prototype app requires owner authentication for `/api/health` and `/api/agents`, so the Dashboard/Agents/Settings health panels fail there; the console is fully usable only against the non-pilot prototype surface, or after its bearer wiring is extended. The `gui/` tree has no test suite. Dashboard task/memory/evidence counts are placeholder zeros and the Activity feed contains static entries (including a model-path claim), so they are not live telemetry.

State: PARTIAL. The console is a real built UI with honest locked states for P2 surfaces, but it is not a tested, fully wired operations console. Add GUI tests, attach the bearer to the prototype-surface reads or document the non-pilot requirement, and replace placeholder/static telemetry with live data or honest empty states.

## KI-06 — Owner pilot qualification boundaries

Observed: the owner pilot service/API/UI and persistent workspace are implemented and unit/integration tested offline, but no live admitted provider conversation, real-browser session or deployment qualification is recorded in this tree. The coding factory core (`OwnerPilotService.submit_coding`/`get_coding_receipt`) is not exposed over `/api/pilot/*`; Telegram owner authentication is not wired; the spending ledger is disabled unless explicitly configured; calculator verification covers only the exact marker-message fixture.

State: PARTIAL/OPEN. These are scope boundaries, not defects, but they must not be described as completed daily-driver qualification. Live provider, real-browser, restart/crash and coding-surface qualification remain separate gates.

## KI-07 — Peer authority absent (core blocker)

Observed: the Lattice is a single-process SQLite/in-memory prototype with process-local proposal state, best-effort mirroring, a `user` override and no membership epoch. `/v1/*` business routes are closed with HTTP 503 except `GET /v1/health`. Signed peer-envelope primitives and a historical four-process CometBFT loopback lab exist, but no Agency task grants, affected-consumer input, human attestation, membership changes or actuator enforcement are implemented.

State: OPEN (core production blocker). The owner decisions D-01–D-05 are approved and the migration path is specified in `SPEC_KNOWN_PEER_PROTOCOL.md` §18 and `SPEC_PEER_LATTICE_GOVERNANCE.md`, but no peer application implementation exists. Do not describe the Lattice, the pilot or the CometBFT lab as decentralized authority.

## Closed repairs in this pass / carried from earlier

- Q-18 admission-worker bootstrap deadline: resolved by the hermetic suite; `test_parent_rejects_real_split_claim_two_ran_mutant` now passes in the full run. The October 18 failure evidence remains historical.
- W-02 deprecated FastAPI `on_event("shutdown")`: fixed (`87fc288`); no `on_event` remains in `src/`.
- W-04 pytest `TestStatus` collection warning: fixed (`20603c6`); collection no longer emits it.
- Scripted/duck-typed models and `MeteredModel` subclasses could skip strict-driver metering; `28c9d09` enforces an exact concrete request-bound model.
- Completed recoverable runs returned data before checking actor/input binding; fixed with binding checks before every completed return plus regression coverage.
- Provider adapter drift was checked only before awaits; fixed by rechecking the bound provider/model immediately before dispatch.
- Unknown cancellation/exception/empty replies wrote terminal audit outcomes; fixed using nonterminal durable uncertainty evidence.
- Eager handler -> Butler import caused fresh ToolDriver/MeteredModel imports and spawned admission-test workers to fail; fixed with deferred command imports.

These fixes do not close P1 end-to-end metering/identity/audit or P2 peer application authority in the production plan.
