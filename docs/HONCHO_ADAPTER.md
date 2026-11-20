# Honcho memory adapter

Honcho is **optional and off by default**. The default SMS memory is unchanged.

## Enable

Install the pinned extra and configure a separate Honcho service with
PostgreSQL/pgvector:

```bash
pip install -e ".[honcho]"
```

```env
AGENCY_MEMORY_BACKEND=honcho
AGENCY_HONCHO_ENABLED=1
AGENCY_HONCHO_URL=http://localhost:8000
AGENCY_HONCHO_API_KEY=...
AGENCY_HONCHO_WORKSPACE=the-agency
AGENCY_HONCHO_TIMEOUT=5
```

`docs/../docker-compose.honcho.yml.example` shows a pinned local stack.

## Behaviour

| Concern | SMS (default) | Honcho enabled |
|---|---|---|
| Durable write | Always | SMS first, best-effort Honcho mirror |
| Read | SMS | Honcho first, SMS fallback |
| Isolation | SQL-bound principal | One workspace, peer per principal, same scope guard |
| Consistency | Immediate | Eventually consistent (deriver) |
| Outage | Source of truth | Falls back to the same principal's SMS rows |

The adapter never imports Honcho server code; it only calls the pinned
`honcho-ai` SDK over HTTP. A missing SDK, incomplete config or unreachable
service degrades to SMS; it never breaks the default runtime.

## Ops and upgrades

- Pin the SDK version in `pyproject.toml` and the server image by digest in the
  compose example.
- Upgrade via a dedicated PR: contract tests (`tests/test_memory_honcho.py`),
  isolation tests, then a live smoke test against the pinned stack. Record
  evidence under `docs/research-evidence/`.
- Do not track upstream `main`; Honcho is not forked.
