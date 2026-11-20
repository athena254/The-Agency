# Owner pilot UI review repairs — October 19, 2026

## Outcome and scope

Implemented R1/R3–R7 and defensive R2 hardening in the exclusive `fix/owner-ui-races-20261019` worktree, based on `19de5e71185254aa8805aebe186342747395a8a9`. This is offline UI evidence, not live integration or release approval.

Read the complete external independent review and frozen `SPEC_OWNER_PILOT_M2_M3_20261019.md`, actual assessment/risk models, original UI and test harness. Preserved the frozen spec, all backend/API models, legacy UI/tests and `pilot.html`. No dependency installation, provider calls, runtime/private DB access, environment/secret access, enablement, deployment, push or merge.

Only repository changes:

- `src/agency/prototype/static/pilot.js`
- `src/agency/prototype/static/pilot.css`
- `tests/pilot_ui.test.mjs`
- This handoff.

Parent's `1d4e897` clarification is respected without editing/replacing this checkout's frozen source: loopback shell/assets/CSRF bootstrap are public; every `/api/pilot` request carries the in-memory bearer. No endpoint or wire-field changes, reconciliation fallback or task resubmission were added.

## Repairs

| Finding | Implemented boundary | Regression evidence |
| --- | --- | --- |
| R1 HIGH | Capture auth epoch at requests/operations; check before 401 side effects and after JSON decode. Lock invalidates operations and aborts the session transport signal, but epochs remain authoritative even when fetch ignores abort. Bootstrap/unlock/init continuations are epoch guarded; bootstrap also has a generation. Unique write-owner identities protect continuation/finally busy resets across Lock and re-unlock. | Delayed valid submit/create/cancel receipts at fetch and JSON boundaries; Lock and re-unlock; completed admission body; stale 401; obsolete unlock/bootstrap/list stages; stale finally while a new write owns the busy slot; abort-signal assertions. |
| R2 defensive | Explicit author `[hidden] { display: none !important; }` for locked/withheld content. | Static stylesheet assertion only. **Parent's real Chromium baseline did not reproduce the reported CSS bug:** hidden workspace already computed `display:none`, height zero. This change is cross-browser defensive hardening, not a reproduced Chromium defect. |
| R3 | Reserve the write owner and snapshot draft, thread, request ID and cancellation target before the first await. Recheck ownership after CSRF recovery and before dispatch. No old recovery may repopulate cleared state, retarget a task, duplicate a write or cancel an already-terminal target. Admission receipts must match the captured thread as well as request ID. | Deferred CSRF with thread switch, second write, Lock, terminal cancellation target; wrong-thread admission receipt; draft not cleared in another selected conversation. |
| R4 | Separate task-operation, GET, render-snapshot and list generations. Terminal snapshots retained; older server `updated_at` snapshots cannot replace newer ones. Digest continuation requires current epoch, operation generation, render generation and exact active snapshot object. Cancel receipt must match captured task and thread; cancellation notice uses the retained current state. Poll continuation/finally is tied to its own poll object. | Old RUNNING GET after confirmed CANCELLED/new admission; subsequent terminal regression; overlapping same-task GETs; delayed digest after new task or re-unlock; competing same-task digest renders; wrong cancel IDs/thread; late nonterminal cancel receipt after terminal GET; stale task lists. |
| R5 | Per-message-load generation plus epoch/thread guards, including selection focus. Conversation list reads also have latest-load generation. | Deferred A→B→A and overlapping same-thread refreshes retain new committed messages and notes without stale focus; obsolete empty conversation list cannot clear new selection. |
| R6 | Native `type=button` task history controls show task and conversation IDs with `aria-pressed`. Selection uses only existing authenticated task GET; history alone cannot enable cancel. Fresh active selection starts bounded read-only polling; refresh and active-only cancellation work after Lock/re-unlock. No HTML change needed. | All seven task states, GET held before selection confirmation, refresh, terminal cancellation denial, active cancel only, task-selection ABA and wrong response IDs; no task-admission POST in recovery. |
| R7 | Exact `critical|high|medium|low|negligible` category membership, preserving actual assessed classic-report shape. | All five valid classic risk categories bind; invalid strings, prototype-property names, wrong case and non-string values fail closed. |

Lock aborts browser transport; it does **not** assert cancellation of an already admitted server execution. Re-unlock/history selection is read-only recovery, never automatic retry.

## Real execution and retained evidence

Evidence directory, outside the repository:

`C:/Users/alphi/agency-evidence/limited-autonomy-20261019/ui-repairs/`

Node used: `v24.19.0`. No Python/full-project suite was run, as delegated scope requires only both offline Node suites.

- Baseline: **37 passed, 0 failed** (`00-baseline.log`).
- **18 completed targeted RED runs, 20 failing test assertions**, followed by per-slice full-suite GREEN runs; R2's RED is static only. `audit.mjs` computes counts from raw logs into `summary.json` rather than estimating them.
- First lifecycle RED attempt (`02-R1-lifecycle-red.log`) timed out because the reproduced obsolete unlock dispatched into the newer held capability promise. The regression was corrected to assert redispatch after flushing microtasks *before* awaiting that obsolete operation. `02-R1-lifecycle-red-retry.log` records the completed behavioral RED; the original partial failure remains preserved.
- `01` write epoch → `02` lifecycle ownership → `03` hidden hardening → `04` CSRF snapshot → `05` task ordering → `06` terminal retention → `07` digest continuation → `08` cancel identity → `09` message ABA → `10` history recovery → `11` risk enum → `12` same-task GET ordering → `13` cancellation CSRF liveness → `14` cancel terminal continuation → `15` task-list ordering → `16` abort defense → `17` conversation-list ordering → `18` admission target binding. Matching `*-red*.log` / `*-green.log` preserve execution output. `19-refactor-green.log` and `20-supplemental-green.log` cover cleanup and additional verification cases.
- Final pilot suite: **45 passed, 0 failed** (`final-pilot.log`).
- Final unchanged prototype suite: **16 passed, 0 failed** (`final-prototype.log`).
- Combined suites: **61 passed, 0 failed** (`20-supplemental-green.log`; repeated final combined run retained separately).
- `node --check` for `pilot.js` and `pilot_ui.test.mjs`, and `git diff --check`: **PASS** (`final-checks.log`).
- Added-production-JS security scan: no hardcoded credentials, dynamic execution, unsafe HTML rendering, browser storage, logging or remote assets found. Scoped diff and path audit are retained externally. This is a bounded static/self-review, not an independent post-fix review.

Commands:

```text
node --test tests/pilot_ui.test.mjs tests/prototype_ui.test.mjs
node --test tests/pilot_ui.test.mjs
node --test tests/prototype_ui.test.mjs
node --check src/agency/prototype/static/pilot.js
node --check tests/pilot_ui.test.mjs
git diff --check
```

## Remaining parent gates / limits

- Parent must review the scoped commit and run live browser/API/provider integration. No browser, HTTP server, assistive technology or real backend/provider was exercised by this worker.
- Fake DOM native-button and hidden-property checks do not prove browser keyboard activation, focus exclusion, screen-reader announcements, stylesheet computation or responsive appearance. Parent's later browser check remains required; R2 has no claimed failing browser repro.
- Recheck locked/unlocked/401 visibility and focus, native task selection via keyboard, all task states and real error/assessment schemas against the integrated pilot routes.
- Offline mocks deliberately ignore abort and permit adversarial response ordering. They establish local controller isolation, not backend admission/idempotency/cancellation correctness or auth-policy qualification.
- No main merge, push, public exposure, deployment, startup enablement, paid-provider fallback or production readiness is authorized by this repair commit.
