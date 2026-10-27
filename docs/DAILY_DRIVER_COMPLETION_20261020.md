# Daily-driver completion ledger — October 20, 2026

Owner request: finish a working daily driver, not another plan. Existing frozen pilot/service specs remain the interfaces. This is an owner-only loopback chat/research pilot, not broad autonomy or a Hermes replacement.

## Acceptance criteria
1. The real execution service is reviewed and exercised by the integration owner, using isolated synthetic data; no canonical runtime DB/config writes.
2. All eight frozen pilot API method/path pairs and `/pilot` are wired to the real service. Authentication precedes body parsing; CSRF/origin/loopback/size errors are sanitized. Pilot mode cannot call legacy unmetered chat or expose owner metadata without authentication.
3. A supported command checks and starts the exact checkout with explicit provider config and isolated protected data/token; no public bind, proxy-header trust, autostart, environment provider fallback or legacy lifecycle. Clean stop and fresh-process restart preserve history and block interrupted/uncertain work without replay.
4. A live explicitly free approved provider completes an actual admitted conversation and calculator/tool loop; a subsequent turn uses only that thread's history. No success-shaped echo or mocked response counts as live evidence.
5. Real Chromium keyboard/browser checks cover unlock/create thread/task/result/history/lock, with denial/replay/cancel/restart checks at the actual HTTP boundary.
6. Route harness repairs fail closed before imports on unreviewed source/dependencies, never fingerprint private dotenv/databases, and distinguish passing denials from exercised functionality. Final route inventory reconciles with receipts.
7. Exact final source passes full Python/UI/Ruff/format/strict Windows+Linux-target checks and independent integrated review; remaining uncertainty is explicit. Run/stop/recovery instructions included.

## Scope/decisions
- Reuse existing MeteredModel/ToolDriver/Registry/Butler/ThreadStore/OwnerPilotService.
- Keep calculator, utc_time, web_search only; shell/files/fetch/governance/bridges/agent activation disabled.
- Existing <=45s provider,120s task,8model,4tool caps unchanged.
- No new dependencies unless necessary; no paid provider fallback, public deployment, main merge or PR. The owner subsequently explicitly authorized committing and pushing completed work on development branches, not merging or deploying it.
- Preserve existing branches/worktrees and private runtime data. Exclude unfinished launcher implementation from the completed publication checkpoint; retain its isolated worktree unchanged.
- Baseline integration clean b00a7bc; service candidate clean59648d1. Core historical2399passes/61UI is NOT qualification of the final application.

## Progress/evidence
- Parent service baseline: **199 passed**, zero failures/errors/skips. Candidate59648d1 applied without an initial commit; canonical main/runtime were not changed.
- All eight pilot API routes and GET `/pilot` are wired to the real service. Actual ASGI calculator/replay flow passes; cancellation drains the actual child and preserves UNCERTAIN outcomes. Replay does not rerun execution.
- API/HTTP-guard/prototype selection: **177 passed**, zero failures/errors/skips, one retained dependency warning. Covers private metadata auth, legacy chat503 before parsing, CSRF/origin/body/media/size guards, foreign resources, conflicts, headers and static shell.
- Independent service review reproduced publication after shutdown. Parent reproduced both queued-transaction and uncommitted-write windows, added a synchronous store fence linearized with actual publication COMMIT, and passed **80** service/store/budget/API/fence checks. Already committed success remains immutable. Final integrated re-review is a separate gate.
- Full integrated core/service/API qualification: **2450 Python tests passed**, zero failures/errors/skips, one inherited Starlette/httpx deprecation warning, reported **76%** line coverage. Source/test fingerprint `21ceacff0a45ec75eb40f1d4beb0cc6bf86c51e24ff9ba76b2b0445f5678b7cf` matches the current working bytes. The test selections overlap; never add their counts.
- Pre-publication root Ruff and format checks passed (270 files); strict whole-source mypy passed for Windows and Linux targets (160 source files each); **61 Node UI tests passed**. Linux was a static target on Windows, not native Linux execution. UI tests use fake DOM/HTTP boundaries, not a real browser. Python containment is not an OS sandbox or arbitrary-child containment.
- Scoped independent pre-commit reviews of both the service/API/publication-fence integration and the separate harness repair passed with no blocking security or logic findings. This does not qualify the unfinished launcher, actual browser, live admitted workflow or final integrated runtime.
- Preserved harness/test failures:api-red03 was a fixture Headers.update misuse, corrected before behavioralRED03b; api-red06 oversize test's implicit pytest ID exceeded Windows envvar length, fixed with explicit short IDs. Neither was relabeled product failure; original receipts retained.
- The API regression originally preserved at e712de3 was reproduced before router edits and now passes in the integrated API suite.
- Harness repair remains on a separate qualification branch. Parent reran **12 harness/self-check tests**, root-discovered scoped Ruff and formatting successfully. It preserves the reviewed archived baseline and rejects changed source before imports; this is not qualification of the current integrated routes.
- Launcher and browser workers were interrupted waiting for model responses. Partial launcher code/tests remain isolated and unqualified; the last saved launcher selection has two failures (corrupt-token handling and cold-import/ACL checking). No completed actual-browser qualification was delivered. No worker activity is implied by these historical dispatches.
- Actual approved free-provider diagnostic: `nemotron-3.5-lightning-free` returned literal `READY` through the frozen HTTP leaf under the45-second ceiling. This was a standalone harmless diagnostic, not an admitted calculator/recall task. Catalog-listed `minimax-m3.1-flash:free` rejected the key withHTTP403; its actual admitted attempt remained UNCERTAIN with no transcript or automatic retry. Canonical `.env` remained unchanged; no key, raw prompt, DB or private log is included in publication.

## Open blockers
- Finish and qualify the supported isolated launcher, integrate/review the harness, and exercise real browser/keyboard/start/stop/restart/crash recovery.
- Complete an actual admitted live model/tool/conversation-recall workflow. A literal provider response is not daily-driver qualification.
- Final whole-product qualification and independent review after the remaining integration, then an owner trial. This checkpoint is not a release or deployment; no main merge/public daemon/paid fallback is authorized.
