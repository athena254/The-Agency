# Limited-autonomy build checkpoint — October 19, 2026

Status: IN PROGRESS. Do not mistake this checkpoint for usable/released/deployed autonomy. Owner authorized research/implementation, harmless live qualification and milestone reports; consequential peer policy/public release/main merge/paid fallback remain outside scope.

## Workspace and preservation

Integration: C:/Users/alphi/agency-worktrees/limited-autonomy-20261019, branch feat/limited-autonomy-20261019. Canonical main C:/Users/alphi/theagency remains untouched. Start from docs/SPEC_LIMITED_AUTONOMY_RELEASE_20261019.md and frozen docs/SPEC_OWNER_PILOT_M2_M3_20261019.md (clarification a957d97).

M1 integrated commits e80fda1, fe3c300, bf65fb7: recovery platform typing, bounded admission diagnostics, model-leaf blocking propagation and cancellation/attempt telemetry. Parent full offline suite at bf65fb7:1927passed,0failed/errors/skips,1Starlette/httpx deprecation; source/test fingerprint stable. Ruff lint/format, strict mypy win32/linux all151sourcefiles passed;16legacyNodeUI tests passed. These results predate the subsequent two test-fixture regressions, not a claim of latest entire suite green. Historical admission timeout still unattributed (first unchanged reproduction passed); no deadline was increased.

Parent de764c9 repairs both A3 audit test modules' Lattice isolation. Two focused regressions failed for missing per-fixture lattice.db; explicit temporary LatticeConfig scoping then passed both entire modules78cases,1dependencywarning. Lint/format/diff clean. No runtime behavior changed by this fixture repair.

## M2 foundation in progress

Worker branch feat/owner-admission-20261019 in C:/Users/alphi/agency-worktrees/owner-admission-20261019 based573b484; exclusive NEW admission/{__init__,models,auth,store,budget}.py and focused tests/handoff. Frozen API clarification a957d97 was delivered through steering (do not blindly merge dirty worker). Parent must review concrete public signatures, integrate scoped commits and run focused checks BEFORE starting dependent runtime worker.

Required next F2 runtime: SAME MeteredModel/ToolDriver owner branch, immutable effective provider dispatch, strict owner policy and safe context, Butler/orchestrator admitted seam, durable OwnerPilotService. Ownership in spec. Do not invent a duplicate runtime or fabricate Telegram principals. F3 API/launcher depends on verified F2; F4 UI can implement frozen schema independently. Optional real Telegram pilot remains a separate authenticated poller integration, not existing public-dict ingress.

## M3 UI preserved, not integrated

OpenCode1.19.0 exact verified free alias opencode/deepseek-v4.1-flash-free. No distinct1017 snapshot ID was listed. Actual smoke responses and tool-based research completed. Bounded UI worker reached1200seconddeadline after real code/tests; parent preserved snapshot19de5e7 on feat/pilot-ui-20261019. New pilot.html/pilot.js/pilot.css and pilot_ui.test.mjs plus handoff. Parent actual Node run37passed (legacy+new). No backend integration/browser/live model claim; independent UI review pending. Do not copy untracked workspace briefs into commits. No worker reported final completion.

## Live evidence and blocker

Actual loopback HTTP preliminary probe entered all four app lifespans with synthetic isolated data/echo:35declared route cases+5securityprobes=40passed. Legacy authority routes correctly503; not enabled feature coverage or exhaustive generated/static route inventory. Evidence/harness retained.

Current configured Nara runtime model nemotron-3-super-free returned400 and is absent authenticated model catalog. Catalog-listed nemotron-3.5-lightning-free failed an initial25second preflight; one separate bounded45second/256token latency probe also timed out. No blind retry/echo fallback, canonical .env unchanged. OpenCode helper success does not qualify Agency runtime provider. Parent must establish approved working runtime provider before claiming usability. Never give credentials/runtime/user data to free CLI workers.

## Active review and next acceptance

Independent M1 review and UI review are read-only, not source writers. Integrate verified foundation -> runtime -> API/UI -> independently reviewed actual route/tool/owner/replay/cancel/restart cases -> harmless authorized real-model tool task -> actual browser DOM -> exact final Python/Node/lint/type matrices -> supported isolated launch/stop/rollback. Full bounded-autonomy/peer governance is not implemented. Existing authority/unsafe tool routes stay disabled. No main merge, push, public access or daemon deployment has occurred.

Private raw evidence root C:/Users/alphi/agency-evidence/limited-autonomy-20261019/. Do not commit raw prompts, databases, logs, secrets or capabilities. Use qualification receipts/JUnit tied to exact trees rather than adding historical pass totals together.
