# Calculator verification v1 — frozen shared contract

## Scope
Only bounded integer arithmetic is checked. A passed receipt means the exact saved final answer equals a deterministic independent recomputation of the exact admitted expression. It does NOT assert that a model used a tool, that arbitrary prose is true, that other answer claims are safe, or that peers approved anything. The existing a2.v1 report remains `independently_verified=false`. Actual calculator tool execution is separately exercised in integration acceptance.

## Input grammar and pre-execution criteria
Expression: 1–120 ASCII characters, digits, spaces/tabs, `+`, `-`, `*`, parentheses. Integers use `0` or a nonzero digit followed by digits (no leading zeros), unary `+`/`-` allowed. No division, decimals, exponentiation, scientific notation, names, comments, Unicode digits or newline. Maximum 64 lexical tokens, 12 nested parentheses/unary recursion levels, and absolute operand/intermediate/final magnitude <= 10^15. Invalid expression is rejected before admission/model calls. Parser must not call eval, exec or CalculatorTool._compute.

Canonical admitted message, preserving expression bytes exactly:
```
[agency:calculator.v1]
expression={expression}
Return only the base-10 integer result, without prose or markup.
```
The existing admission hashes/saves this exact message before model dispatch. A client may explicitly opt in via either the new form/endpoint or this exact message through the legacy tasks endpoint; arbitrary conversational prose is never inferred to be a verification request. Invalid/noncanonical marker messages stay ordinary unverified tasks.

## Endpoints — all existing owner authentication/CSRF/size guards apply
`POST /api/pilot/calculator-tasks`
Request EXACT fields: `request_id`, `thread_id`, `expression` (strings; IDs follow existing length/pattern rules). Response: unchanged OwnerTaskView, 202 on admission / 200 replay. Internally maps to OwnerTaskRequest with canonical admitted message. Reusing a request ID with changed expression/thread conflicts; there is no automatic retry or second publication path.

`GET /api/pilot/tasks/{task_id}/verification`
Exact envelope fields:
- `task_id`, `request_id`, `thread_id`: persisted identity strings.
- `execution_state`: existing uppercase task state.
- `receipt`: null, or the exact persisted CalculatorReceipt below.
- `reason`: `available`, `not_requested`, `not_completed`, or `legacy_result`.

`available` requires completed execution and a valid bound stored receipt. Ordinary tasks have null receipt / not_requested. Explicit calculator tasks not completed have null receipt / not_completed. An old completed opt-in message without a stored receipt is null / legacy_result; do not run its model again or retroactively fabricate a historical receipt. Corrupt/misbound saved receipt/result fails closed with the existing sanitized store error, not a fabricated passed/unavailable response. GET performs no model/tool dispatch or writes.

## CalculatorReceipt — exact keys
- `schema_version`: `calculator.v1`
- `method`: `bounded_integer_arithmetic/v1`
- `task_id`, `request_id`, `thread_id`: exact admitted identity
- `expression`: exact admitted expression
- `criteria_sha256`: lowercase SHA-256 of UTF-8 expression bytes
- `output_sha256`: lowercase SHA-256 of exact UTF-8 response bytes (before trimming)
- `status`: `passed`, `failed`, `unable_to_verify`
- `reason`: respectively `matches`, `mismatch`, `answer_not_integer`
- `expected`: canonical base-10 integer string
- `observed`: canonical base-10 integer string or null

Final response is eligible only if the entire whitespace-trimmed response is an ASCII signed base-10 integer of bounded length/magnitude. No prose/code-block/first-number extraction. A matching numeric answer passes; a different eligible numeric answer fails; unsupported/missing numeric answer is unable_to_verify with observed=null. Missing task completion/evidence uses the null-receipt envelope, never a pass. The receipt model rejects contradictory states, extra keys, booleans/numeric values in string fields and nonfinite/unbounded data.

## Atomic persistence and compatibility
For opt-in tasks only, add `verification` to the staged result JSON and compare/bind it again during the same existing publication transaction as messages and task completion. Existing results without a receipt retain EXACTLY task_id/response/assessment keys. Existing /tasks, capabilities, old report validation and read-only unlock sequence remain unchanged. No new database or background verifier. Store validates identity, admitted input digest, expected outcome and exact output binding before staging/committing; caller-supplied forged receipts cannot establish success. Reopening/replay returns the identical saved receipt and unchanged attempt counters; validation is permitted, redispatch/re-execution is not.

## UI operation matrix and ownership
DeepSeek/OpenCode remote owns pilot.html, pilot.css, pilot.js and one new tests/calculator_verification_ui.test.mjs. It must preserve existing tests and legacy controls.
- Explicit integer-calculation mode/input and supported-grammar hint; separate from ordinary message input.
- Create via frozen calculator endpoint with one retained request ID; failure retains draft, ambiguous outcomes never auto-resubmit.
- Full detail: execution state separately from checker verdict, expression, expected/observed values, method, response binding and limitation text.
- Refresh: separate authenticated verification GET; not part of unlock bootstrap. Fence by session epoch, task/thread selection and read generation. Clear private receipt immediately on lock, selection change, failed/malformed fetch or 401. No stale passed badge during a new task or pending refresh.
- No edit/delete/recheck endpoint in scope; historical receipts immutable. Existing cancellation and saved history continue to work.
- Use textContent, native labeled controls, keyboard access, aria-live verdict changes and the existing dark design tokens. Test populated layouts at 320/768/1024/1440 widths.
- Failure/null-receipt/malformed/out-of-order states must be explicit, not rendered as empty success. Do not label a passed narrow check as Agency-wide independent verification.

## Acceptance
Behavioral RED→GREEN; supported correct/wrong/prose cases; parser rejection and bounds; forged/mismatched receipt rejected; ordinary tasks unchanged; wrong owner denied; cancellation has no successful receipt; persisted passed/failed/unable verdict survives restart; identical request replay and verification reads do not increment attempts; real calculator/launcher/browser with synthetic HTTP provider, labeled synthetic; SPEC then QUALITY independent review on exact final sources. Remote named-model readiness/implementation is a required separate gate.
