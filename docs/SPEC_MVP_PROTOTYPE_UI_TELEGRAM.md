# Working MVP prototype: local UI, Butler, Telegram

Status: implementation contract approved by the owner's explicit request to start work; **not** release approval or proof that the existing Telegram beta is safe. Base: draft beta foundations at `b098776` (PR #9), which itself branches from merged `main` `4cf28a7`. This document supersedes that beta spec's **"No GUI"** constraint for this new prototype only; every still-open security gate in `BETA_GAPS.md` remains open. The separate dirty `theagency` checkout and running `main` bot are not build workspaces.

## Plain-English outcome

A person on this computer opens a local browser page, sees live Agency/Butler status and agents, types a message, and sees a real reply or an honest error. The same Butler engine serves a private Telegram chat through the existing polling connector. The UI does not pretend that echo tests are a working live model. It must not make the unsafe beta tools, agent creation, or public API available by accident.

## Source map and decisions

- Existing Python FastAPI/Butler: `src/agency/butler/server.py`, `service.py`, `src/agency/orchestrator.py`; `src/agency/telegram/{handler,adapter,config}.py` and `src/telegram_bot.py` already implement polling and deterministic commands. `tests/test_butler*.py`, `test_beta_telegram_*.py` cover prior behavior.
- `docs/SPEC_L2_TELEGRAM_BETA.md` and `docs/BETA_GAPS.md` record admission, delivery, tool-boundary, audit, privacy, SSRF and deployment blockers. A UI is an additional surface, not a bypass around these gates.
- There is no tracked JS/Electron frontend in this branch. Use a small FastAPI-hosted HTML/CSS/vanilla-JS UI, not a new build chain. [FastAPI static files](https://fastapi.tiangolo.com/tutorial/static-files/) support same-origin static assets; avoid a second local web server and wildcard CORS. Telegram [getUpdates and webhooks are exclusive](https://core.telegram.org/bots/api); keep the existing single poller, never start a second poller against the live token for tests.
- Coding agents: use Cline CLI free models and Freebuff (signed in), in separate Git worktrees. MiMo-V2.6-Flash free was smoke-tested successfully through Cline; prefer it for UI. An agent's completion message is not test evidence.

## MVP scope and contracts

1. New module `src/agency/prototype/app.py` exports `create_app(butler: ButlerService | None = None) -> FastAPI`. It mounts static assets under `/assets` from `src/agency/prototype/static`, and serves `/` from that directory. Its lifespan starts exactly one ButlerService and stops it cleanly, using a caller-injected service in tests. The application binds **127.0.0.1 only** when started by the documented command; never expose it publicly or reuse the unauthenticated, wildcard-CORS Butler HTTP server as the prototype surface.
2. Same-origin API: `GET /api/health` returns `{status, butler, agents}` from running service; `GET /api/agents` returns `{agents: [{id,name,domain}]}` from current registered agents; `GET /api/session` returns `{csrf_token: string}` from a per-process random secret only to accepted local Host requests; `POST /api/chat` accepts JSON `{message: nonempty string}` (max configured Butler message length) plus `X-Prototype-CSRF` header, returns `{response: string}` or an explicit unsuccessful HTTP status and sanitized `{error: string}`. Reject non-loopback Host, mismatched Origin, absent/incorrect CSRF header, and non-JSON content type before the Butler call. Do not accept a caller-provided sender, trusted actor, tool grant, workspace ID, or arbitrary agent ID. The server uses its own constant local-session sender, with memory disabled until there is a real local identity boundary; do not derive owner identity from an HTTP header. No raw message, token, URL-with-token, or model output in logs.
3. Browser page: responsive accessible chat layout, health/agents indicator, text area, submit button, loading/failed states, stable scroll history in the current tab, keyboard submit (Enter; Shift+Enter newline), and safe DOM rendering via `textContent` (not untrusted `innerHTML`). No keys in JS, no CDN or externally fetched assets, no fake prefilled agent data, no claim of persistence when memory is disabled. Same-origin `fetch('/api/...')`: obtain `/api/session` first, then send its CSRF token only in `X-Prototype-CSRF` for chat. Respect reduced motion; work on narrow mobile viewports. Frontend assets are disjoint from Python code.
4. Telegram lane: preserve owner-only per-user names, private-chat admission in beta, safe deterministic commands and durable polling offset already in PR #9. Add hermetic connector contract tests that verify a simulated Telegram update reaches the actual Butler and send adapter, or a clear refusal/error, without hitting the real Bot API. Fix only a reproduced connector defect in the owner files; do **not** enable beta, webhook mode, creation/proposal votes, or unrestricted tools. A live send/reply test requires operator-controlled rollout, not a mock claim.
5. Prototype scope excludes autonomous Forge, public accounts/API, hostile-code execution, unrestricted `web_fetch`, agent creation, L2 action grants, and unreviewed WIP audit/tool-policy work. Existing B-04/B-05/B-06/B-07/B-12/B-13/B-14/B-15 blockers remain before beta invitations. The UI may use only the same safe general conversation path; enforce no browser-supplied privilege grants at the server boundary.

## Worktrees and non-overlapping ownership

- Backend/UI API Cline lane: `src/agency/prototype/__init__.py`, `app.py`, `tests/test_prototype_app.py`; may not edit static assets or Telegram files.
- Visual UI MiMo lane: `src/agency/prototype/static/index.html`, `app.js`, `style.css` and UI-only tests if practical. It must consume only the API shapes above; may not edit Python or Telegram code.
- Telegram connector lane (Freebuff or Cline): `src/agency/telegram/handler.py`, `src/telegram_bot.py`, and `tests/test_prototype_telegram_connector.py`; may not edit prototype module or shared infrastructure. If a defect needs a different file, stop and report.
- Integration owner: reconciles workers in a separate worktree, runs all checks, builds a local loopback demo and smoke-tests its HTTP UI/API against echo, then reports what did/did not receive a *live* provider reply. No code from a WIP worktree is assumed safe merely because it exists.

## Tests and rollout gates

- Test app startup/shutdown, actual GET endpoints, POST chat success/error, invalid input, injected Butler calls, absence of caller-chosen `sender`/scope, no CORS wildcard on prototype. Host/Origin and CSRF protections for local POST must be tested, including DNS-rebinding-style Host mismatch and malicious Origin. If not solved, do not launch the UI with real credentials; keep it test-only.
- Test static root/assets return 200, UI contains no external scripts, and JS uses safe text-node rendering; browser-manual check must distinguish styling from working chat.
- Test connector with fake adapter and real Butler fake/echo: admitted private update sends once; rejected/unknown/creation cases make zero model/tool/proposal calls; failed send remains retryable. No network in CI tests.
- Run `AGENCY_LLM_PROVIDER=echo AGENCY_LLM_MODEL=echo .venv/Scripts/python.exe -m pytest tests/ -q`, Ruff check and format, mypy, and a real localhost TestClient or HTTP smoke before a working-prototype claim. Use the clean worktree venv. Commit reviewed code as checkpoints; do not commit SQLite files or secrets. Upload only reviewed commits to a draft branch and read back its exact head and CI.
- Existing live `main` bot remains on merged `4cf28a7` unless a separate safe handoff is verified. Do not start another poller with its token. An owner message to the bot is needed to confirm end-to-end Telegram replies; `getMe` alone is not enough.

## Open decisions (explicitly not guessed)

- Whether the final UI should become an Electron shell is deferred; this prototype is local browser UI.
- Whether "L2" denotes an action class or milestone remains unconfirmed; this MVP does **not** enable L2 actions.
- Actual provider/model quality and Telegram delivery cannot be established by offline echo tests. An approved live smoke and owner feedback are still required.
