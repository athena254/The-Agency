# Remote DeepSeek UI implementation brief

You implement the calculator-verification UI in the existing owner workspace. The backend contract is finalized in `tasks/verification-contract.md`; read it first. Existing backend implementation has been built separately. Your worktree starts from the exact reviewed 8a52ec8 UI; do not implement or change backend APIs.

## Ownership
You may modify ONLY:
- `src/agency/prototype/static/pilot.html`
- `src/agency/prototype/static/pilot.css`
- `src/agency/prototype/static/pilot.js`
- NEW `tests/calculator_verification_ui.test.mjs`
- NEW `tasks/ui-worker-report.md` (your final report)
Do not change old tests, the frozen contract, Git/index/refs, credentials, settings, databases, other source, packages or deployment. Do not start a server, install anything, close user apps, use external network tools, or launch subagents.

## Existing source and interfaces
Read the real pilot.js controller and the three existing JS test files. They are the only baseline; retain strict existing validators and old behavior. The __pilotUI.createController test API and old DOM IDs must remain compatible. Existing unlock reads are frozen: no extra verification read on unlock. Do not weaken exact-key checks to accommodate a new response.

Add a separate integer-calculation form to the conversation area (labeled expression input, max120, supported-grammar hint, explicit submit button). Ordinary messages continue through the old endpoint and fields. Keep ordinary-task submission unchanged; implement a sibling submitCalculator path using the same authentication/CSRF and write-owner gates. The pending calculator request ID must be retained across ambiguous outcomes and never automatically resubmitted. A manual retry with unchanged thread/expression reuses its ID; changing criteria gets a new ID. Draft survives failed request. Do not erase a newer draft after a delayed receipt.

Add a separate visible calculation-check panel with an explicit Refresh check button. GET `/api/pilot/tasks/{task_id}/verification` is independent from the old task DTO and old a2.v1 diagnostic report. Show execution state separately from `passed`, `failed`, `unable_to_verify`; render expression, expected/observed values, method, output binding and scope limitation. For null receipts, display the exact reason as understandable not-requested/not-complete/legacy-no-stored-check, never success.

Validate the entire exact-key envelope and receipt from the frozen contract. Verify matching task/request/thread identities, execution state, receipt consistency, numeric strings, exact expression hash and exact saved response hash via existing sha256Hex (do not arbitrary-number parse the response). A malformed, wrong-ID or misbound payload is unavailable, not a passed badge. The server receipt is narrow arithmetic evidence, not a general proof of model honesty/tool use, peer approval or all facts. Preserve the old a2.v1 report's independently_verified=false.

Fence overlapping reads by epoch, selected task/thread, render snapshot and a monotonically increasing read generation. Clear private receipt DOM immediately on lock/401, task or thread selection change, a new task, a failed/malformed read, and before a refresh replaces old evidence. A prior pass must never remain painted for a new task or after a stale async completion. Use the existing apiFetch security boundary and its 401 behavior; never store bearer/receipt in browser storage, URLs, HTML or logs.

Keep the existing dark visual language. Improve hierarchy/spacing only as needed for clear usable verification controls; no fake agents, generic landing page, gradients or unrelated redesign. Native labels/buttons, keyboard submission, aria-live states, [hidden] rules and bounded text wrapping are required. The controller will test populated layouts at 320/768/1024/1440 widths in actual Chrome after integration.

## Test-first execution
Write one end-to-end controller test for the new submission/read flow, RUN it and preserve the actual behavioral RED before changing the UI. Then make that behavior GREEN. Continue small increments for wrong/unavailable/malformed receipt, lock/401 privacy, reverse-order/ABA reads and retained idempotent submission. Use real current controller+markup with deterministic fetch/DOM fixtures as existing tests do. Never modify existing tests or weaken assertions to get green. Label additions that pass initially supplemental rather than inventing historical REDs. The full JSON tool log retains your actual commands/results.

ONLY approved shell commands (run exactly, no pipes, extra flags, git or installation):
`node --test tests/calculator_verification_ui.test.mjs`
`node --check src/agency/prototype/static/pilot.js`
`node --test tests/pilot_ui.test.mjs tests/prototype_ui.test.mjs tests/task_home_ui.test.mjs tests/calculator_verification_ui.test.mjs`
Use the read/edit/write tools for files, not shell writes. Cwd is this worktree. stdin is closed; denied commands are not requests to broaden scope.

## Upstream impacts completed before editing
Parent ran GitNexus against exact base sources. Existing symbols you may modify INSIDE createController only:
- createController LOW:1direct,2upstream,0processes.
- updateSendState HIGH:11direct,26upstream,4processes (createThread,cancelTask,unlock,init).
- lock HIGH:2direct,18upstream,3processes (unlock,createThread,cancelTask).
- resetTaskPanel HIGH:4direct,18upstream,4processes.
- hideResult HIGH:3direct,15upstream,3processes.
- applyTask LOW:3direct,10upstream,1process.
- selectTask LOW:2direct,8upstream,1process.
- selectThread LOW:2direct,6upstream,2processes.
- wireEvents LOW:1direct,3upstream,0processes.
- renderTask LOW:1direct,8upstream,1process.
- renderCompleted LOW:1direct,5upstream,1process (preserve its old-report behavior).
The parent WARNED USER of the HIGH risk before authorizing this bounded work. Add new helper functions as needed. Do not change any other existing function, including apiFetch, submitTask, unlock, init or old schema validators; report a concrete need for a different existing-symbol edit rather than editing without required impact analysis.

## Finish
Run the combined existing+new tests, not only your new file, and node --check. Write tasks/ui-worker-report.md with implemented behavior, actual RED/GREEN commands, final actual counts, changed files, limitations and any blockers. Source review/actual browser acceptance belongs to the controller; do not claim either happened here. Do not commit or publish. End after the report and stable files are complete; do not expand scope.
