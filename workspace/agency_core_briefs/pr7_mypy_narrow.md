# Narrow mypy recovery task — sandbox and Butler type contracts

The Agency MIT Python repo. Isolated C:/Users/alphi/agency-pr7-mypy worktree at c940869; main is dirty and off limits. Parent design: docs/SPEC_PR7_READINESS.md. Previous broad mypy worker was interrupted by an OpenCode API connection error, not by code. Limit this retry to src/agency/security/sandbox/manager.py, src/agency/security/sandbox/process.py, src/agency/butler/server.py, src/agency/butler/service.py and their existing relevant tests. Do not edit other files, lockfiles or CI configuration; do not suppress errors.

Pre-check: git status --short; uv run --no-sync mypy src/; uv run --no-sync ruff check src/agency/security/sandbox/ src/agency/butler/ --output-format concise; inspect call sites and import cycle. Report CHECK-1..N and file:symbol:purpose map before editing.

Goal: fix mypy errors in those four files only, especially `ProcessSandboxBackend` undefined type vs runtime lazy import, preserving behavior. Do not modify the public HTTP identity restriction. Add regression tests only if a runtime behavior changes. Keep changes small and committed with explicit paths, no push. Run relevant tests and focused Ruff/mypy; report before/after diagnostics. If blocked, return exact blockers rather than editing outside ownership.
