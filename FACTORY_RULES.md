# FACTORY_RULES.md — The Agency

Triage, quality gates, and limits for the software factory. Read by the
orchestrator and agents at the start of every workflow.

## Triage
- Classify each issue against MISSION.md. Reject (with reason) anything that
  is out of scope or drift, even if plausible and well-argued.
- PR size limit: one logical change per PR. Oversized PRs are split.
- Mandatory browser/UI testing when the change touches UI.

## Quality gates (all must pass before merge)
1. Forge inspection gate (bounded inventory + Python syntax).
2. Forge security scan (no high/critical findings).
3. Isolated test run (pytest in a temp copy).
4. Scenario proof (when available): real user journeys pass.
5. Independent reviewer score (NYX/Greptile) >= threshold, zero unresolved
   comments.

## Limits
- Max ~15 parallel features per machine (RAM is tight).
- No human writes or reviews raw code in the flow; humans tend the harness.
- Secrets from `.env` only.
- All state changes through the kernel (FindingLifecycle). No shortcuts.
- ATHENA is a peer platform, never a dependency.
- Protected paths (never auto-edited): infra/, secrets, CI config, migrations,
  and anything under docs/SPEC*.md without explicit approval.
