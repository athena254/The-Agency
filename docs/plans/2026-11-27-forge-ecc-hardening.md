# Forge ECC hardening — implementation plan

**Goal:** close the ten remaining lessons from the software-factory notes with deep modules behind small interfaces, then wire them into the governed Forge loop without weakening its human gates.

**Architecture:** five independent slices create new modules only. The orchestrator owns every existing entry point (`external_agent.py`, `orchestration.py`, `cli/main.py`, `pipeline.py`, `inspection.py`, `approvals.py`, `project_adapters.py`) and wires the modules between waves. A module is deep when callers get several behaviours through one small interface; prefer results over side effects, accept dependencies rather than creating them, and use two real adapters before introducing a seam.

**Tech stack:** Python 3.12, Pydantic v2, pytest, Ruff, Mypy, git worktrees.

**Gate commands (children):** `PYTHONPATH=<worktree>/src C:/Users/PC/The-Agency/.venv/Scripts/python.exe -m pytest <own test files> -q --no-header`; Ruff check/format and Mypy on own files only. Do NOT run the full suite in parallel — the orchestrator runs it once after landing.

## Wave 1 — five parallel slices, exclusive file ownership

All slices: read this plan first, then only the named source files. Commit on your own branch. Do not push, merge, deploy, edit `.env`, or touch another worktree. If a full-suite failure is outside your file list, report it; do not fix it.

1. **A08 Eval Engineer + D01 Critic** — `factory/ecc-eval`, `D:/ecc-a1`.
   Create `src/agency/forge/quorum.py` (`run_quorum`, `QuorumReport` — second critic on a different model family, disagreement escalation, bounded retries) and `src/agency/forge/gold_set.py` (`GoldSet`, `evaluate_gold_set`, immutable human-owned task list with secret redaction). Test: `tests/test_forge_ecc_eval.py`. Forbidden: `reviewer.py`, `external_agent.py`, `cli/main.py`.

2. **A07 Provenance + B02 Spec Compiler** — `factory/ecc-provenance`, `D:/ecc-a2`.
   Create `src/agency/forge/handoff.py` (`HandoffEnvelope`, `make_handoff`, `validate_handoff` — task_id, parent_id, from/to, spec_ref, risk_class, artifacts, acceptance, budget, producer, evidence, status) and `src/agency/forge/regression_first.py` (`RegressionProof`, `check_regression_first` — capture failing-test evidence before a fix is trusted). Test: `tests/test_forge_ecc_provenance.py`. Forbidden: `correlation.py`, `work_order.py`, `external_agent.py`, `cli/main.py`.

3. **A03 Model Router + A05 Budget Governor** — `factory/ecc-gates`, `D:/ecc-a3`.
   Create `src/agency/forge/parallel_gates.py` (`run_parallel_gates`, `ParallelGateReport` — independent cheap gates concurrently, review only after tests) and `src/agency/forge/diff_budget.py` (`check_diff_budget`, `DiffBudgetReport` — bounded changed lines/files with documented exception path). Test: `tests/test_forge_ecc_gates.py`. Forbidden: `pipeline.py`, `inspection.py`, `test_runner.py`, `external_agent.py`.

4. **A01 Orchestrator + A10 Pipeline Reliability** — `factory/ecc-reliability`, `D:/ecc-a4`.
   Create `src/agency/forge/escalation.py` (`EscalationPolicy`, `evaluate_escalation` — SLA for `AWAITING_HUMAN`, never auto-approve) and `src/agency/forge/loop_metrics.py` (`LoopHealth`, `compute_loop_health` — first-pass rate, rework rate, blocked escalations, attempts; halt recommendation on regression). Test: `tests/test_forge_ecc_reliability.py`. Forbidden: `orchestration.py`, `metrics.py`, `cli/main.py`.

5. **A06 Guardrail Author + A04 Permission Warden** — `factory/ecc-adapters`, `D:/ecc-a5`.
   Create `src/agency/forge/adapters.py` (`ProductAdapter`, `adapter_for`, two real adapters: node and python) and `src/agency/forge/scheduler.py` (`generate_schedule`, `ScheduleBundle` — generate-only cron/systemd text, never install or run). Test: `tests/test_forge_ecc_adapters.py`. Forbidden: `project_adapters.py`, `cli/main.py`, `orchestration.py`.

## Wave 2 — orchestrator wiring (parent only)

Merge the five branches, then wire each module at one seam: quorum + gold set into risky reviews; handoff envelope into work orders and correlation; regression-first into bug jobs; parallel gates into `run_pipeline`; diff budget into inspection; escalation into `forge jobs`/`forge run`; loop health into `forge metrics`; generic adapters into `project_adapters`; generate-only schedule into a CLI command. Add parent wiring tests. Fix conflicts, not symptoms.

## Wave 3 — proof

Run the project gates in order (Ruff, Mypy, full pytest), a real probe outside the test helpers against the actual job DB, and a Forge pipeline over the changed paths. Create a PR, wait for CI, then merge only after gates are green. Report in plain English with branch, commit, real gate output and remaining caveats.

## Non-negotiables

No secrets in files or logs. Workers never push, merge, release or deploy. Own-branch commits. Bounded timeouts/output/budgets. A stalled job becomes `UNCERTAIN`, never "success". Reports under 25 lines.
