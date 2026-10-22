# Historical bounded actual-route qualification

This is an archived, baseline-bound harness. The permanent worktree paths below
were retired on 2026-10-22; they are historical provenance, not current execution
instructions. Read `CONSOLIDATION_NOTE_20261022.md` before attempting recovery or
review. This harness does not qualify the newer consolidated integration tree.

This is an offline qualification harness, not a pilot feature or a runtime
readiness check. Only an exact **reviewed source/dependency manifest** may reach
Agency imports, app construction, dynamic inventory or HTTP cases. A changed
checkout is not automatically safe to import merely to inventory its routes.

## Run (existing Python 3.11 project interpreter; no install)

```bash
PY=C:/Users/alphi/theagency/.venv/Scripts/python.exe
HARNESS=C:/Users/alphi/agency-worktrees/daily-harness-20261020/workspace/daily-route-qualification
SOURCE=C:/Users/alphi/agency-worktrees/daily-harness-20261020
export PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1
"$PY" -B "$HARNESS/selfcheck.py"
"$PY" -B "$HARNESS/qualify_routes.py" --source-root "$SOURCE" --label qualification
```

`selfcheck.py` runs both the original inventory/guard checks and `test_safety.py`.
To select the new regressions alone, from the harness directory run
`"$PY" -B -m unittest test_safety -v`. Harness-only fixtures are synthetic evidence,
never product receipts. The worker's isolated, source-bound Python 3.11 controller
and exact RED/GREEN executions are retained externally in
`C:/Users/alphi/agency-evidence/daily-driver-20261020/harness-worker/`.

Each qualification invocation creates unique evidence under
`C:/Users/alphi/agency-evidence/limited-autonomy-20261019/daily-routes/`.
Source mismatch/review refusal writes `receipts.json`, exits **2**, records zero
cases and explicitly records that Agency import was not attempted. It does not
invent an inventory for the rejected source. Admitted runs also write
`inventory.json`, retaining source HEAD/branch/status, before/after per-file and
aggregate fingerprints, harness hash, interpreter/versions and UTC times.

Exit codes:
- **1**: an admitted run has case/inventory/containment/stability/cleanup failure.
- **2**: review refusal, or passing bounded cases still have missing **or
  present-but-unqualified** planned pilot surfaces, unexercised surfaces or skips.
- **0**: no such bounded gaps. This is not daily-driver certification;
  `daily_driver_qualified` always remains false. The current harness never marks
  planned pilot routes qualified merely because they exist or return a denial,
  so its nine planned pilot gates remain open until functional qualification is
  deliberately implemented and reviewed.

## Pre-import safety admission

`REVIEWED_SNAPSHOT` is the complete 165-file manifest copied from the independently
reviewed, archived baseline, not generated from the target at runtime. It covers
all `src` code (including module initialization, helpers and imported handler/
config/profile constructors), static assets, `pyproject.toml`, `uv.lock` and the
frozen owner-pilot spec. The dependency declaration/lock bytes are pinned along
with the recorded FastAPI, Starlette, HTTPX and Pydantic versions. Those four
installed versions are checked without importing Agency; this is not a binary
attestation of every installed third-party package.

Installed-version checks and this source manifest are intentionally fail-closed.
The embedded 165-file manifest pins the archived **Windows CRLF checkout bytes**;
independent review reconciled them with commit `97c72f2` after its checkout
line-ending conversion. An LF checkout will intentionally fail admission, even
if the normalized Git source is otherwise equivalent. Do not silently normalize
or replace these reviewed pins during qualification.

The original source aggregate pin is
`8ee783c25eeee14b2a04b68118bc8888a87df8b4a14d3d66c3f6109d91cdb405`.
`BASELINE_METADATA` separately records historical provenance/counts. A later
HEAD/branch/status is observational metadata, not review approval. Added, removed
or changed source/static/lock/spec bytes, or a pinned dependency-version mismatch,
fail closed **before any Agency import or factory/constructor invocation**.
Factory AST pins remain secondary diagnostics/case gates, not the safety boundary.

There is deliberately no target-supplied manifest, automatic approval/generation
command, or qualification-time “accept current source” option. Requalifying a
later tree requires the parent reviewer to review its complete source/dependency
closure and deliberately update the trusted harness pins and any affected probes
outside a qualification run. Preserve this original baseline/archive when doing
so; a before/after-stable new tree alone is not approval. This worker did not
approve a later integration tree or rerun product qualification.

## Fingerprinting and containment

Before reading any source bytes, the harness classifies the entire candidate
file set using a code/static/explicit-metadata allowlist. It rejects `.env*`,
database-shaped files (including WAL/SHM/journal sidecars), runtime/data/log/cache
directories, unknown file types, special files and symlinks/Windows reparse points.
Directory traversal never follows links; path components are checked before
traversal and reads. Python cache directories/bytecode are excluded. Static
inventory uses the same no-follow classification before hashing assets.
Synthetic audit-hook regressions assert **zero open events**, not just an eventual
post-read failure, for each sensitive/runtime fixture independently and for the
qualification entrypoint.

Read-only git discovery finishes before the process guard prohibits child
processes. Environment sanitization precedes fingerprinting and Agency imports:
inherited credentials/provider/proxy/database settings are discarded;
`PYTHON_DOTENV_DISABLED=1`, explicit echo and disabled Butler routing are set.
The event loop is created before the Python audit guard on Windows; the guard is
installed before preflight fingerprint reads. CWD, HOME/config paths, data and
temp directories are isolated under unique harness scratch runs. The guard
rejects network/process operations, dotenv/non-scratch database opens and writes
outside the current scratch/evidence run. It is **not an OS sandbox**, hostile
Python escape audit or provider test.

## Inventory and evidence semantics

- Installed FastAPI's `iter_route_contexts(app.routes)` expands lazy routers.
  Inventory unions effective HTTP methods/paths with actual `app.openapi()`;
  hidden routes, generated docs/OpenAPI/redirect routes and HEAD are retained.
  OpenAPI discrepancies and unsupported child mounts fail explicitly.
- Static mounts have one `*` dispatch row plus exact GET/HEAD asset rows with
  hashes. These are not a count of business endpoints.
- Receipts link cases to inventory IDs and separately count responses, passing
  assertions and direct safe-handler checks. A middleware denial is not proof
  that its underlying business handler works.
- The eight frozen pilot API method/path pairs and `GET /pilot` are checked
  independently. Missing routes are `MISSING`, never passing 404s. Existing routes
  are `present-unqualified`, not readiness passes; `/assets/pilot.html` does not
  imply `/pilot` or its API exists.
- No app lifespan, daemon, real model request, valid Telegram update, arbitrary
  resource operation or privileged task runs. Telegram uses the real handler and
  an in-memory profile. Clients/handler/profile close via `AsyncExitStack`, with
  closure checked. HTTP uses `httpx.ASGITransport` and a synthetic loopback peer,
  not a live listener. Cold health responses remain degraded/starting.

## Retained observed baseline — not rerun by this repair

Original source base `8d0257c26a1b7c1cdea14036e78eb6878e2628de`, harness review
commit `97c72f29f3fa7d69421e9d0ab72ccf820f3b3ba3`:
**72 expanded surfaces** (Agency 32, Butler 12, prototype 18, Telegram 10),
**94/94 cases passed**, 72 surfaces received responses, **28** had direct
safe-handler checks and **44** remained boundary-only. All **8 pilot API pairs
and `GET /pilot` were missing**; the recorded process exit was **2**.

Keep all original attempts, including the first harness HEAD/JSON assertion
failure, untouched. The final archived inventory/receipts/verification are under
`limited-autonomy-20261019/daily-routes/final-97c72f2-20261019T190730Z-d8565882/`;
the independent review and reproductions are under
`limited-autonomy-20261019/review-daily-routes/`. No full product suite, live API,
provider, browser-route or integrated-source qualification was performed by this
bounded harness repair; those remain the parent's separate qualification scope.
