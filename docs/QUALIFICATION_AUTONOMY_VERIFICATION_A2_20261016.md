# A2 implementation and local qualification — October 16, 2026

## Current status in plain English

A2 is implemented in the isolated A2 worktree and passed local offline qualification. It reports what actually ran and which checks actually happened. It does **not** establish factual correctness, independent verification, safe autonomous operation or peer authorization.

The owner superseded the historical Cline/free-worker prerequisite by directing the current assistant to code directly without delegation. No worker, Cline authentication, auxiliary model, paid fallback, provider reconfiguration, push, merge, bot restart or deployment was used in this continuation. The current assistant wrote and self-reviewed the code. **Independent review remains unperformed.**

`HANDOFF_AUTONOMY_VERIFICATION_A2_20261016.md` and `f71212f` are historical pre-implementation records. Their authentication blocker and unimplemented status are not the current status. This report supersedes their continuation instructions; it does not claim authentication succeeded.

## Checkpoints and exact tested revision

- Authorization amendment: `5f8e6757b0367ba2dfa927a368ca93d72e55708a`.
- Tool/classic reporting and zero-work/failure repair: `2aa2efd4e573fa51d9fa22dccab95132342b95bb`.
- Complete runtime/consumer implementation tested: `f710a3f835e4dc033d2845994e31392c177fffd7`.
- Qualified source Git tree: `3e5818e450be307b4d040f9c5252d2f30ba571a1`.
- Qualified tests Git tree: `81902cb80f3dd26dbe04a6f816150f02f72338bf`.
- Raw working-file source/test fingerprint before and after every final gate: `e8a052ee73e97546cc2067ab39da416925ee7274ed15ca51f46180312cad8d33`.
- Branch: `docs/autonomy-verification-a2-20261016`.
- Worktree: `C:/Users/alphi/agency-worktrees/autonomy-verification-a2-20261016/`.
- Canonical main was rechecked clean and unchanged at `713a4e311b1e071fbd6c5cf103b995d17aa7e5a2` after qualification. Main is not the A1/A2 branch; neither has been silently integrated there.

A subsequent documentation-only checkpoint may cite this qualification only with equal `src`/`tests` trees. No changed-source qualification is inferred from a previous run.

## What changed

- Added frozen, recursively immutable `a2.v1` reports with exact terminal statuses, three ordered criteria, explicit heuristic provenance, risk provenance and strict SHA-256 binding over the exact rendered UTF-8 body.
- Rejected unknown/extra schema fields, duplicate/missing criteria, contradictory summaries, nonfinite/coerced/boolean scores, inconsistent legacy heuristic verdicts, invented risk coverage and independent-verification claims.
- General/research tool loops report checks not performed and risk not assessed, with no added verifier, risk-engine or model calls.
- Classic runs expose only real keyword/denylist outcomes. Model-supplied ground truth/assessment objects do not become an oracle. Accuracy stays unperformed. Newly generated execution findings remain UNVERIFIED with confidence zero; historical findings are not rewritten.
- Empty classic plans fail with zero attempts, empty output, `empty_plan`, no execution/checker/evidence/memory/audit work, and FAILED task state. Blank answers and unsuccessful execution units cannot become completed aggregate work.
- Constructed the report/result before success memory and terminal completion. Exceptions and cancellation best-effort mark a still-running task failed and preserve the original exception. Failed aggregate execution does not store success execution memory. Tool-provider exception bodies are excluded from the returned failed artifact.
- Butler text replies and Telegram research replies show deterministic runtime notices. Report binding checks both the actual returned identity and submitted task identity. Failed heuristic checks do not become a Telegram delivery failure or polling retry.
- Isolated turns retain tool/memory/thread/evidence/Lattice-task exclusion, preserve actual terminal failure statuses and downgrade blank completion to failure. Butler carries a bound report plus the actual task ID to the prototype API; absent or mismatched reports remain null.
- The browser shows a separate plain-text runtime line, validates the isolated-only response shape and both exact body digests, preserves Unicode/CRLF/BOM, and treats absent/malformed metadata or unavailable WebCrypto as unavailable. It never parses model text as assessment data or HTML.
- Isolated failure/timeout logging now records exception class rather than private bodies/tracebacks.

### Bounded implementation refinements

Inspection found that `ButlerReply` needed an additive optional `task_id` as well as `assessment`; otherwise the API could not independently compare the report ID to its result ID. This is recorded in the specification. It adds no inbound field, route or authority.

Compatibility changes update only completed-response expectations in `test_beta_failure_integrity.py`, `test_beta_principal_hop.py`, and `test_prototype_app.py`. Existing denial, private scope and isolation assertions remain. The UI safety test now forbids an actual quoted Authorization header rather than rejecting the plain-English word in an explicit denial of authorization; HTML/storage/credential-header restrictions remain.

## Final qualification results

Final gates ran October 16, 2026, **17:45:14–17:48:01 UTC**, all on the exact implementation revision above:

- Full pytest: **1,794 passed; zero failures, errors or skips** in parsed JUnit.
- The two new focused Python files contribute **88 cases**, counted from final JUnit, not estimated.
- Ruff check: passed.
- Ruff format check: passed, 244 files already formatted.
- Strict mypy: passed over 151 source files.
- Node UI tests: **16 passed**, including `node --check`, separate status rendering, malicious model markup, exact byte binding and unavailable metadata behavior.
- Five cold imports passed in fresh Python processes: `agency.tools.driver`, `agency.llm.metered`, `agency.telegram.handler`, `telegram_bot`, and `agency.agents.assessment`.
- One existing Starlette/httpx deprecation warning remains visible. No warning suppression or dependency refresh was introduced.

The selected commands, timings, exit codes, before/after fingerprints, parsed JUnit totals, iteration outcomes and raw-evidence hashes are committed in `research-evidence/autonomy-a2-direct-qualification.json`.

### TDD and review evidence

Behavioral RED/GREEN pairs include tool reports (`direct-tool-red-valid` → `direct-tool-green`), classic truthfulness (`direct-classic-red` → `direct-classic-green`), zero-work/finalization failures (`direct-failures-red` → `direct-failures-green-2`), strict schema/binding (`direct-schema-red` → `direct-schema-green`), consumers (`direct-consumers-red` → `direct-consumers-green`), browser reporting (`direct-ui-red-valid` → `direct-ui-green`), and review refinements (`direct-review-red`, `direct-review-red-2`, `direct-ui-bom-red` → `direct-review-green`, `direct-ui-final-candidate`). The final suite verifies the integrated revision.

Initial malformed synthetic JSON and a JavaScript fixture syntax error were corrected before their corresponding valid behavioral RED runs; those initial failed attempts remain in evidence and are not represented as feature regressions. The first full integration run had three deliberate response-contract expectation failures and retained its failure log. Import-order/format failures were also retained before correction. No failing run was overwritten or reclassified as a final pass.

This was a self-review, not an independent audit. Review regressions cover inconsistent legacy scores, wrong-task binding, isolated exception privacy, actual in-flight cancellation and successful delivery despite failed heuristic checks.

## Acceptance coverage

- A2-T01: actual general/research tool-loop reports with verifier/risk spies; provider failure and blank tool answer controls.
- A2-T02: actual classic wrong-answer/self-oracle fixtures, visible legacy scores, missing accuracy, unverified findings and real risk categories.
- A2-T03: literal ID preservation; None/dict/numeric/Unicode rendering; exact body hashes; changed task/status/body downgrade; joined mixed-subtask digest; browser CRLF/BOM binding.
- A2-T04: frozen nested records/tuples; JSON round trip; invalid fields/tokens/digests/criteria/aggregates/scores and mutation attempts.
- A2-T05: actual provider failure, injected executor timeout, None/blank answers, failed tool loop and mixed classic results without success effects.
- A2-T06: injected empty planner graph at the real orchestration boundary, FAILED task, zero attempts and operation spies. The ordinary planner fallback is unchanged.
- A2-T07: not-performed, needs-review and failure precedence, partial risk coverage and no vacuous empty-plan completion.
- A2-T08: real Butler and Telegram notices, exactly one delivered failed-check reply, outbound API metadata, null legacy/misbound metadata and rejected inbound assessment.
- A2-T09: isolated real paths and existing API/UI regressions; no new tools/stores/threads/mirroring; failed/timeout non-2xx handling retained; safe text rendering.
- A2-T10: complete existing suite including all A1 recall, beta-denial, owner/thread scope, metering/import and cancellation regressions.
- A2-T11: injected verifier, risk, evidence, audit, memory and report-construction exceptions; cancellation at persistence and during a real driver/model await; no success return, task failure, retained unverified evidence; actual memory-before-terminal-state assertion.
- A2-T12: clean isolated source at qualification, unchanged clean main, explicit test source paths and provider, stable source/test fingerprints, exact Git trees and external evidence receipts. No rollout or authority claim.

## Containment, evidence location and limitations

Qualification used the discovered canonical Python 3.11.16 interpreter as a read-only toolchain, but explicit PYTHONPATH/test paths selected the isolated A2 checkout. Each run used scratch CWD, data, logs, test temporary directories and explicit `AGENCY_LLM_PROVIDER=echo`, `AGENCY_LLM_MODEL=echo`. Secret-bearing provider environment variables were removed; the tested checkout had no `.env`.

`direct-final-boundaries.json` independently checked actual imported source paths, the installed Python socket guard, rejection of external DNS/connect calls before lookup/connection, clean source/main, absent dotenv, all ten final receipts and their fingerprints. This is an additional local harness check, **not an independent code review**.

The Python guard permits loopback for Windows asyncio plumbing. It is **not an OS sandbox** and cannot guarantee containment of arbitrary native children. Node checks ran in a separate process scope outside that guard. No live-provider or live-bot validation is claimed.

Raw logs, JUnit, runner scripts, boundary checks and all unsuccessful iterations remain outside the repository at:
`C:/Users/alphi/agency-evidence/autonomy-verification-a2-20261016/resume-20261016/`.

Cross-store finalization is not atomic: earlier unverified evidence or a memory write can survive a later failure. A2 introduces no durable task recovery, uncertain-effect reconciliation, peer-signed receipt, independent accuracy oracle, authorization quorum or autonomy lifecycle. Zero-confidence synthetic findings can dampen heuristic risk categories; the displayed category is explicitly not a safety clearance.

## Remaining gates

Independent code/security review, CI and the Python/OS matrix, live-provider validation, peer governance/authority, durable lifecycle/recovery, merge and deployment remain separate and **unperformed**. Keep the branch local and unmerged until the owner chooses the next gate. Do not restart the live bot or treat local test totals as a production/autonomy pass.
