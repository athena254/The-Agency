# Draft PR #7 — review and quality-gate readiness

## Scope and baseline

`architecture/agency-core` contains bounded v1 foundations (threads, skill/workflow registries, Factory, read-only Forge). It is not a full product release. The PR's tests on Python 3.11/3.12 across Ubuntu/Windows and GitHub security scan pass, but repository-wide Ruff and mypy fail. At the starting SHA `a107ccc2db444c683e4c0e66b1adac9e9aa36eda`, Ruff reports 27 findings and mypy 65 errors in 19 files; these are inherited baseline debt, not permission to suppress diagnostics or rewrite architecture.

## Objective

Make PR #7 reviewable and, if safe, bring CI to green without changing its feature scope. Independently re-review the previously uncompleted HTTP identity, Forge filesystem, and legacy absorption boundaries. Handle quality work in isolated worktrees, compare outcomes with the starting baseline, and integrate only reviewed, minimal fixes. Do not merge or mark ready for review without explicit owner approval.

## Boundaries

- Preserve the owner's uncommitted `main` checkout. All workers use separate worktrees; no worker pushes, merges, or changes branch protection.
- No new authentication system or autonomous Forge/Factory behavior in this slice. The public Butler HTTP request remains anonymous/stateless until authentication is specified and approved.
- Fix observable bugs rather than concealing them with `noqa`, `type: ignore`, globally weakened checks, or catch-all changes that swallow errors. Where a broad exception is intentional at an application boundary, document the reason narrowly and test the fallback.
- Keep Ruff, mypy and security review paths separate. Review agents are read-only. Never put credentials in logs, briefs, diffs, or reports.

## Workstreams

1. **Independent review (read-only).** Map the HTTP identity, Forge inspection, and legacy absorber surfaces and their callers; attempt to reproduce concrete privilege, path, TOCTOU, and write-side-effect failures with hermetic tests or minimal local probes. Report file/line, reproduction, and severity. No external exploitation or network calls.
2. **Ruff debt.** Start with `uv run --no-sync ruff check src/ tests/ --output-format concise` (27 findings). Fix targeted findings without changing behavior; add regression tests for any changed behavior. Do not alter mypy-owned files except by agreement at integration.
3. **Mypy debt.** Start with `uv run --no-sync mypy src/` (65 findings). Group by dependency and correct types/real contracts; minimize changes to Ruff-owned files and avoid suppressions. Validate affected tests.

## Acceptance

- Independent review either reports no reproducible blocking flaw for the three named surfaces, or reports each flaw with a regression and fix before merge consideration. A blocked or inconclusive review is not approval.
- `uv run --no-sync ruff check src/ tests/` and `uv run --no-sync mypy src/` both pass on the integrated branch (or explicitly document remaining blockers; do not claim green).
- Full `pytest tests/ -q` passes under Python 3.11 and GitHub test matrix passes; no new security-scan finding. Wheel and offline smoke checks should still pass.
- Code review checks behavior, error handling, public API, and secrets hygiene; status and remote SHA are verified before reporting.

## Quality-gate remediation checkpoint

At `d59eda7`, the actual CI commands in `.github/workflows/ci.yml` are `ruff check src/ tests/`, then `ruff format --check src/ tests/`, and `python -m mypy src/` in a dependency-installed Python 3.11 environment. Local project-venv reproductions report 20 `BLE001`/`S110` lint findings, 53 mypy errors, and 87 unformatted files. The formatter was not reached in CI because lint stopped first. A separately installed temporary 3.11 environment produced many extra third-party `import-untyped` cascades: unlike the project venv, its packages lacked `py.typed` markers. Use the project venv and compare actual CI diagnostics; do not mask the temporary-environment errors with global ignores.

Split source fixes by file ownership in separate worktrees; preserve broad catches only where a deliberate per-item/transport/finalization boundary must stay alive, with a narrow documented `noqa: BLE001` if unavoidable and a fallback test. No blanket `ignore_missing_imports` or weakened mypy strictness. After integrating reviewed behavioral/type fixes, apply `ruff format src/ tests/` in one dedicated formatting checkpoint, check the complete diff for changed literals/comments, then rerun lint, format check, mypy, the full Python 3.11 suite, and GitHub CI. Do not claim CI green from local success alone.

## Integrated local result (pending remote checks)

The OpenCode and delegated model workers stalled without usable commits; fixes were made and checked in the isolated PR worktree, not the owner's `main`. Strict project-venv mypy reports no issues in 135 source files, and both Ruff lint and format checks pass. The Python 3.11 suite reports 619 passed with 15 warnings. A separate local Python 3.14 run also reports 619 passed with 13 warnings. The warnings include pre-existing aiosqlite workers outliving test event loops, FastAPI `on_event` deprecations, and a pytest collection warning; passing tests do not resolve those issues.

Regression tests cover a demo scan failing closed when its LLM fails, cancellation counting as a bridge-stream failure, and the previously missing SQLite-backed `Lattice.list_open_proposals` API used by the governance route. The formatter changed 88 files; review the resulting formatting-only diff as well as the behavioral edits. Remote CI and the separate security review remain required before anyone marks PR #7 ready or merges it.

## Next feature (not in this slice)

Authenticated project/thread API needs its own design spec, threat model, acceptance tests, documentation checkpoint, and owner approval before implementation. Skill/workflow runtime wiring follows that boundary.
