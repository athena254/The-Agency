# A2 truthful assessment research — October 16, 2026

Status: recovered investigation and fresh baseline; no A2 runtime implementation in this research checkpoint.
Scope identifier: bounded-autonomy A2 / R-03 from `SPEC_BOUNDED_AUTONOMY_V1.md`, not the differently numbered runtime-store work in `SPEC_FREE_ONLY_DECENTRALIZED_PROGRAM.md`.

## 1. Recovery, provenance and scope

Owner request: resume research from the stopped investigation, make a plan, execute it, and give milestone reports. Preserve the standing research → specification → committed checkpoint → approval → implementation workflow. This is not permission to merge, publish remotely, enable beta, select peer authority policy, use a live Agency model, change provider configuration or restart/deploy a bot.

Recovered evidence directory: `C:/Users/alphi/agency-evidence/autonomy-verification-a2-20261016/`.
New evidence directory: its `resume-20261016/` subdirectory. The original evidence was not overwritten.

A manifest hashes all 20 original top-level files, including both failed and successful diagnostic attempts, source snapshots, source receipts, citation ledger and source map. Original source receipts record three successful primary-document fetches on October 16. The same primary documents were rediscovered in fresh web searches during this resumed pass; full-page evidence remains in the retained source snapshots. Search excerpts are not a new full-page fetch, and web research is not a live Agency provider test.

Git recheck:
- Isolated worktree: `C:/Users/alphi/agency-worktrees/autonomy-verification-a2-20261016/`.
- Branch: `docs/autonomy-verification-a2-20261016`.
- Base: `c958b874e24938e2a3713963d4d5aa73f21a97ba` (qualified A1 documentation checkpoint).
- Base src tree: `13ba5624a85ed91591a306e84eec337af09556db`.
- Base tests tree: `2cd9d492728377e5197cf9de89e157b940075657`.
- Canonical main: clean at `713a4e311b1e071fbd6c5cf103b995d17aa7e5a2` on discovery. Main is not the A1 branch; do not silently rebase A2 onto it or claim A1 is already integrated there.
- Qualification interpreter discovered: canonical `.venv/Scripts/python.exe`, Python 3.11.16, Pydantic 2.13.5, pytest 9.1.1. Use it as a read-only toolchain with isolated source/data/CWD, not as permission to test against canonical runtime data.

## 2. Fresh diagnostic and limits

The copied diagnostic was run unchanged except for the new evidence and scratch directories. Receipt: `resume-20261016/baseline-run.json`; observations: `baseline.json`; full log: `baseline.log`. Started 15:25:46 UTC; completed 15:25:52 UTC, October 16. Exit 0. Source/tests fingerprint before and after:
`f65db62f1d628c3a085b38bc1d8e39d3503b0c6e4f82905ca65d8d5cae9cbaa9`.

Synthetic fixture only. Six synthetic model calls; zero live-provider calls and zero tool actions. Echo provider explicitly selected, secret-bearing environment variables removed, no `.env` in the isolated source checkout, temporary task-owned stores, loopback-only Python DNS/socket guard. The guard permits Windows asyncio loopback plumbing. It is not an OS sandbox and does not constrain arbitrary native subprocesses. No bot was stopped, started or contacted.

Reproduced gaps:
- G1: `orchestrator.py:608-617` labels both general and research tool paths `verification=passed` and `risk_category=none`, including a failed model/tool-loop result, despite zero verifier/risk calls.
- G2: `ExecutionResult` (`agents/executor.py:66-77`) has no assessment field. The local `subtask_results` data is discarded at the public return. Changing only those local labels is insufficient.
- G3: classic `execute_task` invokes `AgentVerifier` and `RiskEngine`, but keyword coverage/denylist checks do not establish factual correctness. The fixture's wrong answer passes; requested accuracy is absent from `VerificationResult.checks`.
- G4: classic `orchestrator.py:641-653` converts that heuristic verdict into `Finding.verification_status=VERIFIED`. This is unjustified evidence promotion.
- G5: an empty classic plan hits vacuous `all([])`, marks the task completed, writes execution memory, then raises `ValidationError` because `attempts=0` violates `ExecutionResult`. Zero execution is not successful completion. The probe injects an empty graph at the orchestration boundary; the ordinary planner has a fallback, so this is not evidence that normal planning naturally produces empty graphs.
- G6: `ButlerService.execute` and Telegram `/research` reduce results to model text. `converse_isolated` goes through `ButlerReply` to prototype `ChatResponse(response=...)`, dropping any future assessment unless explicitly adapted.
- G7: accuracy in `AgentVerifier.verify` is derived from a `ground_truth` key inside the result. That is not a trusted independent oracle when the result comes from a model. A2 must not accept model-supplied assessment or ground-truth fields as runtime truth.

The fresh probe confirmed existing prototype request `extra=forbid` rejects a caller-supplied assessment with 422 and no model calls. Preserve this; never add assessment to an inbound request model.

## 3. Source-grounded design constraints

Pydantic validates types/constraints, not the truth of claims. Its frozen models do not recursively freeze mutable dictionary contents; use immutable nested snapshots and tuples rather than retaining mutable verifier/risk objects.[1]

OpenTelemetry reserves its Ok status for validated successful completion.[2] As a design inference, A2 should avoid positive labels without an actual check; no tracing dependency or standards-compliance claim is proposed.

Anthropic distinguishes an agent's transcript from the actual outcome and describes graders against specified criteria. A model's completion claim alone is not outcome evidence; use explicit per-check provenance and retain missing-check states.[3]

These are design references, not certification that this project meets a standard or that its heuristic checker is independent QA.

## 4. Alternatives and reuse decision

A. Change only private dictionary strings: rejected; callers still cannot inspect what happened.
B. Replace the executor/result API or introduce a new agent framework: rejected for this maintenance slice; unnecessary migration and authority expansion.
C. Treat legacy keyword/denylist passes as verified correctness: rejected; contradicted by the fresh wrong-answer fixture.
D. Run the legacy verifier on every tool answer: deferred; it introduces new behavior, still has no trusted oracle and is not independent checking.
E. Add a conservative optional assessment to the existing result, immutable subtask check/risk snapshots, honest aggregation and explicit consumer presentation: recommended.
F. Introduce independent checker peers, durable acceptance reports, peer signatures and task finalization: required later for integrated bounded autonomy, not falsely completed by A2.

Reuse `ExecutionResult`, existing executor statuses, verifier operations, risk engine, evidence store, Butler and prototype guards. Do not change the standalone verifier's API/semantics in A2; conservatively adapt its actual completeness/safety checks and explicitly mark accuracy unperformed. Pass rendered output text to the legacy checker so a model-provided dictionary cannot supply an oracle. Preserve legacy execution completion semantics separately from checks except for the reproduced zero-work/empty-output defect. New claims remain UNVERIFIED; do not rewrite historical evidence.

## 5. Execution plan and checkpoint gates

M1 — recovered evidence + fresh diagnostic (complete): evidence manifest, clean A1 branch, exact source fingerprints, reproduced defects, no live side effects.
M2 — specification (next checkpoint): freeze additive schema, exact aggregation, rendered-output digest, consumer behavior, error/compatibility rules, ownership and acceptance tests. Commit docs only; request approval before runtime edits.
M3 — isolated implementation: vertical RED→GREEN slices, immutable report first, real orchestration paths next, consumers last. One writer for shared files. Retain failed/passing outputs and commit reviewed slices. Any delegated heavy implementation must use a newly proven eligible free lane; no automatic paid fallback or private-data disclosure.
M4 — independent review + exact-revision qualification: focused and complete offline pytest, Ruff lint/format, strict mypy, prototype/UI checks and cold imports. Preserve warnings, failures and actual containment limits. Source/test trees must match the revision referenced in the report. Commit final evidence/qualification notes; do not imply local tests establish CI, live behavior, peer authority or full autonomy.

Stop on scope drift, dirty/unexpected source, unavailable required toolchain, failed isolation, missing implementation approval, or security/review failures not resolved within this slice. No merge, push, beta enablement, deployment or provider change is included.

## Sources

[1] https://pydantic.dev/docs/validation/latest/concepts/models
[2] https://opentelemetry.io/docs/specs/otel/trace/api
[3] https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
