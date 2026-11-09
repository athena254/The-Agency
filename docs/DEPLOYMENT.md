# Deployment artifacts

Status: **repo-side artifacts repaired; not verified on a Docker host or against the Simular box.** This document is the contract for `Dockerfile`, `docker-compose.yml`, the GitHub workflows and the box deployment scripts. It does not authorize deployment.

## What runs

The deployable process is the FastAPI/uvicorn application in `src/agency/api/server.py`:

```
uvicorn agency.api.server:app --host 0.0.0.0 --port 8000
```

* `agency.api.server` is an **ASGI app**, not a module runner. `python -m agency.api.server` creates the app and exits; it must be served by uvicorn.
* The only public route is `GET /v1/health`. Every other `/v1/*` business route is closed with HTTP 503 until peer authority exists (`deny_unverified_authority` middleware in `server.py`).
* The health probe is therefore `GET http://127.0.0.1:8000/v1/health` (not `/health`).
* Storage is SQLite via aiosqlite (`AGENCY_EVIDENCE_DB`, `AGENCY_MEMORY_DB`) plus the Lattice SQLite store (`LATTICE_SQLITE_PATH`). **Redis and PostgreSQL are not used by the runtime** and are deliberately absent from the image and compose file.

## Environment variables

| Variable | Used by | Notes |
|---|---|---|
| `AGENCY_LLM_PROVIDER` | `agency.config.settings` | `openai`, `anthropic`, `ollama`, … |
| `AGENCY_LLM_MODEL` | `agency.config.settings` | model id |
| `AGENCY_LLM_API_KEY` | `agency.config.settings` | required unless provider is keyless (`ollama`) |
| `AGENCY_LLM_BASE_URL` | `agency.config.settings` | custom OpenAI-compatible endpoint |
| `AGENCY_LOG_LEVEL` | `agency.config.settings` | default `INFO` |
| `AGENCY_EVIDENCE_DB` | `api/server.py` | SQLite path; default `:memory:` |
| `AGENCY_MEMORY_DB` | `api/server.py` | SQLite path; default `:memory:` |
| `LATTICE_SQLITE_PATH` | `agency.lattice.config` | Lattice SQLite path (overrides `config/lattice.yaml`) |
| `TELEGRAM_BOT_TOKEN` | `src/telegram_bot.py`, `agency.telegram.config` | **not** `AGENCY_TELEGRAM_BOT_TOKEN` |
| `AGENCY_BETA_MODE` | `agency.telegram.config` | keep `0`; beta model/tool paths stay disabled |
| `TELEGRAM_POLL_DB_PATH` | `src/telegram_bot.py` | persistent poll cursor |
| `TELEGRAM_BETA_BUDGET_DB_PATH` | `src/telegram_bot.py` | required in beta mode |

`AGENCY_CONFIG_PATH` is **not** read by the code; `config/lattice.yaml` is resolved relative to the working directory (`Path("config/lattice.yaml")`). Do not set it.

## Docker image

```bash
docker build -t theagency/api:latest .
docker run --rm -p 8000:8000 \
  -e AGENCY_LLM_PROVIDER=openai \
  -e AGENCY_LLM_API_KEY=... \
  -v "$PWD/data:/app/data" \
  theagency/api:latest
```

The image runs as uid/gid 10001, has a read-only root filesystem expectation (persistent state goes on `/app/data`), and its `HEALTHCHECK` hits `/v1/health`.

## Compose

```bash
docker compose up -d                 # API only
docker compose --profile telegram up -d   # API + optional Telegram bot
```

The Telegram bot is a separate process behind the `telegram` profile. It reads `TELEGRAM_BOT_TOKEN`; compose refuses to start it when that variable is unset. It is not started by default and is not an accidental side effect of an API deploy.

## Box scripts

The Simular box keeps its deployment at `/workspace/deploy_agency.sh` and `/workspace/start_pilot.sh`. The box was unreachable in this pass, so the canonical contract is recorded here and the repo-side references live in `scripts/`:

* `scripts/deploy_agency.sh` — refuses a dirty `src/`+`tests/` tree, builds the image from the current commit, starts only the API service, and waits for `/v1/health` before reporting ready. It requires `AGENCY_LLM_API_KEY` unless the provider is `ollama`, and never prints secret values.
* `scripts/start_pilot.sh` — runs `agency.prototype.pilot_launcher` with an explicit `--data-dir` and `--config`; the launcher verifies ACLs/permissions and never reads `.env`. The first run must be without `--check` so `owner.token` can be created.

Before relying on the box, diff its `/workspace/*.sh` against these references and confirm the box uses the same health endpoint and token variable name.

## Exact-revision CI

The gate set is: `ruff check`, `ruff format --check`, strict `mypy src/`, the hermetic `pytest tests/` suite, and `node --test tests/*.test.mjs`.

* `.github/workflows/ci.yml` runs that set on pushes and PRs (Python 3.11/3.12 on Ubuntu/Windows, plus a Node job and the security-test subset).
* `.github/workflows/cd.yml` first runs the same set at a **pinned revision** (the tagged commit, or the SHA supplied to `workflow_dispatch`) on Ubuntu and Windows, and only then builds the wheel/sdist and the container image. The image is labelled with `org.opencontainers.image.revision`.

To certify an arbitrary revision, run `CD` via `workflow_dispatch` with the full SHA in the `revision` input. A tag push certifies the tagged commit automatically.

## Verified in this pass

* `bash -n` passes on both scripts.
* YAML parses for both workflows and the compose file.
* `uv lock --check` passes; `uv export --frozen --extra dev` resolves pinned tool versions.
* The Node UI tests pass locally (`node --test tests/*.test.mjs`, 79 tests).
* The health endpoint, module runner and env-var names above were read from the source.

## Not verified in this pass

* `docker build` / `docker compose up` — Docker is not installed on this host.
* The Simular box scripts and any live container start.
* The GitHub workflows themselves (no runner available here); action versions were chosen from the versions already used in this repository.
