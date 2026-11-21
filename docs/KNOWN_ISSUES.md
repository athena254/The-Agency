# Known qualification issues

Updated November 19, 2026. These are tracked engineering findings, not waived warnings. The active status reference is `CURRENT_STATE.md`; the approved policy inputs are `OWNER_DECISIONS_D01_D05.md`. Older qualification files remain historical. Production/daily-driver gates remain open.

## KI-01 — Windows-only harness errors from oversized test IDs (harness, not product)

At `c0a1de9`, the hermetic suite recorded 3513 passed, 3 skipped, 4 errors. All four errors were setup/teardown failures on two parameterized tests whose IDs were tens of thousands of characters long:

- `tests/test_factory_coding_models.py::test_canonical_rejects_non_json_or_unbounded_values` with the `"x" * 65537` parameter.
- `tests/test_factory_coding_publication.py::test_original_long_or_calculator_looking_task_never_selects_calculator` with the `"雪" * 8000` parameter.

State: **FIXED** in commit `4406f59`. Both cases now carry explicit short test IDs (`oversized-ascii-65537`, `oversized-snowman-8000`) while the long product payloads are unchanged. On Nov 19 the two parameterized tests re-ran clean on Windows (**11 passed**). A full-suite re-run on this branch has not completed (timed out at 81%), so no new total is claimed. This was a harness/test-ID limit, not a product failure.

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

State: **LARGELY RESOLVED** by the mission-control-upgrade pass (Nov 21, `feat/mission-control-upgrade`). The API client now attaches the in-memory bearer to `/api` reads (pilot-mode health/agents work); Dashboard Memories/Evidence counts come from the observe overview, the fabricated Activity feed was replaced by a real "Recent audit events" panel from the immutable audit log, agent trust/runtime status comes from the observe surface, the sidebar reports real health/version, Skills/Evidence/Memories are honest read-only views, and `/api/health` reports the real version. The React app now has a committed Playwright + axe suite (`cd gui && npm run test:e2e`, 26 tests across desktop/mobile viewports; echo-provider backend). Remaining: pilot task counts on the Dashboard still come from the pilot surface only; Memories/Evidence/Skills remain read-only until the /v1 gates open; Lattice/Forge stay locked pending peer authority. Doc drift note: the console is React 19.3, not React 18.

## KI-06 — Owner pilot qualification boundaries

Observed: the owner pilot service/API/UI and persistent workspace are implemented and unit/integration tested offline, but no live admitted provider conversation, real-browser session or deployment qualification is recorded in this tree. The coding factory core (`OwnerPilotService.submit_coding`/`get_coding_receipt`) is exposed over `/api/pilot/*` but gated on an opt-in fixture repository; Telegram owner authentication is not wired; the spending ledger is disabled unless explicitly configured; calculator verification covers only the exact marker-message fixture.

State: PARTIAL/OPEN. These are scope boundaries, not defects, but they must not be described as completed daily-driver qualification. Live provider, real-browser, restart/crash and coding-surface qualification remain separate gates.

## KI-07 — Peer authority absent (core blocker)

Observed: the Lattice is a single-process SQLite/in-memory prototype with process-local proposal state, best-effort mirroring, a `user` override and no membership epoch. `/v1/*` business routes are closed with HTTP 503 except `GET /v1/health`. Signed peer-envelope primitives and a historical four-process CometBFT loopback lab exist, but no Agency task grants, affected-consumer input, human attestation, membership changes or actuator enforcement are implemented.

State: OPEN (core production blocker). The owner decisions D-01–D-05 are approved and the migration path is specified in `SPEC_KNOWN_PEER_PROTOCOL.md` §18 and `SPEC_PEER_LATTICE_GOVERNANCE.md`, but no peer application implementation exists. The remaining A4-A7 gates are tracked in `CURRENT_STATE.md`. Do not describe the Lattice, the pilot or the CometBFT lab as decentralized authority.

## KI-08 — POSIX private-mode check failed owner-recovery tests (test harness, not product)

Observed: on POSIX CI, `tests/test_owner_recovery_cli.py` created `pilot.db`, `quota.db` and `audit.db` under the default umask, so the launcher's private-mode check (`stat.S_IMODE(...) & 0o077`) rejected them even though the fixture directory itself was `0o700`. Windows uses ACLs and was unaffected.

State: FIXED on this branch (`ddb4eed`, refined in the working tree). The test harness now mirrors the launcher's `umask 0o077` by chmod-ing every file it creates in the synthetic private directory to `0o600` on POSIX. No product code changed. Re-run the owner-recovery tests on Ubuntu and Windows to confirm; do not weaken the launcher's private-mode check.

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

## KI-09 — Engine tool-gap self-publish was simulated (FIXED on feat/blacksmith-orchestrator)

Before this branch, `run_autonomous` hashed the tool NAME, passed literal `"PASS"` to `can_self_publish`, compared the digest to itself, and recorded `published=true` in the proof chain with no build, no gates, and no artifact (observed live for `weather_lookup`). Replaced in commit `f45bdf7`: real builds via `BlacksmithOps.build_tool_job` with real verdicts, report ids, and artifact digests — or an honest `build_blocked` when no ops/spec is supplied. Regression coverage: `tests/test_autonomy_engine_honesty.py` (4 tests, including one asserting the old name-hash can never appear). The e2e assertion that encoded the simulation was updated to assert the honest behavior.

## KI-10 — Striker worker provider availability (RESOLVED via Hermes striker)

Live acceptance (Nov 20, `D:/blacksmith-acceptance`): the opencode free tier returned "Free usage limit reached" (beam-free, hy4-flash-free) after concurrent sessions consumed the quota. The hermes proxy path (`127.0.0.1:8645`, Nous Portal) returned "Unexpected server error" for opencode's agentic requests across three models (qwen4-max, longcat-2.5, hy4-flash), while plain, tools-bearing, and 100KB curl requests through the same proxy succeeded — an upstream request-shape incompatibility, not a Forge defect. The manager lifecycle (dispatch, isolation, honest FAIL recording, retry, BLOCKED, UNCERTAIN crash recovery) was fully verified live during the outage.

**Resolution:** a Hermes one-shot worker replaces opencode as the striker:
`hermes chat -q {task} --in {workdir} --oneshot --yolo -t terminal,file --max-turns 20 --run-budget 300 --reasoning low`.
Two live PASS runs through BlacksmithOps (jobs `66f9cf6b` retry-after-provider-failure and `f2ecaffa` with durable file-backed stores): all four sub-gates PASS (review score 5), correct `multiply` edit confined to the worktree, tests re-run independently (2 passed), evidence + merge-queue reports loadable from disk, merge entry `approved=False` (human approval required), cost recorded, scratch repo and canonical Agency checkout unchanged. The opencode/proxy incompatibility remains open upstream but no longer blocks Forge builds.
