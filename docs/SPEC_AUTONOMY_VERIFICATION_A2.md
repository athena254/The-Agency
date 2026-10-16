# A2 — truthful observable execution assessments

Date: October 16, 2026.
Status: OWNER APPROVED at checkpoint b5de513; resumed for direct implementation by the current assistant on October 16, 2026. The owner's newer instruction is: 'Do not delegate do the coding yourself or use your own luna and terra cheaper models where possible'. This supersedes the Cline/free-worker prerequisite, not the technical scope or offline qualification gates. No delegated or auxiliary model workers will be used in this continuation. HANDOFF_AUTONOMY_VERIFICATION_A2_20261016.md records the historical blocker, not a current requirement to authenticate Cline.
Base: `c958b874e24938e2a3713963d4d5aa73f21a97ba` (A1).
Worktree: `C:/Users/alphi/agency-worktrees/autonomy-verification-a2-20261016/`.
Branch: `docs/autonomy-verification-a2-20261016`.
Research: `RESEARCH_AUTONOMY_VERIFICATION_A2_20261016.md`.

## 1. Plain-English purpose and boundaries

Tell callers what actually ran and what was checked. Finishing a model/tool loop is not proof its answer is correct, safe, independently verified or authorized. A2 adds truthful reporting and repairs the reproduced zero-work completion defect; it does not implement the complete bounded-autonomy task lifecycle.

Preserve A1 quoted recall and all existing beta denials, prototype request guards and isolated tool/memory/thread/Lattice exclusion. No new tools, model calls, verifier models, providers, filesystem authority, HTTP routes, orchestration powers, peer policy, ledger migrations or deployment. Butler stays a human gateway, not an autonomous governor. Existing uncontrolled legacy execution is not relabeled authorized by this work.

The owner requested research, then plan and execution with milestone reports. The approved implementation checkpoint remains explicit. The current assistant now implements and reviews directly under the newer no-delegation instruction; no free-worker smoke or Cline login is required for direct coding. No paid fallback or auxiliary model is selected. Main and any live bot remain untouched. No push, merge or rollout is included.

This A2 is the R-03 assessment slice in `SPEC_BOUNDED_AUTONOMY_V1.md:189`, not the differently numbered durable-runtime A2 in another program.

## 2. Findings and design decisions

The retained baseline proves private hardcoded passed/none labels, dropped public metadata, heuristic promotion of a wrong answer to VERIFIED, missing accuracy and empty-plan completion-before-validation. See research for exact source map, fresh diagnostic and containment limits.

Choose an additive immutable runtime-generated report on the existing `ExecutionResult`. Keep execution status separate from assessment status. Do not replace the standalone `AgentVerifier` API or claim its heuristic verdict establishes factual accuracy. Report its actual completeness and safety checks with their narrow methods; accuracy remains unperformed without a trusted oracle, which A2 does not introduce. The classic verifier receives the exact rendered text, not a model-supplied ground-truth dictionary.

A2 emits no independently verified output. Schema validation and a digest bind a report to observed bytes but are not signatures, authority, trusted provenance across an attacker-controlled process, or a durable acceptance receipt. No new trust is conferred by constructing a Python report object.

## 3. Output rendering and artifact binding

For reports, define `render_output(value)` as: None → empty string; string → unchanged; every other supported legacy output → `str(value)`. Do not extract a model-supplied assessment or ground_truth. The hash is lowercase SHA-256 over that exact string's strict UTF-8 bytes, without trimming, normalization, prefixes, footers or JSON reparsing. Invalid UTF-8 encoding is a failed result path, not an invented digest.

A per-subtask digest binds its rendered output. A top-level digest binds final output after the existing nonempty subtask outputs are joined by two newline characters. Human runtime notices are outside the hashed model body and labeled separately. Empty/whitespace-only output cannot satisfy legacy execution completion. Preserve other output bytes verbatim.

A consumer must not display a positive runtime assessment if task ID, execution status or output digest does not match the result it is presenting. A missing/mismatched report is unavailable, never an inferred pass. A model-written success label remains model text and cannot replace the runtime report.

## 4. Exact additive records

New module: `src/agency/agents/assessment.py`. All report records use Pydantic frozen models, extra=forbid, nested frozen models and tuples (not mutable dict/list references). Strings and enum tokens are preserved literally; unknown versions/methods/statuses/fields are rejected. Bools cannot masquerade as numeric scores; scores must be finite and within 0..1. Nested collections serialize as JSON arrays and round-trip through JSON validation.

### 4.1 AssessmentStatus

String values: `not_performed`, `passed`, `failed`, `needs_review`.
These describe only the explicitly named checks, not authorization or evidence-ladder advancement.

### 4.2 CheckAssessment

Required fields:
- `criterion`: exact `completeness`, `accuracy` or `safety`.
- `status`: AssessmentStatus.
- `method`: exact `not_performed`, `legacy_keyword_coverage/v1` or `legacy_denylist/v1`.
- `reason`: exact `tool_path_unchecked`, `isolated_path_unchecked`, `execution_not_completed`, `no_trusted_accuracy_oracle`, `heuristic_passed` or `heuristic_failed`.
- `score`: float in 0..1 or null, with null required for an unperformed check.

Only completeness uses keyword coverage; only safety uses denylist. Performed heuristic checks use passed/failed and matching reason/score. Accuracy always uses not_performed, not_performed method, no_trusted_accuracy_oracle (or path/execution omission reason) and null score. No details field may carry model text, prompts, exception bodies, recalled context or source paths. No synthesized accuracy score.

### 4.3 RiskAssessment

Required fields:
- `status`: exact `not_assessed` or `assessed`.
- `method`: exact `not_performed` or `legacy_risk_engine/v1`.
- `category`: existing RiskCategory string (critical/high/medium/low/negligible) or null.
- `finding_id`: string or null.

not_assessed requires not_performed and both nullable fields null. assessed requires legacy_risk_engine/v1, the real engine-returned category and the finding ID actually assessed. Never use `none` as a risk category. This is a heuristic category over a synthetic execution Finding, not calibrated task safety, authorization or an independent assessment. This report deliberately omits the risk vector and user-supplied asset names; it must not invent or summarize them as measured zeroes.

### 4.4 SubtaskAssessment

Required fields:
- `subtask_id`: nonempty ID from the executed classic subtask, fixed `tool_loop` for tools or fixed `isolated` for the isolated turn.
- `path`: exact `classic`, `tool` or `isolated`.
- `execution_status`: existing execution-status string; only completed/failed/cancelled/timeout are terminal report values.
- `output_sha256`: exact lowercase 64-hex digest.
- `verification`: AssessmentStatus derived under section 5.
- `checks`: tuple containing each criterion exactly once in order completeness, accuracy, safety.
- `risk`: RiskAssessment.

The schema rejects duplicate/missing criteria and contradictory aggregate statuses. Tools/isolated/failed executions cannot contain positive performed checks or assessed risk in A2. Report actual status rather than calling failed work completed. Classic completed executions can contain only the performed heuristic checks and missing accuracy described above.

### 4.5 ExecutionAssessment

Required fields:
- `schema_version`: literal `a2.v1`.
- `task_id`: exact result task ID.
- `execution_status`: terminal existing status string.
- `output_sha256`: exact lowercase 64-hex final-output digest.
- `verification`: AssessmentStatus derived from all subtask checks.
- `risk_status`: exact not_assessed/assessed/partially_assessed; section 5 defines it.
- `independently_verified`: literal false; true is invalid.
- `subtasks`: immutable tuple of SubtaskAssessment; empty only for a failed zero-work result.

Reject contradictory summaries and a completed empty-subtask record. Aggregate completion may not conceal a noncompleted subtask. This is a local diagnostic snapshot, not a durable or peer-signed CheckReport from the full milestone contract.

### 4.6 Existing record additions

`ExecutionResult.assessment: ExecutionAssessment | None = None` — additive compatibility for standalone AgentExecutor and old internal test doubles. Orchestrated and isolated production paths populate it. `ExecutionResult.attempts` permits zero to represent the reproduced zero-work failure truthfully; default remains one. At the orchestrator level this historical field counts executed units, not total provider attempts; no new metering claim.

`ButlerReply.assessment: ExecutionAssessment | None = None` — additive frozen dataclass field for the isolated path. `ButlerReply.task_id: str | None = None` also carries the actual result ID so the prototype can recheck binding without taking the expected ID from the report itself; legacy doubles without an ID remain unavailable. This additive provenance field closes an inspection-discovered consumer seam, without changing inbound requests or granting authority. Text-only Butler methods retain their public string return type.

`ChatResponse.assessment: ExecutionAssessment | None = None` — additive outbound field. ChatRequest stays message-only, extra=forbid. Report absence stays null; never turn it into a passed assessment. Production returns the real report. Existing test doubles can return no report and must display an unavailable notice.

## 5. Aggregation and execution matrix

Check aggregation over the named criteria:
1. Any failed check → failed.
2. Empty set or all not_performed → not_performed.
3. Any needs_review or any missing/unperformed criterion alongside a performed check → needs_review.
4. Otherwise all actual declared checks passed → passed. A2's no-oracle contract makes a fully passed aggregate unreachable in production.

Apply the same ordering over all subtask checks for the final report. Do not average away a failure or use vacuous all(empty) as a pass. Each subtask report still shows its own checks.

Risk aggregation: no assessed subtasks, including no subtasks → not_assessed; every subtask assessed and nonempty → assessed; otherwise → partially_assessed. There is no aggregate risk category; each actual assessment retains its category and provenance.

Runtime behavior:
- General/research tool loop completed with nonblank final answer: execution completed; each check not_performed/tool_path_unchecked; risk not_assessed; no extra verifier/risk/model calls.
- Tool loop noncompleted: aggregate execution failed (existing mapping retained); each check not_performed/execution_not_completed; no risk assessment; no success execution memory. Preserve actual loop failure in bounded reporting/audit without leaking provider exception text.
- Classic completed nonblank output: run existing legacy completeness/safety verification on rendered text; accuracy not_performed/no_trusted_accuracy_oracle. Any heuristic failure makes checks failed; otherwise needs_review. Execution remains completed independently of heuristic outcome. This is legacy output delivery, not acceptance-certified autonomous completion.
- Classic noncompleted or empty output: top-level execution failed (or preserve isolated executor timeout/cancelled); no verification/risk/finding or execution-memory success work. Do not run checks against string 'None'.
- Empty classic plan: return failed with attempts=0, output='', error='empty_plan', empty-subtask assessment not_performed/not_assessed; TaskStatus.FAILED; zero model/tool/verifier/risk/evidence/memory calls. Existing planner may run; no invented execution.
- Isolated turn: actual executor status preserved unless completed output is blank, which becomes failed; report all checks not_performed/isolated_path_unchecked or execution_not_completed; risk not_assessed. No tools, memory, evidence, threads or Lattice task work added.
- Mixed classic work: any noncompleted/empty unit makes aggregate execution failed, failed checks remain visible, successful-unit findings remain unverified. No success execution memory for failed aggregate.

Verifier/risk/evidence/audit/memory exceptions may not produce a returned success or a fabricated assessment. Construct and validate the report/result before execution-memory or terminal completion writes; leave the task RUNNING until those required operations finish. TaskManager forbids COMPLETED→FAILED, so moving the completed-state write after result construction and memory is required rather than trying to roll back a terminal status. Best-effort mark a still-running task failed on Exception or cancellation, propagate the original failure/cancellation to existing consumer handling, and do not log exception bodies in new code. If prior unverified findings were written, retain them. A2 does not add cross-store transactions, uncertain-effect reconciliation, durable task recovery or a complete metering/audit admission boundary; those remain A3/A5.

## 6. Evidence and memory truthfulness

All newly generated classic agent_execution findings stay VerificationState.UNVERIFIED, regardless of legacy heuristic score or output claims. Keep the existing methodology label; set finding.confidence=0.0 to mean no factual confidence has been established. Actual keyword/denylist scores belong only in the per-check snapshots. This intentionally changes the downstream heuristic risk estimate: the engine dampens low-confidence inputs, which must not be represented as evidence of low operational risk. Any displayed category is explicitly a synthetic-finding heuristic, not a safety clearance.

No promotion to evidence level 5, no verified timestamps/citations invented, no backfill/rewrite of historical evidence. No claims that legacy risk estimates are independently validated.

Store execution memory only for completed nonempty aggregate execution. Include execution status and check/risk status in the task execution summary; do not store an unchecked answer as verified success. Existing Butler conversation memory is a conversation transcript, not a success/QA receipt; keep owner scope, thread behavior and A1 recall unchanged.

## 7. Consumer behavior and public observability

Provide pure helpers in assessment.py for output rendering/hash, immutable report building/aggregation, binding checks and concise deterministic runtime summary. Helpers must not call models, tools, stores or network. The module must not import executor/orchestrator at runtime and create a cold-import cycle.

Frozen helper surface (keyword-only parameters except the first value):
- `render_output(value: Any) -> str` and `output_digest(value: Any) -> str`.
- `unchecked_subtask(subtask_id: str, *, path: Literal["classic", "tool", "isolated"], execution_status: str, output: Any) -> SubtaskAssessment`.
- `classic_subtask(subtask_id: str, *, execution_status: str, output: Any, verification: VerificationResult, category: RiskCategory, finding_id: str) -> SubtaskAssessment`; only called for real completed/nonempty classic output and completed actual verifier/risk operations.
- `build_assessment(task_id: str, *, execution_status: str, output: Any, subtasks: tuple[SubtaskAssessment, ...]) -> ExecutionAssessment`.
- `bound_assessment(assessment: ExecutionAssessment | None, *, task_id: str, execution_status: str, output: Any) -> ExecutionAssessment | None`; validates the exact binding, returning None for missing/mismatched reports rather than inferring a pass.
- `assessment_notice(assessment: ExecutionAssessment | None) -> str`; deterministic bounded plain text, no raw metadata dump.
- `assessed_output(output: Any, *, assessment: ExecutionAssessment | None, task_id: str, execution_status: str) -> str`; rendered body plus notice after binding validation. Missing old-test-double task_id can be represented only as an unavailable binding, never substituted from the assessment.

Private helpers may enforce record/aggregation invariants without widening this public surface. Callers supplying dicts/model-written fields are not accepted report authorities.

`ButlerService.execute`: keep its string API. After actual completed execution, append a separately labeled plain-text runtime notice to model output. Typical tool notice: 'Runtime assessment: execution completed; checks not performed; risk not assessed. No independent verification or authorization.' Classic notice explicitly says heuristic checks only and accuracy not checked. Existing failure status still raises ButlerExecutionError before a successful reply.

`ButlerService.handle_isolated_message`: carry bound assessment into ButlerReply without granting tools/memory. Its returned text remains the model body; the outbound prototype API carries structured report separately. Failure stays existing sanitized status handling, not a 200.

`TelegramHandler._handle_research`: change success wording from 'Research complete' to execution-oriented wording and append the same deterministic runtime notice. Retain real noncompleted/timeout handling. Model content cannot inject or replace assessment fields. Never reuse Telegram delivery status for QA status: a completed execution with failed heuristic checks still produces one delivered reply, not polling retries or duplicate execution. This notice becomes part of routed conversation history by design, but it must not become an authorization grant when recalled.

Prototype `/api/chat`: outbound success includes response plus nullable assessment. Preserve status mapping and request guards. The browser shows a separate runtime-assessment line using textContent or equivalent safe text rendering, never model HTML or model-parsed metadata. Display 'unavailable' for absent/malformed report, not a green pass. Keep the model body separate from the status line. Do not expose raw finding evidence, prompts, recall, exception text or unrestricted filesystem paths.

Other text-only adapters that already use ButlerService.handle_message inherit its notice without a public return-type migration. Deterministic commands, DemoAgent, bridge execution and workflow results are not orchestrator ExecutionResult assessments; do not falsely wrap them as A2-reviewed.

## 8. File ownership and non-goals

One integration writer owns existing:
- src/agency/agents/executor.py (additive field and zero-work attempts only).
- src/agency/orchestrator.py (replace unreturned report theatre with real snapshots, failure and zero-work behavior).
- src/agency/butler/service.py (report delivery; preserve all existing boundaries).
- src/agency/telegram/handler.py (research presentation only).
- src/agency/prototype/app.py and its existing static JS/CSS assets if needed for a separate runtime notice.

New runtime file: src/agency/agents/assessment.py.
New focused tests: tests/test_autonomy_verification_assessment.py and tests/test_autonomy_verification_consumers.py. Existing affected tests may be updated only where response schema/text contract deliberately changes; never weaken denial/isolation assertions. Exact UI files are src/agency/prototype/static/app.js and tests/prototype_ui.test.mjs; no CSS/HTML change is needed. Deliberate compatibility-test updates include tests/test_beta_failure_integrity.py (completed text controls), tests/test_beta_principal_hop.py (the completed context-identity control) and tests/test_prototype_app.py (the exact success JSON shape); their failure/denial assertions remain intact. Existing research tests may expect the new execution-oriented heading, with conservative fallback for old status-only doubles.

Documentation/evidence: this spec, research, final qualification report and selected sanitized receipts under docs/research-evidence/. Full raw evidence stays outside the repository at the named evidence root.

No planner dispatch/dependency repair, new standalone verifier semantics, registry/model-policy bypasses, new storage/transport/authentication system, runtime DB changes, profile configuration, npm dependency refresh, paid model selection, remote publication, merge or live restart. Stop and propose a separate spec for those.

## 9. Acceptance and TDD tracer bullets

A2-T01: real general and research execution expose immutable not_performed/not_assessed reports while spies prove zero verifier/risk calls; failed tool path never has passed/none labels.
A2-T02: real classic wrong fixture gets visible heuristic completeness/safety results, missing accuracy, needs_review or failed aggregate, and only UNVERIFIED findings. Caller/model ground_truth and fake assessment cannot establish accuracy or independence.
A2-T03: exact UTF-8 binding, multiline/Unicode/dict/None behavior, multi-subtask final digest and consumer downgrade on changed body/task/status; no trimming/normalizing signed-looking identifiers.
A2-T04: schema JSON round-trip, unknown fields/version/method/status, malformed digests, missing/duplicate criteria, contradictory aggregates, nonfinite/bool scores and attempted mutation of nested records/tuples rejected.
A2-T05: classic provider failure/timeout/blank output, tool failure and mixed subtask failure preserve actual execution failure and never manufacture a successful checker/risk/finding/memory result.
A2-T06: empty plan returns valid failed result, attempts=0 and failed TaskStatus; assert zero model/tool/verifier/risk/evidence/memory operations.
A2-T07: aggregate failed/needs_review/not_performed precedence and mixed risk coverage; cannot drop a failed/missing check or treat empty tuples as passed.
A2-T08: Butler user-visible notice, Telegram research notice and prototype structured outbound assessment reflect runtime checks; malicious model text and inbound assessment remain untrusted. Prototype extra assessment still 422 before provider work.
A2-T09: isolated API/browser stays tool/memory/thread/evidence/Lattice-task-free; missing metadata is unavailable; real failed/timeout turns retain non-2xx mapping, safe text rendering and no raw error disclosure.
A2-T10: A1 recall (classic/tool allowed, isolated excluded), beta denial before all side effects, strict metering/import regressions, cancellation behavior and existing owner/thread isolation pass unchanged.
A2-T11: injected verifier/risk/evidence/audit/memory failures cannot return success; prior unverified evidence stays unverified, no fabricated audit/independent receipt. Record cross-store atomicity limitations honestly.
A2-T12: no main/source runtime-data changes, no bot/provider operations, no unsupported authority/autonomy claim; final logs/JUnit and source hashes tie qualification to exact tested revision.

Use vertical RED→GREEN slices: first expose tool report, then classic truthfulness, then zero-work/failure behavior, then immutable validation/binding, then consumers. Each new behavior needs a failing test observed before production edits. Retain focused red/green logs and checkpoint commits. The current assistant is the sole writer and performs an explicit read-only review pass after implementation. An independent reviewer is not supplied under the no-delegation instruction and must remain an explicitly unfulfilled gate, not a claimed independent review.

## 10. Qualification and milestone reporting

M1 recovered evidence/diagnostic: completed; research records observations, not an autonomy pass.
M2 spec checkpoint: review source/consumer coverage and open decisions, commit docs only, present exact commit and request owner approval. No code before that approval.
M3 implementation: execute scoped RED/GREEN tracer bullets; report actual completed slices and focused results, not just worker process status. Commit reviewed source/tests only with explicit paths.
M4 qualification: independent code/security review; all tests on exact revision; Ruff check and format check, strict mypy, UI tests, cold imports for agency.tools.driver, agency.llm.metered, agency.telegram.handler, telegram_bot and assessment. Full pytest with explicit echo provider and Python external-network guard, separate scratch CWD/data/logs/basetemp and no `.env` in the tested source checkout. Python guard is not native-child or OS isolation; UI Node tests are a separate containment scope.

Use discovered Python 3.11 interpreter read-only; PYTHONPATH and test paths must point to this isolated checkout. Preserve failed runs, start/completion timestamps, commands, JUnit totals, source/test fingerprints and exact changed-file lists outside cache. A source/test change during qualification invalidates that exact-revision claim. A later docs-only commit may reference earlier code qualification only after tree-equality proof.

Final report distinguishes local offline qualification and self-review from unperformed independent review, unrun CI/platform matrix, live-provider validation, independent peer authorization, durable lifecycle and deployment. None is inferred from unit-test counts. Main remains clean/unchanged; no live bot action. Stop at any missing approval/tooling prerequisite or unresolved security/regression failure and report the blocker plainly. The historical free-worker prerequisite no longer applies because the owner explicitly directed direct coding.
