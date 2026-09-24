# PR #7 — Ruff baseline remediation, disjoint file group

Project: The Agency, MIT Python 3.11+. Isolated worktree from draft PR #7, not the owner's main. Read docs/SPEC_PR7_READINESS.md and pyproject.toml first. This task is narrowly scoped to Ruff diagnostics in these files only:
- src/agency/agents/demo/agent.py
- src/agency/bridges/claude/bridge.py
- src/agency/bridges/coordinator.py
- src/agency/bridges/openclaw/bridge.py
- src/agency/telegram/server.py
- src/telegram_bot.py
- tests/test_evidence_store.py
- tests/test_kernel_tasks.py
- tests/test_security_purple.py

PITFALLS: P-L1 broad Exception handlers can be deliberate boundary isolation; don't replace them blindly or suppress with noqa. P-L2 tests asserting Exception are too broad; narrow to the actual exception type without weakening test meaning. P-L3 avoid shared files owned by the mypy worker (Butler, orchestrator, sandbox manager). P-L4 this repo's tests mutate tracked data/lattice.db; restore ONLY that test artifact in this isolated worktree if generated.

PRE-CHECK (report CHECK-1..N before editing): `git status --short`; `uv run --no-sync ruff check src/ tests/ --output-format concise` or `uv run ruff ...` if venv absent; map these files' call sites and relevant tests, STOP and report a file:symbol:purpose map before edits.

GOAL: Eliminate Ruff findings in the OWNED files without altering error behavior; where handlers must remain broad, narrow only if same isolation semantics preserved, otherwise leave a documented blocker instead of suppressing. No edits outside the owned list; no `--fix` on the whole tree, no config weakening.

ACCEPTANCE: `uv run --no-sync ruff check <owned files>` yields zero; targeted pytest for changed tests succeeds, and any modified runtime behavior has regression coverage. If a finding cannot be resolved safely, report it instead of claiming all green.

REPORT BACK: exact files changed, diagnostic before/after counts for owned files, tests, any behavior risks; commit only explicitly owned files with `fix: address isolated Ruff baseline findings`. Do not push, merge or touch another worktree.
