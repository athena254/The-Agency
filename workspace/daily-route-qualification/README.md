# Bounded actual-route qualification

This is a qualification harness, not a pilot feature or a runtime readiness check.
It imports **only the supplied checkout's `src`** and constructs the real Agency,
Butler, prototype and Telegram ASGI apps. No application handler is replaced.

## Run (existing project interpreter; no install)

```bash
PY=C:/Users/alphi/theagency/.venv/Scripts/python.exe
HARNESS=C:/Users/alphi/agency-worktrees/daily-route-qualification-20261019/workspace/daily-route-qualification
SOURCE=C:/Users/alphi/agency-worktrees/daily-route-qualification-20261019
cd C:/Users/alphi/AppData/Local/hermes/cache/scratch/agency-build-20261019/daily-routes
"$PY" -B "$HARNESS/selfcheck.py"
"$PY" -B "$HARNESS/qualify_routes.py" --source-root "$SOURCE" --label qualification
```

Change `SOURCE` to a later integration checkout; do not change its `.env` or stores.
Each invocation creates unique `inventory.json` and `receipts.json` under
`C:/Users/alphi/agency-evidence/limited-autonomy-20261019/daily-routes/`.
Receipts retain exact source HEAD/branch/status, per-file and aggregate source
fingerprints (before/after), harness hash, interpreter/versions and UTC times.
The fingerprint includes `src` (including static assets), project/lock metadata
when present and the frozen owner-pilot spec. It excludes runtime data and caches.

Exit codes: **1** = case/inventory/containment/cleanup failure; **2** = bounded
cases pass but planned routes are missing, surfaces unexercised or factories
require re-review; **0** = no such bounded gaps. No exit code certifies a complete
daily driver: `daily_driver_qualified` deliberately remains false.

## Inventory and evidence semantics

- `fastapi.routing.iter_route_contexts(app.routes)` expands installed FastAPI's
  lazy included routers. Raw route types and iteration source are retained.
- Inventory unions effective HTTP methods/paths with actual `app.openapi()`;
  hidden roots, generated docs/OpenAPI/redirect routes and HEAD are retained.
  OpenAPI discrepancies and unsupported child mounts are explicit failures.
- Static mounts have one `*` dispatch row with GET/HEAD handler semantics, plus
  exact GET/HEAD rows for every current asset, including hashes. These expanded
  surfaces are **not** a count of application business endpoints.
- Case receipts link to inventory IDs. Coverage separately counts actual
  responses, passing assertions and direct safe-handler checks. Middleware
  denial is not evidence that its underlying business handler works.
- Missing planned routes are `MISSING`, not executed 404 passes. The eight frozen
  pilot API method/path pairs plus `GET /pilot` are checked independently.
  An existing `/assets/pilot.html` does not imply `/pilot` or its API exists.
- Later trees are inventoried, but an AST-changed factory's behavioral cases are
  skipped until its safety guards are re-reviewed and the pin/cases deliberately
  updated. Unknown/new routes are never automatically exercised with mutations.

## Safety and limits

CWD, data, HOME/config paths and TEMP/TMP/TMPDIR are confined to unique runs under
`C:/Users/alphi/AppData/Local/hermes/cache/scratch/agency-build-20261019/daily-routes`.
Inherited credentials/provider/proxy settings are discarded before Agency imports;
`PYTHON_DOTENV_DISABLED=1`, explicit echo and disabled Butler routing are set.
A Python audit hook rejects network/process operations, dotenv/non-run database
opens and writes outside the current scratch/evidence run. This is **not an OS
sandbox** or a provider test. The event loop predates that guard on Windows.

No application lifespan, daemon, real model request, valid Telegram update or
privileged task is run. Telegram uses its real handler and an in-memory profile
store. Clients/handler/profile are closed through `AsyncExitStack`, then closure
is checked. Health responses intentionally remain cold (`degraded`/`starting`).
HTTP is in-process `httpx.ASGITransport` with a synthetic loopback peer, not live
network/listener validation. ASGI requests cover closed APIs, auth-before-parse,
CSRF/origin/Host, validation, disabled chat, docs, exact static bytes, wrong
methods and slash redirects. Synthetic FastAPI routes exist only in `selfcheck.py`
to test the inventory algorithm; they are never product receipts.

## Observed baseline

Against base `8d0257c26a1b7c1cdea14036e78eb6878e2628de`: **72 expanded surfaces**
(Agency 32, Butler 12, prototype 18, Telegram 10), **94/94 cases passed**,
72 surfaces received responses, 28 had direct safe-handler checks. The remaining
44 surfaces have boundary-only checks, not functional handler qualification.
All **8 pilot API routes and `/pilot` are missing**. Expected harness exit: **2**.
Five harness-only selfchecks pass, including seven intentional containment denials.
The initial archived attempt exposed a harness HEAD/JSON assertion bug (8 failures);
HEAD handling was corrected and regression-tested, not relabeled as an app defect.
No full repository suite was run; unrelated core-model RED remains outside scope.
