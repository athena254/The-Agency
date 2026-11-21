# Owner HTTP guard handoff — October 19, 2026

## Delivered boundary

Branch `feat/owner-httpguard-20261019`, based on `8d0257c26a1b7c1cdea14036e78eb6878e2628de`.
Only these repository files are added:

- `src/agency/prototype/owner_guard.py`
- `tests/test_owner_http_guard.py`
- this handoff

Public API (the existing FastAPI `Request` is Starlette's `Request`):

```python
async def guard_owner_request(
    request: Request,
    authenticator: OwnerAuthenticator | None,
    *,
    csrf_token: str | None,
    write: bool = False,
) -> tuple[OwnerPrincipal, dict[str, Any] | None]:
    ...
```

The guard uses the **real** `OwnerAuthenticator`, `OwnerPrincipal` and fixed-message `PilotError`. No runtime/service construction, app import, route, provider, memory, database, Telegram identity, CORS middleware or forwarded-header trust is added. Existing app helpers were inspected but not imported: they are app-coupled and accept looser Host/Origin forms than this contract permits.

Every call requires a concrete authenticator, a numeric loopback connection peer, one strict loopback Host authority, and an exact bearer. An optional Origin must match HTTP(S) scheme, canonical hostname and effective port; localhost and numeric loopback aliases are not interchangeable origins. IPv6 brackets, valid ports, default ports and case normalization are covered. Missing Origin is not an authentication bypass. Reads return `(principal, None)` and never call receive, `.body()` or `.json()`.

Writes additionally require exact `X-Prototype-CSRF`, `application/json` (optionally UTF-8 charset), and a streamed UTF-8 JSON object of at most **32768 bytes**. Declared oversize fails before receive; actual chunks are independently counted before copying, and a supplied Content-Length must match the final byte count. Long decimal lengths cannot overflow integer parsing. Duplicate/malformed security headers fail closed, including case-insensitive duplicate lookup. Encoded bodies other than identity, ambiguous transfer framing, non-object JSON, malformed UTF-8, non-JSON NaN/Infinity and receive failures are rejected. Cancellation propagates rather than being swallowed.

Errors remain fixed `PilotError` values: disabled/configuration **503**, bearer **401**, transport/origin/CSRF **403**, oversize **413**, media **415**, shape/framing/receive **422**. The guard neither logs inputs nor incorporates exception bodies in errors.

## Qualification and evidence

Project interpreter: `C:/Users/alphi/theagency/.venv/Scripts/python.exe`, Python 3.11.16.

Evidence directory:
`C:/Users/alphi/agency-evidence/limited-autonomy-20261019/owner-http-guard/`.

- `22-focused-final.txt`: **120 passed**, guard-only test file.
- `23-auth-seam.txt`: **121 passed**, the same 120 plus the existing local-bearer authentication test only. No Telegram-principal test was run.
- `23-ruff-final.txt`: worktree-root Ruff check and format check, both affected Python files, passed.
- `22-mypy-win32-final.txt` and `22-mypy-linux-final.txt`: strict affected-source mypy, both targets passed; dependency imports followed silently. Linux is a **mypy target**, not an executed Linux runtime.
- `22-cold-import-final.txt`: fresh exact-source import passed under the isolation guards described below.
- Raw numbered RED/GREEN receipts cover incremental disabled/read/auth/peer/Host/Origin/CSRF/media/JSON/size/framing/fault behaviors. Tests use a test-only real FastAPI ASGI endpoint and real guard/authenticator, not a production service mock. Additional positive/compatibility regressions are not claimed as new behavioral REDs.
- `qualification-runner.py` and `qualification-manifest.json` preserve the harness, command scope and source fingerprints outside the checkout.

Every pytest/mypy/cold run disables dotenv **before Agency imports**, selects explicit echo settings, removes credential-like environment variables, uses this worktree's absolute source/test paths and isolated scratch CWD/data/temp. External Python socket connections/DNS are denied while numeric loopback remains allowed for event-loop plumbing. The final harness additionally audits native SQLite opens and socket events. This is Python-process containment, **not** an OS sandbox or arbitrary-native-child network guarantee. No installs, providers, daemons, runtime DBs, private dotenv files, full suite, push, merge or deployment were used.

### Retained corrections and limitations

- The first disabled RED was a test-time missing-module failure; later REDs were behavioral failures against the preceding implementation.
- An initial safe-write refusal left the test file unchanged; the accidentally premature two-test run labeled `03-auth-red` was superseded by the real auth RED receipt. The refusal/premature result remains in the session tool trace, not as a separate evidence file.
- The initial length test incorrectly treated a long leading-zero value as malformed. That unsupported premise was replaced by actual CRLF malformation before the length grammar repair; both original and corrected RED receipts remain. Leading zeros are supported.
- Raw-header validation initially changed two existing media/length error codes; the mapped fixed codes were restored. A positive boundary fixture initially measured 32770 bytes, not 32768; its padding was corrected using an actual interpreter calculation. These failing runs are retained rather than described as product regressions remaining in the final tree.
- Initial Ruff/mypy diagnostics were corrected (explicit bytes typing; a narrowly documented broad receive-exception sanitizer). Final gates pass.
- The original cold-import harness overclaimed that no legacy app module would load. **Existing** `agency/prototype/__init__.py` eagerly re-exports `create_app`, so importing any leaf loads legacy `app` and its transitive dependencies. The failed probe is retained as `21-cold-import.txt` (its failed assertion was not flushed as a traceback). The corrected check proves exact-source import and no direct guard-to-app/service dependency, not absence of that inherited eager package import. The unowned initializer/app were not changed.

## F3 integration obligations — not performed here

1. Call this helper before FastAPI body-model parsing or any middleware/body consumer. Pass `write=True` on writes; consume its returned object rather than reparsing the stream.
2. Supply the real configured authenticator and process CSRF value. Do not add a fallback principal/service or accept credentials in URL/cookies/static source.
3. Translate `PilotError` using the frozen owner error schema and apply no-store response headers in the owning router. The ASGI exception handler in this test file is **test-only**, not a shipped owner router.
4. Preserve the actual connection peer and scheme: disable proxy-header rewriting on the supported local server; do not install middleware that trusts Forwarded/X-Forwarded claims.
5. Keep bootstrap/session ownership separate, unsafe ingress closed, and require parent integration/independent review plus actual route/server qualification. These tests are in-process ASGI checks, not a bound TCP server, browser trial, live-provider test or rollout approval.
