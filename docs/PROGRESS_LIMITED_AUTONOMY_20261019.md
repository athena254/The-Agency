# Limited-autonomy build progress

## Current checkpoint — October 20, 2026

Status: IN PROGRESS, NOT DAILY-DRIVER READY. No release/deployment claim. Owner authorized continued implementation and milestone reports; no paid-provider fallback, consequential peer-policy changes, public rollout or main merge is authorized.

### Implemented, integrated and parent-qualified

- Owner model/budget/policy, immutable actual dispatch, thread-only context and HTTP guard are integrated. Earlier parent checks: 125 model/policy/budget, 168 context/HTTP/auth and 61 Node UI tests; these are separate overlapping scopes, not additive totals.
- Durable uncertainty helper worker edac630 is integrated. It checks ALL owner-scoped uncertain turns, including those older than the visible task list, and fails closed on storage/lifecycle errors.
- Public owner timeout defect was behaviorally reproduced: 45.0001/60.0 seconds failed to raise. Worker c9daf84 added the minimal owner-only >45-second rejection; beta timers and all deadlines remain unchanged. Parent integrated timeout/uncertainty/store/model/policy qualification at 3209655 passed **152 tests in 46.64 seconds**, zero failures/errors/skips.
- Registry/Driver worker 73a281c is reviewed and integrated as **e05919d**. It uses exact owner model/registry/policy/grant/audit/task binding, durable tool reservations, shared audited intent/outcome pipeline, normal/forced-final owner contexts and blocking typed uncertainty. Parent integrated owner/tool/model/policy/budget/beta/legacy qualification passed **361 tests in 58.65 seconds**, zero failures/errors/skips. HTTP provider leaves were synthetic and external Python network traffic blocked; not live provider evidence or an OS sandbox.

### Actual work now running / next gates

- Service contract committed at **b0f2f1e**, docs/SPEC_OWNER_SERVICE_EXECUTION_20261020.md. Exclusive service worker owns admission/service.py plus additive Butler/orchestrator helpers and tests. It must use the SAME real model/Driver, no broad legacy startup/Lattice activation/shared memory, one tracked durable execution, cancellation-safe ownership, stage -> gateway audit acknowledgement -> quota finish -> atomic transcript/result commit. Service delivery and parent qualification remain pending.
- Fresh full **pre-service Core** qualification at **55d77f5** completed: **2399 Python tests passed**, zero failures/errors/skips, one retained Starlette/httpx dependency deprecation warning; reported line coverage **75%**. Root Ruff lint and format passed (265 files); whole-source strict mypy passed for Windows and Linux targets (158 source files each); **61 Node UI tests passed**. Parent parsed JUnit and verified all six receipt/source fingerprints match the same unchanged src/tests tree. Linux was a static target on Windows, not native Linux execution. These results qualify the integrated Core, NOT the not-yet-integrated service/API or live model. Evidence integration/owner-core-full-pre-service-20261020 and sibling owner-core root/UI/Linux receipts.
- Free OpenCode/DeepSeek fresh smoke passed with reported cost zero. Its strictly test-only API tracer is preserved on separate branch test/owner-api-red-20261020 at **e712de3**. Parent reproduced exactly one behavioral failure, zero collection errors: actual GET /api/pilot/capabilities returns404 instead of the required sanitized503 when unconfigured. No API production fix is authorized before real service delivery; this intentionally failing test is NOT in the integration branch.
- Route harness worker **97c72f2** delivered a real inventory/receipt artifact, reporting72 surfaces and94 ASGI cases, with28 safe-handler and44 boundary-only classifications. Eight pilot API routes plus GET /pilot are explicitly missing; its exit2 is NOT readiness. Independent read-only review is checking counts, containment and future-source guard assumptions before integration/rerun. Do not claim all handlers work from boundary responses.
- Terminal/file startup failures no longer prevented the latest parent checks; the earlier220s/140s/no-JUnit attempts remain INCOMPLETE. Root cause is not established. No production deadline was increased.

### Remaining release blockers

The actual owner execution service, API/router and supported isolated launcher are unfinished/unqualified. Real browser/keyboard, provider/tool loop, duplicate/cancellation/restart/recovery matrices, final full gates and independent integrated-runtime review remain open. Nara's last actual bound inference still timed out at45 seconds; no working replacement or paid-spend approval has been received. Configured-provider validity is not successful live qualification. No bot/poller/Agency runtime is deployed by this work; canonical main and private runtime configuration/data remain untouched.

## Historical checkpoints — October 19, 2026

The sections below preserve earlier source-pinned findings and worker states. They are historical, not current activity/readiness claims. The October20 checkpoint above supersedes their progress status.

Historical status: IN PROGRESS. Do not mistake the earlier checkpoint for usable/released/deployed autonomy. Owner authorized research/implementation, harmless live qualification and milestone reports; consequential peer policy/public release/main merge/paid fallback remain outside scope.

## Workspace and preservation

Integration: C:/Users/alphi/agency-worktrees/limited-autonomy-20261019, branch feat/limited-autonomy-20261019. Canonical main C:/Users/alphi/theagency remains untouched. Start from docs/SPEC_LIMITED_AUTONOMY_RELEASE_20261019.md and frozen docs/SPEC_OWNER_PILOT_M2_M3_20261019.md (clarification a957d97).

M1 integrated commits e80fda1, fe3c300, bf65fb7: recovery platform typing, bounded admission diagnostics, model-leaf blocking propagation and cancellation/attempt telemetry. Parent full offline suite at bf65fb7:1927passed,0failed/errors/skips,1Starlette/httpx deprecation; source/test fingerprint stable. Ruff lint/format, strict mypy win32/linux all151sourcefiles passed;16legacyNodeUI tests passed. After the two fixture regressions were integrated, parent final M1 full qualification at1d4e897 passed1929cases with0failures/errors/skips and1retaineddependencywarning. Receipt integration/m1-final-fixtures-full/result.json identifies exact revision/trees; source fingerprint unchanged during run. Scoped independent M1 review found no new repair blocker, not production-security approval. Historical admission timeout still unattributed (first unchanged reproduction passed); no deadline was increased.

Parent de764c9 repairs both A3 audit test modules' Lattice isolation. Two focused regressions failed for missing per-fixture lattice.db; explicit temporary LatticeConfig scoping then passed both entire modules78cases,1dependencywarning. Lint/format/diff clean. No runtime behavior changed by this fixture repair.

## M2 foundation and provider binding integrated

Foundation worker81dfdfa integrated98aa278; immutable provider snapshot7cc374b integrated8611b5f. Concrete owner authentication, exact shared models, real SQLite lifetime-lock/idempotency/atomic publication, private quota mapping and opaque leases are now on disk. Parent read all new foundation source and ran combined foundation/provider qualification:165passed,0failures/errors/skips at98aa278 with stable source fingerprints. Parent caught3RuffI001 test-import ordering errors in the worker delivery, corrected only those imports, reran32foundationtests and passed worktree-root Ruff/format plus strict win32/Linux-target mypy on all5new foundation modules. Checkpointba1a42b records the source/spec. Native execution was Windows, not Linux.

Provider owner methods freeze endpoint/model/account/request bounds before awaits, use actual immutable HTTP leaf, disable redirects/proxy inheritance, and have no retry/fallback. Parent integrated-provider focus209passed, Ruff and Windows/Linux-target mypy passed. This is binding verification, NOT successful live model qualification.

Independent foundation/provider review completed againstba1a42b: no new reproducible blocker in inspected scope,165targetedtests+7adversarialprobes passed. Parent parsed both JUnit files and rechecked all18manifest source/dependency texts. See docs/REVIEW_OWNER_FOUNDATION_PROVIDER_20261019.md; this is not runtime/API/release approval. F2a contract/ownership remain frozen in docs/SPEC_OWNER_CORE_INTEGRATION_20261019.md. First free OpenCode core attempt was STOPPED for production-first edits without a new failing test;5owned files hash-preserved externally and isolated branch restored, NO code integrated. Fresh free smoke passed; smaller test-only tracer phase now has900second enforced deadline and must stop after actual expected behavioral RED, before parent-authorized GREEN. Core must extend the SAME MeteredModel/ToolDriver/Registry; service/Butler/orchestrator/API remain dependent next slices. No fabricated Telegram principal, paid fallback or alternate unmetered assistant.

## M3 repaired UI integrated, live qualification pending

Original UI checkpoint19de5e7 integrated0b96284, repaired worker4d7ec9e integratedb99f46c. Parent reviewed the scoped repair diff and ran BOTH integrated Node suites:61passed,0failed/skipped/cancelled; both syntax checks and gitdiffcheck passed. Repairs cover session/401 isolation, stale write/finally ownership, CSRF target capture, task/read/render and digest ordering, terminal-state retention, message ABA races, durable task selection and actual risk enum validation. This does not prove backend/browser integration.

Explicit [hidden] CSS was added as defensive hardening. The original visibility leak did NOT reproduce in actual Chromium (hidden workspace computed displaynone), and no browser RED is claimed. Existing prototype UI was retained. A distinct DeepSeek1017 snapshot ID was not listed; used the exact available opencode/deepseek-v4.1-flash-free alias. Initial OpenCode UI authoring hit its1200second deadline; its partial output was preserved, independently reviewed and then repaired rather than silently treated as finished.

## Live evidence and blocker

Actual loopback HTTP preliminary probe entered all four app lifespans with synthetic isolated data/echo:35declared route cases+5securityprobes=40passed. Legacy authority routes correctly503; not enabled feature coverage or exhaustive generated/static route inventory. Evidence/harness retained.

Current configured Nara runtime model nemotron-3-super-free returned400 and is absent authenticated model catalog. Catalog-listed nemotron-3.5-lightning-free failed initial25second and separate45second/256token legacy diagnostics. A NEW single-attempt manual qualification at about20:33EAT used the ACTUAL immutable owner generate_bound HTTP leaf: authenticated catalog200, exact free model listed, snapshot valid, then ReadTimeout at45seconds; no successful model response. Receipt live-provider/owner-bound-20261019.json. This is NOT an admitted task replay, nor automatic runtime retry. Canonical .env unchanged; no paid/subscription/runtime-proxy fallback chosen. OpenCode helper success does not qualify Agency runtime provider. A working approved runtime provider is still a hard live-usability blocker. Never give credentials/runtime/user data to free CLI workers.

## Active review and next acceptance

Independent M1 review and UI review are read-only, not source writers. Integrate verified foundation -> runtime -> API/UI -> independently reviewed actual route/tool/owner/replay/cancel/restart cases -> harmless authorized real-model tool task -> actual browser DOM -> exact final Python/Node/lint/type matrices -> supported isolated launch/stop/rollback. Full bounded-autonomy/peer governance is not implemented. Existing authority/unsafe tool routes stay disabled. No main merge, push, public access or daemon deployment has occurred.

Private raw evidence root C:/Users/alphi/agency-evidence/limited-autonomy-20261019/. Do not commit raw prompts, databases, logs, secrets or capabilities. Use qualification receipts/JUnit tied to exact trees rather than adding historical pass totals together.
