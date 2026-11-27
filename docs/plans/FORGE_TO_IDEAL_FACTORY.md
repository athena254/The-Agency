# Forge → ideal software factory migration plan

**Goal:** take Forge from a governed builder to the dim, self-maintaining factory described in `docs/IDEAL_SOFTWARE_FACTORY.md`, without weakening any human gate.

**Architecture:** keep the engine product-agnostic. All target-repo knowledge moves into `.factory/adapter.yaml` and hooks. Every new capability is a deep module behind a small interface, wired at one seam in the CLI or worker. Tests first, evidence after. No secrets, no silently installed tools, no worker-side push/merge/deploy.

**Tech stack:** Python 3.12, Pydantic v2, pytest, Ruff, Mypy, YAML adapters, SQLite-backed Blacksmith queue, git worktrees.

**Dependency order:** Phase 0 → 1 → 2 → 3 → 4 → 5. Phase 6 (governance) runs throughout. Do not start a later phase with an earlier phase's gate red.

## Current state vs ideal

| Area | Forge today | Ideal | Phase |
|---|---|---|---|
| Repository understanding | Role skills and work orders; no repo archaeology | `.factory/adapter.yaml`, revival mode, generated guidance | 0 |
| Specs and planning | Enforced plan file with allow-list, tests, risks | PRD, critic challenge, committed spec and coverage recipe, architecture and task waves | 1 |
| Build isolation | One worktree and branch per job, atomic queue claim | Same, plus overlap check against open PRs | 0/1 |
| Verification | Inspection, security scan, tests, review, runtime, diff budget, regression proof | Same plus held-out tests, real second provider, six-phase audit, bounded review loop | 2 |
| Deployment | Gated local ship and release tools | staging → smoke → canary → prod, rollback rehearsed, human approved | 3 |
| Maintenance | CI failure capture and proposal bridge | Scheduled telemetry → issue → builder → QA → review → handoff, doc updater, revival | 4 |
| Triggers | `forge run`, generate-only schedule | Installed, supervised scheduler with kill switch and backpressure | 5 |
| Human ownership | Named approvals, path-derived gates, gold set seam | Same plus UI and immutable gold set owned by F01 | 6 |
| Observability | Traces, live view, load probe | Metered cost, token use, SLOs and halt rules | 5/6 |

## Phase 0 — Repository onboarding and adapters

1. Create `src/agency/forge/adoption.py` with `inspect_repository`, `AdapterDraft`, `detect_revival_mode`. Bounded read-only scan of languages, test setup, CI, README and git history. No edits to the target repo.
2. Create `src/agency/forge/adapter_manifest.py` with `load_adapter`, `write_adapter`, `validate_adapter` for `.factory/adapter.yaml`: build/test/lint/deploy commands, protected paths, skill profile. Refuse to run when missing or invalid.
3. Add CLI `forge adopt --repo <path> --write` that prints the draft and writes only after an explicit flag. Never overwrite an existing adapter.
4. Acceptance: run against a stale fixture repo with no CI; detect revival mode, emit a valid adapter, and `ruff`/`mypy`/a focused pytest file all pass. A second run refuses to overwrite.

## Phase 1 — Specs before code

1. Create `src/agency/forge/spec_gate.py` with `SpecBundle`, `check_spec_gate`. Require in `specs/<feature>/`: `PRD.md`, `challenge.md`, `coverage.md`, `architecture.md`, `tasks.md`, and at least one held-out test path under `.factory/holdout/`.
2. Wire `WorkOrder` to carry `spec_ref`; `render_work_order` includes the spec path and coverage recipe.
3. Refuse dispatch for standard and risky jobs when `check_spec_gate` fails. Trivial jobs stay on the lighter path with an explicit caveat.
4. Add CLI `forge spec --feature <name> --check` and `forge plan --feature <name>` that prints the ordered task waves.
5. Acceptance: a risky job with no spec is BLOCKED; one with a complete spec dispatches with the spec path in evidence.

## Phase 2 — Independent verification

1. Wire `quorum.apply_risky_review` to two configured live providers; if the second provider is missing, fail closed with `second_critic_unavailable`. Never use the same model twice and call it two families.
2. Wire `holdout.run_holdout` into `external_agent` for risky and spec-gated jobs. The builder's work order must list only the path, never the test content; evidence records the verdict and a `holdout_unseen` caveat.
3. Add `src/agency/forge/security_audit.py` implementing the six phases as a bounded orchestration over existing scan tools; structured output, no invented findings.
4. Add a bounded review loop: cap rounds at 5, require zero unresolved actionable findings, report the real score and stop honestly on timeout.
5. Acceptance: a risky job with two providers and a holdout produces evidence naming both critic families and the holdout verdict; missing either blocks the job.

## Phase 3 — Deployment governor

1. Create `src/agency/forge/deployment.py` with `DeploymentPlan`, `plan_deployment`, `execute_deployment_step`. Steps: staging, smoke, canary, prod; each step needs the previous one's evidence.
2. Wire `forge deploy --step staging|canary|prod --dry-run` and a human `--approver`. Default is dry-run. Production requires a named human and a rollback plan file.
3. Keep `ship.py` as the local merge seam; deployment never calls it implicitly.
4. Acceptance: staging FAIL blocks canary; canary FAIL blocks prod; dry-run performs no external effect; rollback file is present before prod.

## Phase 4 — Maintenance control loop

1. Wire `improvement_bridge.bridge_failures` into `self_improve.plan_issue` with `correlation.make_key` idempotency and dedupe.
2. Add `src/agency/forge/maintenance.py` with scheduled job kinds: dependency scan, security re-audit, consolidation/entropy scan, performance check, doc updater, dead-repo revival check. Each produces candidate jobs only.
3. Add CLI `forge maintenance --kind <kind> --dry-run` and a human approval path before any candidate becomes a queued job.
4. Acceptance: replay a synthetic CI failure twice; exactly one candidate is produced. With no named approver nothing is queued. A doc-drift check reports a missing README section without editing it.

## Phase 5 — Triggers, scale and cost

1. Add a supervised trigger runner that reads `forge schedule` output, requires a named owner, and supports a kill switch file. Installing or enabling a host scheduler stays an explicit human action.
2. Enforce `load_harness` recommendations in `forge run`: when backpressure is advised, cap concurrency rather than accepting more work.
3. Add metered cost and token reporting at the worker seam where available; where not available, emit `cost_not_metered` instead of `0.0`.
4. Add SLO-style halt rules to `loop_metrics`: halt on repeated first-pass regression, unresolved blocked escalations or cost over cap.
5. Acceptance: a 100-job synthetic probe stays within the configured latency and reject thresholds; kill switch stops new dispatch; unmetered workers are reported honestly.

## Phase 6 — Governance throughout

- F01 owns PRDs, priorities, gold set, guardrails, critic changes and release decisions.
- The factory cannot edit its own rubric, approval rules, gold set or protected paths.
- Every decision has a named human, an idempotency key and an append-only record.
- Quarterly audit: replay gold tasks, review drift, check that no worker gained new authority.
- Definition of done: no rule exists only as prose when its violation would be an incident; encode it as a gate or hook.

## What we deliberately will not do

- No lights-out shipping to production.
- No worker-side push, merge, tag, release or deploy.
- No secrets in files, logs or evidence.
- No self-approval, generic approvers or invented cost/token numbers.
- No unattended scheduler before the Phase 2–4 gates have been green for a sustained period.

## Whole-plan definition of done

The migration is complete when Forge can take a repo plus an approved PRD, onboard it through an adapter, produce a spec and task waves, build in isolated worktrees, verify with held-out tests and two real critic families, ship through staging/canary/prod with human approval, maintain itself from real telemetry, and prove all of it with auditable evidence — while a human still owns goals, approvals and the factory's own guardrails.
