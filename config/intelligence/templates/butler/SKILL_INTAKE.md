---
type: SKILL_INTAKE
schema_version: 1
---

# Skill — Machine access (intake API)

**Who uses it.** External agents and programs — never humans chatting. The
Butler is the human gateway; machines use the intake API.

**Surface** (`src/agency/api/server.py`, started via `agency start`):

- `POST /v1/tasks` — create a task (bearer token required).
- `GET /v1/tasks` — list tasks (`?status=&limit=`).
- `GET /v1/tasks/{id}` — fetch one task.
- `/v1/agent-requests` — agent intake requests.

**Posture.** Default-deny: authed routes return 503 unless explicitly
enabled; every route requires a bearer token; bodies bounded (~32 KiB);
errors come from a fixed public catalog (no internals leaked).

**Grants on intake (landed).** The §12 grant path runs on intake routes: a
task created by an external caller can carry a signed, time-boxed,
revocable grant verified at the actuator before execution — machines can
ask, but only granted, bounded work runs.

**Known gaps (be honest).**

- Historically agent intake sat behind 503 until enabled; check health
  before promising intake works.
- The full chain "external task → executed → result returned to the
  submitter" is still partial (the result-return leg is open work — the A5
  outbox). Do not promise a submitter will receive results end-to-end yet.

**Guidance for an external integrator.**

1. Request an owner-issued bearer token (never share it in logs).
2. Create a task with a minimal, bounded description; attach the §12 grant
   if actuation is needed.
3. Poll `GET /v1/tasks/{id}` for status; treat UNCERTAIN as "needs human".
4. Expect every action to be audited with attribution.
