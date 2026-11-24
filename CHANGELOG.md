# Changelog

## 0.3.0 — 2026-11-24

Release cut from `main` at `74c24a5` after the branch/worktree consolidation.

### Included
- Forge fleet failover, patient review, cross-family fallback judge (Hermes when the primary reviewer is down, fail-closed when both fail), resurrection of UNCERTAIN jobs, and striker-first fleet ordering (`4b34459`, `2f6fdfd`, `a1915eb`, `b8c131f`, `5a374b7`, `e02aa30`, `74c24a5`).
- Striker/apprentice naming taxonomy, Blacksmith forge-orchestrator prompt pinning, global agent rules (KISS/YAGNI/modular/research-plan-execute) and token-guarded tool contracts (landed on main before this release).
- Optional fail-closed TLS termination on the owner pilot launcher, local test-only CA generator, and second-provider live qualification (`0fe1d0c`, `4810b82`, `5d328fa`, `182faec`).
- Stash reconciliation: `tests/test_llm_model_selection.py` (six tests for cron/serious model selection and review-gate provider wiring) is the only genuinely unmerged artifact from the preserved WIP and is now part of the suite.
- Zero-loss preservation of all four stashes and all non-merged remote tips via `preserve/*` tags, `recovered/agency-20261123-preservation.bundle`, patch/archive exports, and `recovered/pre-consolidation/.zcode/`.
- CI and CD static-check jobs now use Python 3.12, matching `mypy`'s configured `python_version = "3.12"` and the dev environment floor; fixes the 3.11 `openai`/`anthropic` import-not-found failures that had broken every recent pipeline.
- CI/CD test jobs are sharded 4 ways (307 test modules → 77/77/77/76 per shard) so no Ubuntu runner approaches the 50-minute communication-loss limit seen on unsharded full-suite runs.

### Intentionally excluded (preserved, not lost)
- Forge MCP server work (`feat/forge-mcp` @ `31bac81`, point-in-time snapshot tag `preserve/inflight-mcp-20261124`). Kept out of 0.3.0 per release decision; available for a later release.
- Stale planning snapshots, scratchpad scripts, runtime logs and SQLite artifacts from the WIP stashes; retained in the preservation bundle only.

### Verification
- `ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/` — pass.
- `node --test tests/*.test.mjs` — pass.
- Isolated run of `tests/test_llm_model_selection.py` — 6 passed.
- Full suite and CI/CD workflow status recorded in the release verification notes.
