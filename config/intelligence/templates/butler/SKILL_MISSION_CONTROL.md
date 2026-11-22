---
type: SKILL_MISSION_CONTROL
schema_version: 1
---

# Skill — Mission Control (operator UI)

**What it is.** The Agency's operator surfaces: a prototype app (chat + IDE
views, non-pilot) and pilot mode (threads, tasks, verification receipts).
Status: prototype/pilot quality — honest locked panels where backend gates
are missing.

**Launch recipe (verified working, 2026-11-21):**

1. Prototype app (chat works with a real provider via `.env`):
   `PYTHONPATH=src .venv/Scripts/python.exe -m uvicorn agency.prototype.app:create_app --factory --port 8001`
2. Main API: `agency start --port 8000`
3. Pilot mode: `python -m agency.prototype.pilot_launcher --config <data-dir>/owner.json --data-dir <data-dir> --port 8766`
   - The config MUST live inside the data dir.
   - Restrict the data dir: `icacls <data-dir> /inheritance:r /grant:r "USER:(OI)(CI)F"`.
   - The pilot bearer token lands at `<data-dir>/owner.token`.

**What the user can do there.** Non-pilot: chat with the Butler (session
continuity within a session; each session is independent). Pilot: create
threads (auto request-ID), submit calculator tasks and watch TASK RESULT →
VERIFICATION RECEIPT → usage accounting, list tasks, coding tasks, see
honest locked/gated states (Memories/Evidence/Skills/Lattice/Forge name
their gate: P1 identity+audit, P2 peer authority, P3 isolation, P4
capability lifecycle).

**Known limits (say these honestly).**

- Ordinary beta chat/research/model calls and tools stay policy-disabled.
- The calculator dispatches through the model path: with the echo provider a
  task can soft-lock as UNCERTAIN — recovery is "Request cancellation" in
  the /pilot workspace.
- The Lattice page states the truth: single-process SQLite prototype, not
  consensus.
- Session chat history is in-memory per session (cleared on shutdown, never
  persisted).

**Guidance.** Walk a new user: run the prototype app → send one chat message
→ open pilot mode → submit a calculator task → read the verification
receipt together. Explain that a receipt is accounting evidence, not
independent verification.
