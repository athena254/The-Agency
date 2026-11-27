# Software-Factory Autonomy Loop — Concrete Design Mapped to The Agency's Forge

Date: 2026-11-27. Scope: read-only synthesis of local notes plus a skim of
`D:/agency-autonomy/src/agency/forge/*.py`. No repository file, `.env`, or
worktree was edited. No secrets appear in this document.

---

## 0. Source files consulted (provenance)

| File | Role in this design |
|---|---|
| `D:/software factory/LEARNINGS.md` | Cross-source principles, recurring loops (PIV, isolate/build/prove/ship), "rules ask / hooks guarantee", evidence-driven testing. |
| `D:/software factory/README.md` | Inventory of collected repos/skills; points to the three design docs below. |
| `D:/software factory/DARK-FACTORY-DESIGN.md` | Full-SDLC design: 5 components, 6 planes / 54 agents, handoff envelope, risk routing, escalation triggers, independence gaps, maintenance control loop, 13 distilled principles. |
| `D:/software factory/dark-factory-agent-spec-v2.md` | Role-to-agent spec, handoff envelope, risk-based routing, escalation, human/physical dependencies. |
| `D:/software factory/sdlc-roles-v3.md` | Automation tiers, mandatory human gates (Appendix B), core loop + build order (Appendix C). |
| `D:/agency-autonomy/gaps.md` §22 | The eight concrete Forge efficiency/autonomy gaps and their "outcome" acceptance criteria; cross-cutting no-secrets/no-auto-merge rule. |
| `D:/agency-autonomy/FACTORY_RULES.md` | Triage, five quality gates, limits (max ~15 parallel jobs, humans tend the harness, secrets from `.env` only, protected paths). |
| `D:/agency-autonomy/src/agency/forge/*.py` | The implementation surface this loop must map onto (module inventory in §3). |

---

## 1. Distilled principles (with source file names)

| # | Principle | Source file(s) |
|---|---|---|
| P1 | A factory is a **loop, not a tool**: intent → spec → build → validate → ship, held by skills + hooks + gates. | `LEARNINGS.md` §1; `sdlc-roles-v3.md` Appendix C. |
| P2 | **Verification is the bottleneck, not generation.** The factory's value is gates, not code output. | `LEARNINGS.md` §2–3; `DARK-FACTORY-DESIGN.md` §5, §12.1. |
| P3 | **Rules ask, hooks guarantee.** If the consequence is an incident, encode a hook/gate, not prose. | `LEARNINGS.md` §3; `DARK-FACTORY-DESIGN.md` §12.2. |
| P4 | **Isolate everything**: one worktree + branch per task; never build on main; `--force-with-lease` only on own branch. | `LEARNINGS.md` §3; `DARK-FACTORY-DESIGN.md` §12.4. |
| P5 | **Prove before ship**: record failure before the fix and success after; evidence-driven testing. | `LEARNINGS.md` §3 (Ras Mic "Prove"); `DARK-FACTORY-DESIGN.md` §12.5. |
| P6 | **Multi-model / independent review**: agreement across model families is the signal; builders never judge their own work. | `LEARNINGS.md` §3; `DARK-FACTORY-DESIGN.md` §8, §12.6, §8d. |
| P7 | **Risk-based routing**: class from paths/data/blast radius; the class decides which checks run (low/medium/high). | `DARK-FACTORY-DESIGN.md` §8b; `dark-factory-agent-spec-v2.md` §5; `gaps.md` §22.1 (model routing). |
| P8 | **Plan up front and treat plans as claims**: required sections + named test command + risks + non-empty allow-list; a plan is not a guarantee. | `LEARNINGS.md` §2 (PIV); `DARK-FACTORY-DESIGN.md` §12.7; `gaps.md` §22.4. |
| P9 | **Subtract before you add**; smallest change that fully solves the task (YAGNI ladder / ponytail). | `LEARNINGS.md` §3, §10; `DARK-FACTORY-DESIGN.md` §12.8. |
| P10 | **Encode lessons in structure** — make the system prevent recurrence, not merely remember it. | `LEARNINGS.md` §3; `DARK-FACTORY-DESIGN.md` §12.9. |
| P11 | **Never block on the human, but don't pretend judgment isn't needed** — design for unattended progress; escalate deliberately. | `LEARNINGS.md` §3; `DARK-FACTORY-DESIGN.md` §12.10, §8c. |
| P12 | **Dim over dark when in doubt** — executable specs plus reading code as needed; defense in depth. | `LEARNINGS.md` §1; `DARK-FACTORY-DESIGN.md` §12.11, §11. |
| P13 | **Mandatory human gates** for irreversible data actions, auth/crypto/payments/permissions, spend/vendors, legal/safety, and edits to the factory's own guardrails. | `sdlc-roles-v3.md` Appendix B; `DARK-FACTORY-DESIGN.md` §8h, §8c. |
| P14 | **Fixed handoff envelope** (task_id, parent_id, from/to, spec_ref, risk_class, artifacts, acceptance, budget, producer, evidence, status) so every handoff is auditable and the next agent can validate it. | `DARK-FACTORY-DESIGN.md` §8a; `dark-factory-agent-spec-v2.md` §2. |
| P15 | **Everything product-specific lives in the adapter; the engine is generic.** | `DARK-FACTORY-DESIGN.md` §12.13, §4; `omniscient/dark-factory` adapter pattern in `LEARNINGS.md` §2. |
| P16 | **Independence gaps need named fixes**: unverified judges get a human-owned gold set/path-based hard rules; critics run on a different model family on high risk. | `DARK-FACTORY-DESIGN.md` §8d; `gaps.md` §22 (quorum on risky). |
| P17 | **No secrets anywhere; workers never push/merge/deploy; commit on own branch; focused tests + Ruff/Mypy.** | `gaps.md` §22 cross-cutting rule; `FACTORY_RULES.md`. |

---

## 2. The concrete closed loop

`SENSE → PROPOSE → ISOLATE → BUILD → VERIFY → INTEGRATE → LEARN → REPEAT`

One `task_id` is minted at SENSE and stamped onto every artifact via
`idempotency_key = sha256(task_id | transition | source_sha)[:32]`
(the `correlation.make_key` scheme). Every stage is append-only and dedupes on
that key, so re-running any stage is safe. The current state of a job is one of
`QUEUED → DISPATCHED → PASSED → AWAITING_HUMAN → {FAILED | BLOCKED | UNCERTAIN}`
(`orchestration.JobState`); crashes landing in `UNCERTAIN` are never auto-resumed.

### 2.1 Stage table

| Stage | Purpose / I→O | Gate (fail = stop/route) | Role(s) | Idempotency key | Human approval | Failure handling |
|---|---|---|---|---|---|---|
| **Sense** | Telemetry (CI/test failures, gate reports, CVEs, issues) → bounded, deduped `ProblemCandidate`. | Signal is a real FAIL/ERROR report with a task id/source; dedupe on key. | A07 audit, A08 eval, super-collect; `self_improve.detect_problems` | `make_key(task_id, "issue:<gate>:<detail>")` | None to *observe*; approval required only to *act* (LEARN). | Malformed/secret-bearing reports dropped, never logged; source retained as bounded text. |
| **Propose** | Candidate → ordered plan (`Goal/Approach/Files/Tests/Risks`) + spec-ref + acceptance + work order. | `planning.check_plan` must PASS: required sections present, named test command, explicit risks, non-empty file allow-list (gaps §22.4). | B02 spec compiler, B03 spec-to-test, planning gate | `make_key(task_id, "plan", spec_sha)` | High-risk spec sign-off (P13/P16); edits to guardrails/rubrics always F01. | Vague plan → `BLOCKED` with reason; plan is a claim, not a guarantee, so VERIFY still runs. |
| **Isolate** | One worktree + unique branch per task; snapshot pre-change state; prepare dependencies. | `create_worktree` valid branch + absolute path; `isolation_violations` empty; protected paths refused. | C-builder station, worktree pool | `make_key(task_id, "isolate", base_sha)` | None (mechanical). | Slot conflict → queue/retry with backoff; protected path → immediate escalation, never auto-edit. |
| **Build** | Render work order (KISS/YAGNI, allow-list, tests) → agent applies bounded edits on own branch and commits there. | Route decides worker+model+budget: `trivial→apprentice (12 tools, inspection+tests)`, `standard→striker (40, +security+review)`, `risky→striker (60, quorum, +runtime)` (`routing.py`). Route model must be injected into the worker command template or it is silently lost (gaps §22.1). Apprentice gets a hard tool budget and refuses risky classes (§22.5). | A03 router, A05 budget governor, C builders / apprentice / fleet of strikers | `make_key(task_id, "build", base_sha)` | None for code; new vendor/paid service/spend over budget → F01 (P13). | Worker infra failure → `fleet.FleetRunner` fails over to next runner; crash → `UNCERTAIN`, human triage, no auto-resume; no push from worker. |
| **Verify** | Inspection + security scan + isolated tests + review (+ runtime probe + smoke + holdout on risky/UI). | `pipeline.run_pipeline` single durable verdict; all gates PASS; zero unresolved review findings; review provider actually used and recorded (§22.2); risky jobs run `runtime_verify` and FAIL blocks evidence (§22.3). Multi-model critic quorum on risky. Evidence captured before/after in a temp copy. | D01–D08 critics, independent model families, A11 drift detector | `make_key(task_id, "verify:<gate>", head_sha)` | Production change with no passing independent verification → fail closed (P13). | FAIL → job `FAILED`, evidence + failure detail emitted; bounded rework attempt, then `BLOCKED` + escalation packet (default: hold). |
| **Integrate** | Combine disjoint verified branches; run the **full suite** after combination; report branch ready. | `integration.propose_integration` disjointness + `merge_queue.evaluate_merge` on required gates/protected paths; suite must run post-combination (§22.7). Still **no merge** — a named human approves the final merge. | C08 merge/integration arbiter, ship gate | `make_key(task_id, "integrate", combined_sha)` | **Yes** — `approvals.decide` with a named human approver; approve keeps state `AWAITING_HUMAN` until gated `ship` actually runs. | Suite fails on combination → back to BUILD on the offending branch with same task lineage; conflict → human packet. |
| **Learn** | Feed discovered failures back into guarded improvement. | `self_improve.detect_problems → plan_issue → publish_issue → bridge_issue_to_job`; publish only with named human approval, allow-listed repo, `dry_run=false` (§22.8); proof logged with key. | A09 self-improvement, A08 eval, F01 | candidate/draft/issue all carry `make_key(...)`; dedupe on it. | **Yes** — no publish, dispatch, merge, or release without a named human; the factory cannot loosen its own guardrails. | Duplicate key → ignored; unapproved → `BLOCKED` with reasons; accepted issue becomes a **new** QUEUED job → loop repeats. |
| **Repeat** | Scheduler artifacts (systemd timer/cron, generate-only) re-enter SENSE on a cadence; metrics-as-product score the loop. | `metrics.compute_metrics` (state mix, first-pass/rework rate, blocked escalations, avg attempts) + human-curated gold set A09 cannot edit (§8d). | A10 pipeline reliability, A08 eval | cadence key `make_key("scheduler", "tick", date)` | Changes to budgets/critics/permissions/guardrails → F01. | Drift/eval regression → halt timer (`scheduler.generate_timer` includes halt text); never auto-promote a model/rule that fails gold tasks. |

### 2.2 Non-negotiable invariants (apply to every stage)

- No secrets in files or logs; providers discovered from a repo-local `.env`, values never logged, provider used recorded in evidence (`env_config`, gaps §22.2).
- Workers never push, merge, tag, release, or deploy; those are owner-scoped, fail-closed tools (`remote_merge`, `github_release`, `ship`, `merge_queue`).
- Every report is stamped with `task_id`, `transition`, `source_sha`, `idempotency_key`; every event append-only and deduped (`correlation`).
- Each job commits on its own branch; protected paths (infra/, secrets, CI config, migrations, docs/SPEC*.md) are never auto-edited (`protected_paths`, `FACTORY_RULES.md`).
- Focused tests plus Ruff/Mypy gates before a PASS is trusted.
- Timeouts, output caps, and bounded token/tool/file budgets everywhere; a stalled job becomes `UNCERTAIN`, not "success".

---

## 3. Loop stage → existing Forge module mapping

| Stage | Existing modules in `src/agency/forge/` | Present / missing |
|---|---|---|
| **Sense** | `self_improve.detect_problems`, `metrics.compute_metrics`, `observability.make_trace`, `cost.record_cost`, `mcp_server` (status/inspect tools) | Present: bounded FAIL/ERROR detection + idempotency key + metrics. Missing (gap §22.8): wired to real CI/gate failure feed; live heartbeats/token estimates (§22.6). |
| **Propose** | `backlog.slice_prd`, `planning.check_plan` / `path_allowed`, `work_order.render_work_order`, `agent_skills.skill_for`, `routing.route_job` | Present: plan shape check + allow-list + rendered work orders + route classes. Missing (§22.4): quality gate (named test command, required risks) — currently shape only. |
| **Isolate** | `worktree.create_worktree`, `worktree_pool.acquire/release/reap`, `project_adapters.prepare_dependencies`, `protected_paths.is_protected_path` | Present: full isolation, TTL reaping, path-conflict rule, protected paths. Complete for this stage. |
| **Build** | `orchestration.BlacksmithOps.submit/dispatch_next`, `external_agent.run_external_agent`, `coding_agent.run_coding_agent`, `apprentice.run_apprentice`, `fleet.FleetRunner`, `worker_pool`, `scheduler`, `routing`, `work_order` | Present: state machine + striker/apprentice paths + model routing + failover + bounded pools. Missing (§22.1): route model injected into command template; (§22.5): distinct bounded apprentice path with enforced promotion gate. |
| **Verify** | `pipeline.run_pipeline`, `inspection.inspect_changes`, `security_scan.scan_changes`, `test_runner.run_tests`, `evidence.capture_evidence`, `holdout.run_holdout`, `runtime_verify.verify_runtime`, `smoke.smoke_app`, `diff.build_diff_bundle`, `reviewer.run_review`, `self_improve` keys | Present: all gate primitives + evidence + review loop. Missing (§22.2): review config auto-discovery; (§22.3): runtime gate auto-run on risky jobs; quorum/second-family critic only partly wired. |
| **Integrate** | `integration.propose_integration`, `merge_queue.evaluate_merge`, `ship.ship_branch`, `remote_merge.plan_remote_merge`, `github_release.plan_github_release`, `approvals.list_briefs/decide` | Present: disjoint combine + gate/protected-path evaluation + human-gated ship/merge/release. Missing (§22.7): suite runs after combination (currently stops early); §22.6 weak live state during integration. |
| **Learn** | `self_improve.detect_problems/plan_issue/publish_issue/bridge_issue_to_job`, `correlation.CorrelationStore.append/events`, `metrics.format_metrics`, `holdout` (gold scenarios) | Present: guarded draft→issue→job bridge with named approver + idempotency. Missing (§22.8): CI/gate-failure ingestion disconnected; gold-set eval gate for factory self-changes (§8d). |
| **Repeat** | `scheduler.generate_timer`, `orchestration.status/recover_uncertain`, `metrics.compute_metrics`, `observability`, `prompts`/`prompt_params` | Present: generate-only scheduling artifacts + recovery semantics + self-measurement. Missing: heartbeat/lease updates and cost estimates in watch/trace (§22.6). |

---

## 4. Top five changes to make Forge more autonomous and better, ordered by impact and cost

**1. Close the four cheap wiring gaps that already have acceptance criteria — HIGH impact, LOW cost.**
Wire the routed model into the worker command template (§22.1), auto-discover a repo-local `.env` and record the actual review provider (§22.2), auto-run `runtime_verify` on risky jobs with FAIL blocking evidence (§22.3), and run the suite after `integration.propose_integration` before reporting a branch ready (§22.7). These are already-designed, mostly configuration/plumbing changes; each has an explicit outcome in `gaps.md` §22. They convert "capability exists" into "capability fires unattended".

**2. Connect self-improvement to real gate/CI failures — HIGH impact, MEDIUM cost.**
Feed bounded FAIL/ERROR reports into `self_improve.detect_problems` with `correlation.make_key` idempotency, produce candidate jobs, and require a named human approval before any publish/dispatch (§22.8). This closes the loop in §2 stage LEARN→SENSE and is the single change that turns Forge from a one-pass pipeline into a self-correcting factory. Fail closed, dedupe on key, never let A09 edit its own gold set.

**3. Strengthen the pre-code and apprentice gates — HIGH impact, MEDIUM cost.**
Upgrade `planning.check_plan` from shape to quality: non-empty allow-list, named test command, and explicit risks required for code jobs, with a caveat for legacy plans (§22.4). In parallel give `apprentice` a real bounded path — enforced tool/token budget, refusal of risky classes, promotion evidence recorded, distinct from striker (§22.5). Together these stop vague plans and cheap-wrong edits from consuming verification budget.

**4. Make live state and cost honest — MEDIUM impact, LOW–MEDIUM cost.**
Add lease/heartbeat updates during DISPATCHED so `forge status` shows real state instead of `UNCERTAIN` while running, and include agent duration plus estimated cost in traces where available (§22.6). This enables budget kill-switches (A05), trustworthy multi-monitoring of parallel agents, and detection of stuck workers — prerequisites for running more than a handful of jobs unattended.

**5. Add independent, eval-gated critics for risky work — HIGH impact, HIGH cost.**
Enforce a second critic on a different model family plus multi-model quorum for `risky` routing (`routing.require_quorum`), a human-curated gold set that self-improvement cannot edit, and shadow-run new models on golden tasks before promotion (`DARK-FACTORY-DESIGN.md` §8d, §12.6, §12.13). This addresses the independence gaps that make otherwise-green pipelines untrustworthy (the judge of the factory is itself unverified). Highest long-term payoff for "better results" but the most expensive to build and maintain; do it after 1–4 make the loop reliable.

---

## 5. Closing note

The Forge already implements most of the loop's *mechanisms* (state machine,
worktrees, gates, evidence, human approvals, idempotency keys). The autonomy
gap is mostly *wiring and enforcement*, not missing modules: routing, runtime
verification, post-merge test execution, and self-improvement ingestion exist
as code but are not yet connected to the job flow. Prioritize items 1–3 for the
largest autonomy gain per unit effort; keep items 4–5 on the roadmap for
trustworthy unattended scaling. Always: no secrets, no worker-side
push/merge/deploy, own-branch commits, focused tests + Ruff/Mypy.
