# Supported owner-pilot launcher — implementation contract

Implements criterion3 of DAILY_DRIVER_COMPLETION_20261020.md; existing owner pilot/service/API wire specs remain normative. Current user explicitly authorizes finishing the daily-driver path. No push/PR/main merge/public daemon/paid fallback.

## Interface and ownership

`python -m agency.prototype.pilot_launcher --config <absolute-json> --data-dir <absolute-private-dir> [--port 8766] [--check]`

Only launcher module, minimal lazy prototype package export, new launcher tests and user how-to are worker-owned. App/router/service/store/Butler/orchestrator/LLM/interfaces are parent-owned, not worker-editable. Consume `create_app(butler=None,*,pilot=None,authenticator=None)`; pilot lifespan starts/stops only pilot. No private substitutes.

JSON exact allowed fields: owner_id, subject_id, provider, model, base_url, api_key, max_tokens, temperature, timeout. Owner/subject/model/base_url/provider required; explicit provider=openai only (no echo success-shaped production mode or provider fallback). Key SecretStr private, optional only if snapshot permits actual endpoint. Safe bounded defaults max_tokens1024, temperature0.2, timeout45. Reject invalid/nonfinite/upward timeout values through real frozen snapshot before stores/token/server. Loopback OpenAI-compatible endpoint remains supported for genuine local providers and synthetic qualification; external HTTPS only per existing snapshot. Do not auto-discover .env or provider presets.

`--check`: validate CLI, configuration, paths, source and readiness prerequisites read-only. No mkdir, chmod/ACL rewrite, token provisioning, SQLite open/recovery, listening socket or inference. Report configuration_valid, never live-provider verified. Any existing token is not printed. Missing/unprotected prerequisites must be clear failure rather than silently provisioned.

Start: owner-selected isolated data directory only, never canonical repository data. Reject links/reparse aliases and unsafe existing file types. Config must be private; preferably require it directly beneath isolated data directory. Ensure directory/file inheritance protects config/token/DB/WAL/SHM/lock. Windows chmod is NOT an ACL implementation: establish and verify private ACL using native facilities (owner/SYSTEM/Administrators only) or refuse safely. POSIX private modes. Create high-entropy `owner.token` exclusively if absent, never rotate/replace existing token, never print token/key/config body in stdout/log/errors/URLs. Expose only its path to owner. No credentials in process arguments. Acquire actual OwnerPilotStore lock BEFORE caller-owned ThreadStore constructor writes shared pilot.db. Quota/audit separate files there. Fresh objects per launch/restart.

Disable dotenv before transitive app imports: package __init__ must become lazy/minimal while preserving exported create_app. Supply explicit inert legacy LLMAdapter(echo) and :memory: legacy memory. Construct ButlerConfig with _env_file=None AND explicit every field to prevent inherited environment reads. Never call legacy Butler/orchestrator start/stop; no seed/Lattice/global memory/mirror/poller.

One process, one event loop, one worker, literal127.0.0.1, no reload, proxy_headers=False, no inherited proxy-peer trust. Serve app object with uvicorn.Server in same loop after owned store acquisition; ThreadStore init/use/close same thread. Cleanup every partially initialized resource, retaining/draining actual operations. Caller-owned ThreadStore always closes even if pilot cleanup fails. No separate broad app factory/start command. Ctrl+C supported; stop/restart instructions use fresh process. Do not clear uncertain rows/audit automatically.

## Verification

Strict vertical RED->GREEN for CLI/check/start failure/cleanup behavior. Use own3.11 source-bound scratch tests, dotenv disabled, secrets stripped, HTTP leaves only scripted, network denied except explicitly synthetic loopback subprocess/browser tests. Prove --check creates/writes nothing and does not load dotenv; reject echo/public bind/bad config/canonical paths/links; private token creation/reuse and Windows ACL actual readback; real store lock before ThreadStore mutation; second live worker refuses; fresh process preserves history and uncertainty. No actual private config/provider or daemon during worker tests. Focused tests + root Ruff/format + affected strict Windows/Linux-target mypy, no concurrent full suite. Keep original failures, exact commands/results/gaps. Parent reviews/integrates and runs end-to-end independently.
