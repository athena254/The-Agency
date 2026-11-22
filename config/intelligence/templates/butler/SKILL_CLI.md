---
type: SKILL_CLI
schema_version: 1
---

# Skill — CLI operation

**Butler CLI** (`src/agency/butler/cli.py`, run as `butler …`):

- `butler start` — launch the Butler HTTP service (`--daemon` for background).
  Binds `BUTLER_HOST`/`BUTLER_PORT` (default 127.0.0.1:8001).
- `butler stop` — stop a daemonised server.
- `butler status` — health via `GET /v1/health`.
- `butler agents` — list registered agents via `GET /v1/agents`.
- `BUTLER_SERVER_URL` overrides the target server; `--server-url/-u` overrides
  per call.

**Butler configuration** (env / `.env`, prefix `BUTLER_`): `host`, `port`,
`llm_provider` (`custom` for any OpenAI-compatible endpoint, `ollama`, `none`),
`llm_model`, `llm_base_url` (full chat-completions URL), `api_key_env_var`
(default `LLM_API_KEY`), `max_message_length`, `timeout`,
`decision_provider` (`jev`|`none`) + `jev_model`/`jev_signal_threshold`/
`jev_uncertain_low`, `persona_enabled`, `persona_max_output`.

**Agent execution LLM** (shared adapter, prefix `AGENCY_LLM_`):
`AGENCY_LLM_PROVIDER` (e.g. `openai` for OpenAI-compatible endpoints),
`AGENCY_LLM_MODEL`, `AGENCY_LLM_API_KEY`, `AGENCY_LLM_BASE_URL` (root, the
adapter appends `/chat/completions`), `AGENCY_LLM_PROXY` if needed. Without
these, agents run the deterministic echo backend — say so if replies look
echoed.

**Main Agency API**: `agency start --port 8000` — bearer-token auth over
`/v1/tasks` and `/v1/agent-requests`; default-deny otherwise.

**Conventions.** Run Python as `.venv/Scripts/python.exe` with
`PYTHONPATH=src` from the checkout root. `.env` is gitignored — secrets
never enter docs, prompts or the repository.

**Troubleshooting.**

- `butler status` fails → is the server started? right port? loopback?
- Replies look echoed → no `AGENCY_LLM_*` configured (echo backend).
- Routing looks keyword-only → `BUTLER_LLM_PROVIDER` unset or key missing.
- "Message rejected" → governance length limits.
