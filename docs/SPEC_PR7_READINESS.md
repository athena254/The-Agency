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

## Next feature (not in this slice)

Authenticated project/thread API needs its own design spec, threat model, acceptance tests, documentation checkpoint, and owner approval before implementation. Skill/workflow runtime wiring follows that boundary.
