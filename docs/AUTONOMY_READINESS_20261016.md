# Autonomy readiness — October 16, 2026

**Verdict: NOT READY for full unattended operations.** No completion percentage or delivery date is asserted. Local tests are not a peer-authority, recovery, real-model or production acceptance run.

## What is actually implemented

- A1 context repair: retained on this development branch; scoped recalled context reaches routed execution without granting authority, and isolated chat excludes it.
- A2 truthful execution/check reports: implemented at `f710a3f835e4dc033d2845994e31392c177fffd7`; the documentation checkpoint `d9377606de25e7dadbcf85d51480026022068085` has the same source/tests. Completed execution is no longer presented as independent verification.
- Existing beta ingress, request budgets, concrete metered-model/audit seams and replay-safe workflow foundations remain useful **parts**, not an integrated autonomous task lifecycle.
- A3 investigation reproduced gateway audit failure being ignored, unpinned endpoint/token limits and a non-authoritative driver counter. See `RESEARCH_AUTONOMY_ADMISSION_A3_20261016.md`.
- Fresh A3-worktree baseline: **1,794 passed; zero failures/errors/skips**. One Starlette/httpx deprecation warning remains. This exercised unchanged A2 code, not an A3 implementation. Other A2 gates are historical qualification, not newly rerun A3 results.
- A3.1 required-gateway-audit contract is written but **NOT implemented or approved** at this checkpoint.

## What “can run on its own” must mean

The first honest milestone is the already proposed `public-fixture-summary/v1`: a permitted task, immutable approved sample inputs, actual model-directed choices, bounded tools and a sole task-owned output, separate checker identity, policy-valid permission, durable progress/result and honest handling of interruption. That is bounded autonomy for one class, **not unrestricted full operations**. No private directories, shell, money movement, account changes, self-modification or general execution authority is implied.

The first class remains a proposal until resource/provider/limit and authority decisions are approved. No standing peer/resource grant is created by this checklist or by the request to keep working.

## Dependency gates

- A3.1: required gateway audit acknowledgement and propagation. Frozen next repair: `SPEC_AUTONOMY_ADMISSION_A3_1.md`; awaiting implementation approval.
- Remaining A3: authenticated admission and pinned dispatch identity; durable shared model-attempt accounting across routing/work/checking/failures/final turns; no unmetered alternate consumer; fail-closed finalization. Platform-neutral compatibility and D-05 still needed.
- A4: separate-process identities/stores, membership, authenticated transport, affected-input completeness, approved Byzantine-tolerant policy, independently verifiable human input and actuator grants. No centrally fabricated peer votes.
- A5: task/assignment journal, atomic local dedup/result/outbox, safe recovery, target-side fencing and uncertain-effect reconciliation. Do not replay unknown effects or claim external exactly-once behavior.
- A6: complete actual fixture task, authorized tools/output, independent checker and checked-byte publication. Candidate staging/publication are effects, not “read-only”.
- A7: process-kill/restart/partition/adversarial tests, supported-platform CI, independent security review, an explicitly authorized bounded real-model demonstration and human artifact review.
- Rollout: separate owner authorization, verified deployment interpreter/config/data, backup/rollback and safe canary. Main/live runtime remain untouched during this work.

## All milestone acceptance scenarios remain accounted for

These are the AC-01–AC-24 requirements from `SPEC_BOUNDED_AUTONOMY_V1.md`, not twenty-four new features. **None is signed off as the complete autonomy acceptance scenario.**

- AC-01 useful goal: OPEN — no integrated independently authorized fixture-to-report task.
- AC-02 dynamic choice: OPEN — synthetic scripted/echo tests are not actual-model-directed autonomy.
- AC-03 context: PARTIAL — A1/A2 local propagation/isolation evidence exists; full admitted multi-peer path unrun.
- AC-04 distinct assignments/progress: OPEN — no durable multi-step autonomous assignment integration.
- AC-05 useful checks/checked publication: OPEN — A2 reports checks honestly; no independent fixture oracle/publisher integration.
- AC-06 honest status: PARTIAL — A2 locally qualified; gateway-audit reproduction and future lifecycle leave the full criterion open.
- AC-07 interruption: OPEN — no actual worker kill/recovery matrix across task boundaries.
- AC-08 dedup/replay: PARTIAL — existing beta/local workflow primitives; no integrated task/result/effect/delivery dedup.
- AC-09 stale workers: OPEN — no approved target-side stale-generation publication enforcement.
- AC-10 audit/storage: FAILING BASELINE — synthetic gateway audit failure still returned completed. A3.1 proposes a bounded repair, not full cross-store closure.
- AC-11 budget/provider identity: FAILING/INCOMPLETE — endpoint/limits drift reproduced; routing/SDK/bridge/checker paths not all request-metered.
- AC-12 resource scope: OPEN — no approved fixture/output resource enforcement and path-race acceptance run.
- AC-13 independent peers/authentication: OPEN — engine/envelope experiments are not the integrated peer-authorization scenario.
- AC-14 affected-input/quorum/grants: OPEN — selected policy requirements exist, operative application policy still unapproved/unintegrated.
- AC-15 Butler offline: OPEN — no durable admitted autonomous task/result path independent of gateway uptime.
- AC-16 coordinator loss/partition: OPEN — no approved fault-assumption convergence/authority test.
- AC-17 cancellation/deadline: PARTIAL — existing local cancellation tests; durable in-flight uncertainty and task-wide enforcement unrun.
- AC-18 isolation/privacy: PARTIAL — existing scope/isolation tests; legacy gateway audit/log body handling and future peer/export boundaries need repair/review.
- AC-19 real model: OPEN — no owner-authorized actual-model autonomy demonstration; no provider fallback is inferred.
- AC-20 delivery: OPEN — no durable result/outbox/transport uncertainty demonstration.
- AC-21 restore: OPEN — no signing/replay/budget/policy rollback-resilience acceptance test.
- AC-22 quality: PARTIAL — A2 local gates and fresh unchanged-source 1,794-test baseline; independent review and CI/OS/Python matrix pending.
- AC-23 operations: OPEN — no rollout authorization/canary qualification; deployment is separate from coding.
- AC-24 evidence: PARTIAL — local raw receipts and source hashes retained; the complete future milestone evidence set does not exist.

## Owner decisions, in the order they become blockers

**Now:** approve or amend the frozen A3.1 offline repair contract. Direct coding was authorized for A2; later component contracts still require the documentation-first checkpoint/approval process. Cline sign-in is not the blocker for assistant-written code.

**Before a real A3 admission/provider integration:** D-05 — exact permitted fixture/output resources, limits, provider/model/endpoint, data-disclosure/cost policy and evidence retention. The milestone's numeric ceilings are proposed lab values, not silently adopted production defaults. Building with a subscription-backed assistant is distinct from authorizing a runtime provider or paid fallback.

**Before A4 grants/actuation:** D-01–D-04 — validator/fault configuration, consensus adoption/application approval, membership/revocation, authenticated affected set and attributable input/override, independently verifiable human consent/recovery and precise resource grants/fencing. Recommendations can be researched; no final authority is fabricated to avoid asking.

**Before release:** independent reviewer and rollout authorization. No-delegation is respected; assistant self-review is not relabeled as independent.

The readiness claim changes only after the exact end-to-end scenario and required failure cases run successfully on the intended revision and the missing authority/release gates are actually closed. A green unit suite or a working chat reply is insufficient.
