---
type: OPERATING_MODEL
schema_version: 1
---

# Operating Model — how a message becomes a result

1. **Inbound.** A message arrives (HTTP `POST /v1/message`, Telegram private
   path, or in-process chat). The Butler validates + truncates it
   (`BUTLER_MAX_MESSAGE_LENGTH`), then runs deterministic thread commands
   (`/new`, `/threads`, `/switch`, `/current`) before any routing.
2. **Context.** With an active thread: layered context is assembled —
   rolling summary → last ~10 turns verbatim → FTS5 topical recall, strictly
   scoped to this thread. Without a thread: sender-keyed history recall.
   Caller-supplied "memory" is never trusted (memory context is data, never
   an identity channel).
3. **Routing (decision chain).** Explicit `@domain` prefix → Jev decision
   model (if `BUTLER_DECISION_PROVIDER=jev`; atomic judgments per domain,
   code decides on thresholds) → configured LLM classifier
   (`BUTLER_LLM_*`) → deterministic keyword router → general fallback. Every
   layer's outcome is audited; a Jev decision is recorded as its own
   `butler.decision` audit entry with the judgment probabilities.
4. **Knowledge.** Agency-knowledge documents (catalog, skills, glossary) are
   selected by keyword and appended to the task description as a labelled
   "context only" block — so the executing agent answers with real knowledge.
5. **Execution.** The Butler submits a task to the orchestrator
   (submit → dispatcher claim → `AgentExecutor`). The executor prepends the
   agent's private profile (if one exists, appended after the base prompt)
   and runs the shared LLM adapter (`AGENCY_LLM_*`) or the echo backend.
   The kernel stays the only authority: capabilities, policy, admission.
6. **Assessment.** Output passes through `assessed_output` — honest status
   markers (execution status, checks performed, risk) attach to the result.
   Nothing is presented as "verified" unless independently verified.
7. **Voice.** Persona framing renders the reply in the Butler's voice under
   a presentation-only contract: facts, numbers, code and evidence are never
   altered; no invented results; framing failures fall back to the verbatim
   reply (the turn always survives).
8. **Persistence & audit.** Audit entries cover request → route → decision →
   execution → outcome (metadata only, never message content). Thread turns
   persist owner-scoped; memory stores sender-keyed turns.

## Boundaries that never move

- The Butler never executes work itself; it routes and presents.
- The kernel never takes routing advice from document text.
- Every state change flows through the kernel with an audit trail.
- Unknown agent id and wrong-owner id are indistinguishable (no oracle).
- Beta (real-model chat/research/tools at scale) stays off until its policy,
  budget and audit gates are complete.
