# Forge Improvements — research-backed plan (drafted + executed 2026-11-22)

Research tracks: (1) line-level audit of `src/agency/forge/`, (2) survey of the
`D:\software factory` corpus (~13 projects: dark-factory, omniscient-dark-factory,
ai-software-factory, shipwright, awesome-software-factories, pstack, rstack,
humanlayer-skills, super-board, cubefarm, agentic-coding-starter-kit + root
syntheses), (3) web SOTA (AWS gated review pipeline, Addy Osmani agentic code
review, OpenHands verification stack, SWE-agent ACI, CodeAct, model-routing
cost literature).

Landed on `feat/forge-improvements` (worktree `D:/forge-improvements`), four
commits, forge suites green at each step.

## Defects found in audit → fixes landed

| Defect | Where | Fix |
|---|---|---|
| LLM reviewer scored scanner/lint **metadata** — never saw the code | `reviewer.py` `_review_prompt` | `forge/diff.py` bounded risk-ranked diff bundle (protected paths first, doubled budget, recorded omissions) fed into the prompt (P1, `d71a151`) |
| LLM findings carried no verifiable citation (hallucination risk) | `reviewer.py` findings parse | findings citing a path outside the change set are demoted to low `LLM_UNVERIFIED` — kept for visibility, never trusted, never dropped; **no LLM free text is persisted** (pre-existing test-enforced invariant kept — the draft plan's "preserve finding text" was consciously replaced by structured citations `path:line:rule:severity`) |
| No frozen-tree / delta re-review primitive | `reviewer.py` | `run_review(delta_base_sha=)` scopes the round to `base..HEAD`; unresolved base falls back full-scope with `delta_base_unresolved` caveat; `ReviewReport.reviewed_sha`/`delta_base_sha` recorded. Retries in BlacksmithOps are fresh-worktree full reviews by design (each attempt is self-contained), so the delta primitive serves in-place remediation loops (TUI/pipeline consumers) |
| Retries were blind (identical bare task string; work orders orphaned) | `orchestration.py:dispatch_next` | dispatch renders the `WorkOrder` contract (boundaries, work rules, acceptance, test command); attempt N≥2 embeds the previous attempt's bounded failure tail as RETRY NOTE; failed gates record bounded structured review citations (`b659e71`) |
| Cost recorded as 0.0 with no semantics | `orchestration.py` | per-attempt dispatch-ledger entries `{job}#a{n}` with model recorded; 0.0 = honest "not metered" for external CLI agents |
| `ship_branch` hijacked the main checkout (`git checkout target`) | `ship.py` | branch-ownership map (`worktree list --porcelain`): target owned by main repo → refuse when dirty then checkout+ff there; target owned by a sibling worktree → refuse (no index/HEAD desync); target nowhere checked out → `git fetch . src:dst` ref-only ff (`822c559`) |
| Enforcement narrower than the prose ("no edits to CI or gates") | `protected_paths.py` | fail-closed: `.claude`/`.zcode`/`.factory`/`.claude-plugin` dirs, `.mcp.json`, root governance files (AGENTS/CLAUDE/GEMINI/MISSION.md); lookalikes (`src/agents.md`) stay editable; `.github` outside workflows stays allowed (test pins kept); work order BOUNDARIES now states the enforced categories |
| Review gate never calibrated; no second opinion; no operational numbers | — | `forge calibrate-export` / `forge calibrate-score` (agreement-rate runbook, 0.75 bar); `run_review(shadow_llm=)` recorded-but-never-gating second reviewer with divergence logging (`f1df67b`); `forge metrics` (first-pass, rework, verdict mix, avg attempts, blocked) from the existing jobs DB |

## Calibration status (honest)

The reviewer gate is live-qualified (gaps.md #10) but **not yet calibrated**:
run `forge calibrate-export` → hand-label ≥20 verdicts → `forge calibrate-score`
before trusting threshold-4 blocking beyond its current fixtures. The shadow
leg only runs when a second provider is configured (task LLM ≠ review LLM).

## Deferred backlog (needs Phase-4 data first; YAGNI now)

- Bounded parallel dispatch (N workers) — note the known factory/forge
  contention flakes; needs the single-writer discipline re-checked.
- PR bounding per backlog source + open-branch overlap check (humanlayer /
  rstack patterns) — matters once dispatch volume grows.
- Risk-lane routing (complexity-adaptive review depth; dispatch ceilings on
  migration/perf/refactor keywords).
- Holdout upgrades: exact hand-computed figures (not properties), planted
  `defects.json` with detection-rung attribution; builder-blindness.
- MISSION-style drift rejection at intake ("never build" list).
- Consolidation patrol (Rule-of-Three, strangler-fig work orders with HITL
  flags); behavioral eval harness with offline stubs for forge's own changes.
- Arena fan-out for architecture-level work orders; blast-radius evidence
  ladder as a reviewer rubric requirement.

## Explicitly not done (golden rule 1)

54-agent dark-factory spec, trajectory-critic training, dashboards/GUI,
Docker/microVM isolation, new providers, a scheduler executor (the autonomy
loop is the trigger), token metering fabrication for external CLI agents.

## Test evidence

34 forge test files + consumers (`test_autonomy_engine_honesty`,
`test_loop_gates`, `test_protected_paths`, `test_blacksmith*`) — final sweep
result recorded in the landing commit message.
