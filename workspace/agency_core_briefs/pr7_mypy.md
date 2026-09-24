# PR #7 — Mypy baseline remediation

Project: The Agency, MIT Python 3.11+. Isolated worktree from draft PR #7; owner's main is uncommitted and must not be touched. Read docs/SPEC_PR7_READINESS.md, pyproject.toml, and CI mypy config first. This workstream owns mypy findings in src/agency/ except src/agency/agents/demo/agent.py, src/agency/bridges/claude/bridge.py, src/agency/bridges/coordinator.py, src/agency/bridges/openclaw/bridge.py, src/agency/telegram/server.py (owned by Ruff worker). Do not edit tests/test_evidence_store.py, tests/test_kernel_tasks.py, tests/test_security_purple.py or src/telegram_bot.py.

PITFALLS: P-T1 current diagnostics are 65 across 19 files; type fixes must preserve runtime semantics, especially LLMAdapter/router/Butler contracts. P-T2 sandbox manager has an F821 Ruff error at ProcessSandboxBackend; lazy import or TYPE_CHECKING changes must preserve import-cycle behavior and actual backend construction. P-T3 existing branch has a passing four-platform test matrix; no mass ignore/noqa/config weakening. P-T4 tests mutate tracked data/lattice.db; restore ONLY the test-generated artifact in your isolated worktree.

PRE-CHECK (report CHECK-1..N before edits): `git status --short`; `uv run --no-sync mypy src/` or `uv run mypy src/` if venv absent; `uv run --no-sync ruff check src/ tests/ --output-format concise`; map each affected file:symbol:contract, STOP and report map before editing.

GOAL: Correct actual type/contract errors minimally; static typing should reflect runtime behavior, not hide defects. Fix only owned files and explicitly named tests if required to prove runtime changes. Use focused batches, not broad formatting. Where a fix changes behavior, add a targeted regression test.

ACCEPTANCE: `uv run --no-sync mypy src/` reports zero or list exact unfixed blockers. Run impacted tests; `uv run --no-sync ruff check <changed files>` creates no new findings. Report before/after totals and precise changed files. Commit explicitly changed paths with `fix: address mypy baseline contracts`; do not push or merge.
