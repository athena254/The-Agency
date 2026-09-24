# PR #7 — LLMRouter/LLMAdapter contract repair

The Agency is an MIT Python 3.11+ repo. Work ONLY in a separate worktree from architecture/agency-core, not user's main. Read docs/SPEC_PR7_READINESS.md; src/agency/llm/router.py, adapter.py, config.py and existing tests before edits.

PITFALLS: P-C1 router.py:143/151 calls generate/stream(..., model=...) but adapter.py:103/136 only accept (prompt, context). P-C2 router.py:160/161 reads adapter.config, absent from adapter.py. P-C3 router.py:207 passes LLMConfig positional as provider; adapter.py:41 expects config keyword. P-C4 LLM adapter echo fallback must not mask a broken model path, and tests must run with fake adapters/no network or real keys. P-C5 no global ignore/type suppression, no changes outside owned files.

Pre-check: git status --short; targeted mypy output for src/agency/llm/router.py; map current constructor, generate, stream, explain and fallback semantics as CHECK-1..N before edits. Scope owned files: src/agency/llm/router.py, src/agency/llm/adapter.py only if necessary, tests/test_llm_router.py (new) and existing dedicated LLM tests only if behavior changes. No lockfiles, config edits or other modules.

Goal: route(task/context), route_stream and explain must work with actual LLMAdapter contract and preserve provider/model selection. Prefer passing model/provider override through context, not inventing unsupported keyword parameters. Make config exposure explicit only if needed, with immutable or read-only semantics; do not leak API keys in explain or logs. Use hermetic fake adapter for tests, cover explicit model override, preset selection, fallback, stream and explain; assert no network calls. Fix mypy errors in these two owned modules where feasible while preserving return behavior.

Acceptance: targeted pytest passes; `uv run --no-sync mypy src/` has fewer errors with none in router.py; `uv run --no-sync ruff check <changed files>` no new findings. Run git diff --check and inspect diff; commit explicitly owned files. Do not push or merge. Report exact outputs, remaining blockers and changes.
