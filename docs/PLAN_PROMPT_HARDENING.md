# PLAN: Prompt Hardening — Audit & Improvement of All Agency System Prompts

**Date:** 2026-11-20 · **Status:** DRAFT for owner review · **Branch target:** `feat/prompt-hardening` (off `prompts`)

**Goal:** Find weaknesses in every system prompt the Agency ships, fix the outright defects,
close the two biggest gaps (Blacksmith prompt is dead code; striker workers get no prompt at
all), and DRY the six domain-agent prompts — without touching REAP upstream content and
without expanding anyone's authority.

**Non-goals:** No new capabilities for any agent. No changes to gate logic, merge authority,
or `release_not_authorized`. No REAP pack edits (upstream-synced, MIT, manifest header says
do not hand-edit). No LLM-driven orchestration decisions.

---

## 1. Role vocabulary (verified against the codebase)

| User's term | Codebase reality | Prompt status |
|---|---|---|
| **Blacksmith** | `agency.autonomy.blacksmith` (domain `forge`) | Prompt constant exists but is **dead code** — never wired |
| **Strikers** (a.k.a. striker workers) | External CLI workers (opencode etc.) invoked via `forge/external_agent.py` | **No prompt exists at all** — a raw `task` string is passed through |
| **Apprentice** | **Zero mentions** in `src/` and `docs/` — role does not exist yet | Nothing to improve; needs definition or deferral |
| **Forge (the forge itself)** | The pipeline (inspection/security/tests/review gates) — deterministic code, not an LLM | The only "prompt" is the reviewer's scoring prompt |

## 2. Verified current state (evidence, 2026-11-20)

| Prompt | Location | Wiring | Verbatim facts |
|---|---|---|---|
| Agency segments ×3 | `factory/prompts_builtin/segments/agency-{base-identity,evidence-discipline,l0-scope}.md` | Manifest `SEGMENT` entries, pinned at factory activation | 8 lines each: identity/capability≠permission, evidence 3-bucket rule, L0 observation-first |
| REAP segments ×3 | `segments/reap-{core-methodology,safety-scope,tool-availability}.md` | Manifest; tool-availability has `{{REA_READY}}`-style slots filled by `forge/prompt_params.py` | Methodology (6 rules), authorization scope, tool honesty |
| REAP packs ×25 | `prompts_builtin/reap/reap-*.md` | Manifest PACK entries, upstream-synced | Out of scope here |
| Domain agents ×6 | `agents/{personal,work,business,security,general,research}/agent.py` — inline `*_SYSTEM_PROMPT` constants | `system_prompt()` / `handle()` concatenate prompt + message; **do not use the segment system** | ~20–30 lines, 8 numbered rules, `{"final": ...}` suffix rule duplicated 6× |
| Blacksmith | `autonomy/blacksmith.py:12-23` `BLACKSMITH_SYSTEM_PROMPT` | **Dead**: grep shows zero imports; `activate_for_caps` (`autonomy/factory_auto.py:160-217`) has no `prompt_refs` param | Confirmed by PLAN_BLACKSMITH_FORGE_ORCHESTRATOR.md §2 |
| Striker worker | `forge/external_agent.py:103` `task: str = ""` | Raw string from caller; no template anywhere in `forge/` or `autonomy/` | Retry feedback planned in Blacksmith plan §3.1 |
| Forge reviewer | `forge/reviewer.py:384-392` inline scoring prompt | Single LLM call, JSON out, fails closed | Change set truncated to 6000 chars silently; test results not included (plan gap §3.7) |
| Coding planner | `agents/coding_planner.py:21-33` `_PROMPT_TEMPLATE` | JSON-edit extraction | No instruction for the impossible-task case |
| Orchestrator Butler | `orchestrator.py:632-664` `_system_context` | Grounded system facts + roster | Anti-fabrication present; no tool/escalation guidance |
| Demo agent | `agents/demo/agent.py:100` | "You are a security scanner" one-liner | Demo-only; cosmetic |

## 3. Weaknesses (per prompt, with the research rule each violates)

### W1 — GENERAL_SYSTEM_PROMPT has a literal duplicate rule (bug)
`agents/general/agent.py:11-13`: the header line "You are the General Agent…" is repeated
verbatim as rule 1. Conflicting/duplicated instructions are the #1 steerability bug (OpenAI
GPT-5.1 guide explicitly says to hunt these down; its metaprompt method finds them).

### W2 — Blacksmith prompt is dead code (highest priority)
The best-written prompt in the repo never reaches a runtime agent. Wiring is already planned
(`PLAN_BLACKSMITH_FORGE_ORCHESTRATOR.md` Phase 1, segment `agency-forge-orchestrator`).
This plan defers to that plan for wiring but fixes two things it does not cover:
the dead constant must not survive as a second source of truth (alias or delete), and the
prompt needs delegation-quality upgrades (W3 rules).

### W3 — Orchestrator prompt doesn't teach delegation
Anthropic's multi-agent lesson #2: without detailed task descriptions, subagents duplicate
work or misinterpret. The Blacksmith prompt says "decompose work into bounded slices" but
never specifies what a slice must contain. Every dispatch needs the four fields Anthropic
found necessary: **objective, output format, tool guidance, task boundaries**.

### W4 — Striker workers have no contract at all
`run_external_agent` receives a bare string. Cognition's Principle 1 ("share context, share
full traces") and Principle 2 ("actions carry implicit decisions") say a worker without the
task's decisions — acceptance criteria, protected paths, isolation rules, deliverable —
will invent its own. The Blacksmith plan's `ForgeJob.acceptance` fields exist precisely to
feed such a template; the template itself is missing.

### W5 — Six domain prompts duplicate a drifting skeleton
Scope line + 8 rules + `{"final": ...}` suffix repeated 6× with drift: honesty rule worded
differently in each ("Never invent personal details" / "Never fabricate deliverables" /
"Never fabricate financial figures"); the evidence 3-bucket discipline exists as a segment
but security re-invents a weaker inline version ("Distinguish confirmed vs. suspected").
Any future rule change must be made six times and will drift. (DRY; single source of truth.)

### W6 — Filler verbs instead of verifiable behavior
"Reflect on your answer for completeness and safety before finalizing" (×4 prompts),
"Reflect on failures" (Blacksmith), "Reflect on plans for gaps" (work/business). None of
these is checkable behavior; Anthropic and OpenAI both prescribe concrete, evaluable
instructions over vague exhortations. Replace with explicit stop/escalation conditions and
a defined self-check (e.g., "before finalizing, re-read the request and list any part you
did not address; if any, say so").

### W7 — No stop conditions or escalation format anywhere
Base identity says "Escalate or stop when a request leaves [your scope]" but no prompt says
*how* to escalate, *to whom*, or *what to include*. The Blacksmith plan defines BLOCK+escalate
in code; the prompts never mention the format. Agents need: when to stop, what message shape
carries an escalation, and that "I can't do X because Y (evidence)" is a valid final answer.

### W8 — Reviewer prompt is under-specified
`reviewer.py`: no severity rubric (what makes a finding high vs critical?), no evidence
requirement on findings, no uncertainty instruction (an unsure reviewer should lower the
score and say why), and the 6000-char change-set truncation is silent (reviewer can't know
context was cut). The missing test-results context is already planned (§3.7 of Blacksmith
plan) — this plan adds the rubric/uncertainty/truncation-marker fixes.

### W9 — Output contract leaks into persona prompts
`Always end with {"final": "<your reply>"}` is a transport detail of the loop harness,
duplicated in 6 constants. Single-source it; the harness contract belongs in one shared
rule, not six copies that can drift.

### W10 — No prompt regression tests
Nothing tests prompt *content contracts* (required sections, no duplicated rules, all
`{{SLOT}}` filled at composition). Prompts are the most-editable safety surface in the repo
and the only one with zero tests. (`gaps.md` #2 also notes self-improvement can rewrite
purpose prompts — testable invariants reduce that blast radius.)

## 4. Research checklist applied (sources in §9)

Rules used to judge the prompts above, distilled from the sources:

1. Identity line + explicit scope + explicit **non**-scope ("refuse X and say why").
2. Delegation = objective + output format + tool guidance + task boundaries (Anthropic).
3. Scale effort to task size; state the budget (tool-call caps, retry caps) numerically.
4. Concrete, evaluable rules; delete filler verbs ("reflect", "be smart").
5. Hunt conflicting/duplicate instructions; keep one rule per behavior.
6. Actions need context: give workers every decision made before they act (Cognition).
7. Prefer single-threaded/serial unless parallel isolation is enforced by harness
   (worktrees + gates) — The Agency's design satisfies this only via the gates; prompts
   must reinforce it (one job in flight, isolated worktrees).
8. Stop conditions, budget caps, and escalation format stated in the prompt, not just code.
9. Evidence discipline: claims cite artifacts; verdicts quote report ids/digests.
10. Keep prompts short and structured (labeled sections); verbosity is controllable.
11. Test prompts like code: golden tasks + contract tests + metaprompt diagnosis loop.

## 5. Design

### 5.1 Shared operating rules for domain agents (W5, W6, W7, W9)
New module `src/agency/agents/_prompt_shared.py`:

```python
HONESTY = (...)      # never claim undone work; cite evidence; say "I don't know"
OPERATING = (...)    # plan → act → verify (with what command) → report; stop conditions
ESCALATION = (...)   # when out of scope/blocked: stop and answer with
                     # {"final": "BLOCKED: <what> because <evidence>"}; never improvise authority
OUTPUT_CONTRACT = 'Always end with {"final": "<your reply>"}.'   # single source
```

Each `*_SYSTEM_PROMPT` becomes: identity line + Scope line + domain Rules (domain-specific
only, 3–5 rules) + shared blocks. Text of rules is unchanged where correct — this is a
dedup refactor, not a rewrite. Behavior change: none intended; existing tests
(`tests/test_agents_*.py`) must pass untouched.

### 5.2 Work-order template for striker workers (W4)
New `src/agency/forge/work_order.py` (deterministic template renderer, pydantic-input,
no LLM):

```
render_work_order(job) -> str
  sections: OBJECTIVE (one sentence, from job.title/task)
            CONTEXT (repo, worktree ABSOLUTE path, branch)
            BOUNDARIES (protected paths list; isolation rule: change nothing outside worktree;
                        no network installs; no subprocess.* [matches security gate])
            ACCEPTANCE (job.acceptance items verbatim)
            TESTS (job.test_paths + exact command)
            DELIVERABLE (what changed_paths to leave; run tests; honest output)
            ON_FAILURE (stop and return the exact error output; do not fabricate success)
            RETRY NOTE (attempt N/M + bounded failure tail of previous attempt, secrets redacted)
```

`BlacksmithOps.dispatch_next` (Blacksmith plan §3.2) passes `render_work_order(job)` as the
`task` string. Until that plan lands, `forge/cli.py` gets `--work-order` support so the
template is usable and tested independently. Template is versioned in code, not the factory
manifest (it is not an agent persona; it is a per-job document).

### 5.3 Blacksmith prompt upgrades (W2, W3) — coordinated with Blacksmith plan Phase 1
Keep the planned segment `agency-forge-orchestrator` as the single source of truth; changes
to its body (all additive, same authority):

- Delegation clause: "Each dispatch states objective, output format, tool guidance, and
  boundaries; include acceptance criteria and test paths verbatim from the job."
- Effort rule: "One job in flight; queue the rest." (RAM constraint already in plan §6.)
- Escalation format: "BLOCK with the failing sub-gate name and the bounded failure tail."
- Replace "Reflect on failures" with: "On FAIL, name the failing gate and the smallest
  change likely to fix it; retry with that feedback, max twice, then BLOCK."
- Delete the dead `BLACKSMITH_SYSTEM_PROMPT` constant (or make it import the segment text)
  so there is exactly one source.

### 5.4 Reviewer prompt hardening (W8)
In `forge/reviewer.py` (coordinate with Blacksmith plan §3.7 which adds test results):
add severity rubric (low = style/naming; medium = missing validation/error handling;
high = security or correctness bug in changed paths; critical = secret leak, injection,
protected-path edit), "every finding must cite path:line", "if uncertain, lower the score
and say what is unverified", and mark truncation explicitly (`"…[change set truncated]"`).

### 5.5 Prompt contract tests (W10)
New `tests/test_prompt_contract.py` (pure string checks, no LLM):
- no duplicated lines inside any `*_SYSTEM_PROMPT` (would have caught W1);
- every domain prompt contains identity line, Scope, honesty rule, `{"final": ...}` rule;
- every `{{SLOT}}` in manifest segments has a matching param provider name;
- work-order output contains all 8 section headers and the verbatim acceptance items;
- reviewer prompt contains the four severity definitions.
Metaprompt diagnosis loop (GPT-5.1 guide §metaprompting) is manual practice, not code.

### 5.6 Apprentice (defer — owner decision)
"Apprentice" has zero footprint in the code. Do not invent a role on spec (YAGNI). If the
owner wants it, the natural fit is a FOLD-style prep worker (research + notes → spec for a
slice, never edits code — Claude Code's own subagent pattern), which would need: blueprint
domain, L0 caps, a segment, and a planner strategy. Deferred to its own plan after owner
confirmation.

## 6. Phases (TDD, gate per phase: ruff check/format, mypy, focused pytest)

1. **Bug + contract tests**: `test_prompt_contract.py` RED (catches W1) → fix
   `GENERAL_SYSTEM_PROMPT` GREEN → commit `fix(agents): dedupe general prompt rule; add prompt contract tests`
2. **DRY domain prompts**: `_prompt_shared.py` + 6 constants re-composed; all existing
   agent tests pass unchanged → commit `refactor(agents): single-source shared prompt rules`
3. **Work-order template**: `forge/work_order.py` + tests + `--work-order` CLI →
   commit `feat(forge): render structured work orders for striker workers`
4. **Blacksmith prompt body + dead-constant removal**: segment body edits + wiring is
   Blacksmith plan Phase 1 (reference, don't duplicate) → commit
   `feat(forge): forge-orchestrator prompt delegation + escalation clauses`
5. **Reviewer hardening**: rubric/evidence/uncertainty/truncation-marker + tests →
   commit `feat(forge): reviewer severity rubric and evidence requirements`

## 7. Acceptance

- Contract tests pass for every shipped prompt; zero duplicated rules anywhere.
- A striker dispatch (real or hermetic fake) renders a work order containing objective,
  boundaries with protected paths, verbatim acceptance, test command, and failure contract.
- Reviewer JSON findings all carry path:line; severity values are justified by the rubric.
- No agent's authority changed: L0 scope, `release_not_authorized`, human merge gate intact.

## 8. Risks / constraints

- `gaps.md` #2: self-improvement can rewrite purpose prompts without a distinct human gate —
  this plan's edits go through reviewed commits, not `improve_agent`; contract tests bound
  what a future rewrite can silently break.
- Domain prompts are import-time constants; behavior-affecting wording changes are additive
  only (Phase 2 is dedup, not rewrite) to keep the ~20-min suite stable.
- REAP content is upstream-synced; agency-`*` segments are ours and version-pinned —
  bump segment versions (1.0.0 → 1.1.0) only in Phase 4.
- Concurrent sessions edit the tree; execute in a worktree per repo convention.

## 9. Sources

- [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) (orchestrator-workers; ACI/tool docs; stopping conditions)
- [Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system) (delegation task fields; effort scaling; guardrails)
- [OpenAI — GPT-5.1 Prompting Guide](https://cookbook.openai.com/examples/gpt-5/gpt-5-1_prompting_guide) (conflict hunting; metaprompt diagnosis; persistence; verbosity spec)
- [Cognition — Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents) (share full context; actions carry implicit decisions)
- [HumanLayer — Advanced Context Engineering for Coding Agents](https://www.humanlayer.dev/blog/advanced-context-engineering) (FOLD: notes → spec → fresh-context execution)
- [agents.md](https://agents.md) (build/test commands and constraints as durable instruction files)

## 10. Open owner decisions

1. "Strikers" vs "strikers": confirm one name (repo currently uses striker as display name).
2. Apprentice: define the role now, or defer (plan assumes defer).
3. Should `{"final": ...}` stay in the persona prompt (as OUTPUT_CONTRACT) or move to the
   harness loop where it could be appended mechanically?
