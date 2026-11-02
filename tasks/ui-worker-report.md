# Calculator-verification UI worker report

## Scope and changed paths
Implemented the bounded calculator-verification UI in the five owned paths only:

- `src/agency/prototype/static/pilot.html`
- `src/agency/prototype/static/pilot.css`
- `src/agency/prototype/static/pilot.js`
- `tests/calculator_verification_ui.test.mjs` (new)
- `tasks/ui-worker-report.md` (this report, new)

No backend, frozen contract, old test, credential, runtime store, Git index/ref, package, deployment or unrelated source file was changed. No server, network tool, install, subagent or commit was used.

## Implemented behavior
- Added a separate labeled integer-calculation form in the conversation area with a 120-character expression input, supported-grammar hint, explicit submit button and keyboard/native form submission.
- Added a sibling `submitCalculator` path using the frozen `POST /api/pilot/calculator-tasks` request fields `request_id`, `thread_id` and `expression`, with the existing bearer/CSRF, write-owner and capability gates. Ordinary `submitTask` still uses the old `/api/pilot/tasks` endpoint and fields.
- Retained one pending calculator request ID across network, 5xx/429 and malformed-response ambiguity. It is never automatically resubmitted. A manual retry with the same thread/expression reuses the ID; changed criteria gets a new ID. Failed requests retain the draft, and a delayed receipt does not erase a newer draft.
- Added a separate visible calculation-check panel with an explicit `Refresh check` button and an independent authenticated `GET /api/pilot/tasks/{task_id}/verification`. Unlock/init/admission do not read verification.
- Validated the exact envelope and receipt key sets, task/request/thread identities, execution state, status/reason/observed consistency, canonical bounded integer strings, exact expression hash and exact saved-response hash using the existing `sha256Hex`. The UI does not arbitrary-number parse the saved response. Malformed, wrong-ID or misbound payloads render unavailable, never a passed badge.
- Rendered execution state separately from `passed`, `failed`, `unable_to_verify`, plus expression, expected/observed values, method, output binding and narrow scope limitation. Null receipts render explicit not-requested, not-complete and legacy-no-stored-check reasons. The old `a2.v1` report's `independently_verified=false` behavior is preserved.
- Fenced verification reads by session epoch, selected task/thread, task render snapshot and monotonic read generation. Private receipt evidence is cleared on lock/401, task or thread selection change, new task, failed/malformed read and before a refresh replaces old evidence. A prior pass cannot be repainted by a stale async completion.
- Added dark-theme styles for the calculator form and verification panel, with wrapping and existing `[hidden]`, reduced-motion and responsive rules preserved.

## Actual RED/GREEN history
- Attempt 1's first new-test run was a **harness failure**, not a genuine behavioral RED: the new test's fake document/byId omitted `pilot-calculator-expression`, so it failed at old test line 354 (`Cannot set properties of undefined`) before behavior ran. This is disclosed and not claimed as behavioral RED.
- The coordinator independently observed the genuine behavioral RED on the still-unwired flow: `1` failure, with the assertion expecting one calculator POST but observing zero, at `tests/calculator_verification_ui.test.mjs:359`. That assertion was preserved and not weakened.
- After controller wiring, the first end-to-end test went GREEN.
- Additional coverage was then added as **supplemental later additions**, not historical REDs: all three verdicts, all three null-reason paths, malformed/wrong-ID/hash-misbound rejection, lock/401 privacy, stale and reverse-order/ABA reads, retained request IDs/drafts, parser bounds and ordinary-task compatibility.
- During supplemental test development, the new test fixture briefly leaked a non-contract `response` key into receipt fixtures. That was corrected only in the new test file; no source behavior was weakened.

## Final commands and counts
Run separately and exactly as allowed, against the final preserved bytes:

1. `node --test tests/calculator_verification_ui.test.mjs`
   - tests `9`, pass `9`, fail `0`.
2. `node --check src/agency/prototype/static/pilot.js`
   - exit `0`, no output.
3. `node --test tests/pilot_ui.test.mjs tests/prototype_ui.test.mjs tests/task_home_ui.test.mjs tests/calculator_verification_ui.test.mjs`
   - tests `76`, pass `76`, fail `0`.

## Prior-attempt interruptions
Prior attempts 1, 2 and 3 timed out. Attempt 2 historically exhausted the free quota. Attempt 3 logged a transient socket closure before subsequent tools succeeded. These historical attempts are not claimed to be clean, and the final counts above are from the separately rerun final commands, not from an uninterrupted session.

## Remaining limitations / controller-owned work
- Actual browser acceptance at 320/768/1024/1440 widths in real Chrome was not performed here.
- Independent source/SPEC/QUALITY review, backend integration, real calculator/launcher/provider exercise, deployment, commit and publishing were not performed here.
- The calculator check is narrow bounded integer arithmetic evidence only; the UI does not claim model honesty, tool use, peer approval or general fact verification, and the old `a2.v1` report remains `independently_verified=false`.

## Strict-enum continuation (two reproduced bugs)

### Scope
Only two source lines changed, both enum membership checks inside the named helpers:

- `normalizeVerification`: `TASK_STATES[value.execution_state]` and `VERIFICATION_REASONS[value.reason]` now require `Object.prototype.hasOwnProperty.call(...)`.
- `normalizeCalculatorReceipt`: `CALCULATOR_STATUS[value.status]` and `CALCULATOR_REASON[value.reason]` now require `Object.prototype.hasOwnProperty.call(...)`.

No other source bytes, functions, HTML, CSS, old tests, frozen files or runtime state were changed. Tests were appended only to `tests/calculator_verification_ui.test.mjs`; every pre-existing test byte was preserved.

### Real RED before repair
The appended regressions reproduced both exact payloads as accepted values:

- Verification: `reason: 'toString'` was accepted with a `COMPLETED`/null-receipt envelope.
- Receipt: `status: 'constructor'` was accepted with `reason: 'answer_not_integer'` and `observed: null`.

Actual RED count: `11` tests, `9` pass, `2` fail. The failures were the exact reproduced cases at test lines 856 and 910. Inherited `toString`, `constructor`, `__proto__` and `hasOwnProperty` were also covered for both enum fields, while valid enum positive coverage was retained.

### GREEN after repair
- `node --test tests/calculator_verification_ui.test.mjs`: `11` tests, `11` pass, `0` fail (prior calculator coverage remains 9/9; the increase is only the two appended regressions).
- `node --check src/agency/prototype/static/pilot.js`: exit `0`, no output.
- Joined suite: `78` tests, `78` pass, `0` fail (prior 76/76 plus the two appended regressions only).

### Preserved limitations
All earlier limitations remain: no real-browser acceptance, independent review, backend integration, deployment, commit or live-provider exercise was performed. No source change was made after the final checks.

## Supplemental non-string enum guards

Within the same two validators only, the four enum fields now require both a string type and strict own membership:

- `normalizeCalculatorReceipt`: `status` and `reason`.
- `normalizeVerification`: `execution_state` and `reason`.

The existing local own-membership checks and all baseline validators/maps were kept. No other source, HTML, CSS, old tests or frozen files changed. One test was appended to `tests/calculator_verification_ui.test.mjs`; all 11 prior tests were preserved.

### Real RED
The appended test looped arrays, objects, `null`, booleans and numbers across the four fields, plus inherited `execution_state` names and the exact array payloads. RED: `12` tests, `11` pass, `1` fail; the first accepted non-string was `reason: ["not_requested"]`.

### GREEN
- Calculator tests: `12` tests, `12` pass, `0` fail (prior 11 plus the one appended regression).
- `node --check src/agency/prototype/static/pilot.js`: exit `0`, no output.
- Joined suite: `79` tests, `79` pass, `0` fail (prior 78 plus the one appended regression).

Valid string enum coverage remains green, and all earlier browser/review/backend/deployment limitations are unchanged.
