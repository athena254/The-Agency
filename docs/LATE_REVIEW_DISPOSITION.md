# Late read-only review disposition

The three read-only assessments were dispatched against `7bc5987` before consolidation and arrived after the completion report at `340c8a0`. Their original line numbers and statuses are snapshots, not the current checkout. Findings were compared with current source; no claim of an independent full security audit is made.

## Superseded by implemented work

- Telegram thread transport absent: closed by `e1efe35`/`679b994`, with private nonbeta commands, owner isolation and restart tests. Beta/HTTP thread ingress is not enabled.
- Recovery has only one happy-path test and completed resume bypasses actor/input checks: closed within the explicit local API scope by `8273cbd`, with real child-kill/lock/reconcile/resume and binding/permission/persistence tests. W-05 still tracks subprocess stability/diagnostics; orchestration recovery and exactly-once remain unimplemented.
- Metering/recovery still labeled unmerged WIP: corrected by consolidation and current-state documentation, with historical checkpoint labels retained as history.

## New reproduced defect — strict-driver model bypass

Current-source review confirmed that ToolDriver required metering for real adapters but allowed arbitrary other model objects to dispatch in a strict/beta context. A scripted object could return a final response without a request-bound MeteredModel, quota reservation or audit. A MeteredModel subclass also bypassed the exact-type path. This was an internal seam violation; beta public model/tool ingress remained disabled, so this is not evidence of a remotely exploited beta endpoint.

Four real regressions covered scripted/subclass objects with and without a concrete temporary audit file. All four reproduced `completed` instead of fail-closed denial before the fix. Commit `28c9d09` now requires an exact concrete MeteredModel plus the matching typed private principal and same concrete BetaAuditLog before listing tools or dispatching. All four deny with zero provider/tool calls after the fix.

Three old tests depended on dispatching scripted model doubles through the strict seam. They were not weakened or removed: their setup now uses real temporary budget/audit stores and the concrete metered wrapper, with fake responses below the adapter seam. Their original audit-failure, private-provider-error and registry-policy-denial assertions remain. The full integrated run exposed the third stale setup; `59e9bc9` corrects it and adds an explicit single-model-attempt assertion. Expanded focused metering/driver/uncertainty/policy/admission check: **104 passed**. Fresh full verification at `59e9bc9`: **1297 passed, 23 warnings**. Ruff lint/format, strict mypy, 14 UI tests and the repinned launcher preflight pass; exact evidence is in `research-evidence/late-review-verification.json`. The earlier 1293-test checkpoint remains preserved as historical evidence, not current source proof.

## Open source-backed product gaps

- **R-01 — Context propagation:** `orchestrator.py:_system_context` does not include `memory_context`; only the tool-capable general/research path appends it. Classic routed domains therefore do not receive the same conversation context. Thread storage/private command tests do not establish all-domain continuity. Closure belongs to P4, with per-domain captured-provider prompt regressions and owner scope checks.
- **R-02 — Specialist/DAG execution:** classic execution loops planner subtasks with the same whole-task `system_context` and originating agent ID, without using subtask-specific work, assigned specialist or dependency outputs. AgentLoop is not a proven delegated runtime. Closure belongs to P2/P4, after peer authority/contracts are frozen.
- **R-03 — Verification semantics:** tool-loop records hardcode `verification='passed'`; final task completion is based on execution statuses, not a blocking independent verification decision. A successful tool loop is not evidence-correctness or security approval. Define a deterministic evidence/QA contract before treating verification as an authority gate.
- **R-04 — Legacy tool memory isolation:** `MemoryQueryTool.run` accepts argument-supplied `agent_id` or unscoped text search and does not bind the resource scope to the calling principal/thread. Its FTS-fallback log includes the query and raw store errors may be returned. Thread-context isolation is not an end-to-end memory-tool guarantee. Keep beta memory tools disabled; bind the legacy tool boundary or gate it before sharing the nonbeta runtime with other users. Closure belongs to P1/P3 with real SQLite cross-owner denial and log-redaction regressions.
- **R-05 — Lifecycle/durability:** task state and ordinary audit/evidence defaults are ephemeral; memory periodic aging is not started by orchestrator startup. These are specific remaining P0/P3/P4 lifecycle tasks, not proof that persistence modules are absent.
- **R-06 — Capability governance:** Factory authorization/publication callbacks use local truthiness; SkillRegistry publication validates evidence metadata without an authenticated approving actor/evaluation result. These are bounded metadata APIs, not peer grants or autonomous evaluated capability release. Approval semantics must be tightened under P2/P4, not silently promoted to deployed authority.

Still open as already documented: true peer transport/membership/replay/application grants, affected-consumer/human attestation, actuator checks, all-entrypoint model budgets/audit, connected-address fetch protection, hostile-code isolation, Factory restart/evaluation, integrated Forge, authenticated multiuser UI and production operations.

Current code evidence for R-01–R-04 was re-read at `28c9d09`; the orchestrator/memory-tool modules are unchanged from the reviewed source. Open source-backed gaps are not represented as new runtime test results. See `PRODUCTION_READINESS_PLAN.md`, `CURRENT_STATE.md` and `KNOWN_ISSUES.md` for release boundaries.
