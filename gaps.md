# Known gaps — requested-agent / capability / factory / skills

Found while implementing `RequestedAgentService` (`src/agency/factory/requested_agent.py`).
Ordered by production risk. **Avoid these patterns; all agents are free to fix them.**

## 1. Capability ⇄ Factory circular import (structural)
`src/agency/capability/contract.py:9` imports `canonical_sha256` from `agency.factory.coding_models`.
Because importing a submodule runs `agency.factory.__init__`, any factory module that imports
`agency.capability.*` at module load time creates a cycle. `requested_agent.py` works around it
with lazy imports inside `publish_skill` (`src/agency/factory/requested_agent.py:514`).
**Fix:** move `canonical_sha256` into a neutral module (e.g. `agency/core/hashing.py`) and re-export;
then remove the lazy-import workaround. Do NOT add module-level capability imports under `agency/factory/`.

## 2. Self-improvement can rewrite its own prompt without a distinct human gate
`improve_agent` auto-approves the refined purpose prompt and new blueprint version using the
service's fixed `approver` (`requested_agent.py:450-480`). The factory authorizer is consulted, but
the same service identity performs both request and approval; there is no separate human decision
for self-modification. **Fix:** require an explicit `authorizer(actor, "agent.self_improve")`
callback (and a different actor) before re-versioning, mirroring `agent.approve`.

## 3. Re-activation is not atomic
`requested_agent.py:478-480` calls `activate(new)` then `revoke(old)` as separate SQLite
transactions. A crash between them leaves two ACTIVE blueprints/agent identities; a failure in
`revoke` strands the old identity. Factory rollback exists inside `activate` only.
**Fix:** add an atomic `AgentFactory.reversion(...)` transaction, or an explicit reconciler on
startup. Related: runtime identities are ephemeral — `recover_active()` is manual.

## 4. Distilled / gap skills have no runnable implementation
`_new_gap_skill` (`requested_agent.py:157`) and `selfimprove/distiller.py:66` create skills with
opaque `implementation="learned.<slug>"`, but nothing registers a callable in `OperationRegistry`.
Even after promotion to PUBLISHED, `SkillExecutor.run` returns "unknown operation". The self-
improvement loop creates skills that cannot execute. **Fix:** register an implementation for each
promoted skill, or mark them explicitly as documentation-only metadata.

## 5. Publication gate is opt-in
`publish_skill` checks `AttestationStore` only when one is configured (`requested_agent.py:514+`).
With the default `attestation_store=None`, an improved skill can be promoted DRAFT→PUBLISHED
automatically. **Fix:** make attestation mandatory for any skill a factory-issued agent will pin.

## 6. No external entry point to request an agent
`RequestedAgentService.request_agent()` is only callable from Python. No CLI subcommand
(`agency forge`/`agency`), Butler command, or HTTP route exposes "make an agent from a request".
**Fix:** add a thin CLI/API adapter with an explicit authorizer and a purpose/policy allowlist.

## 7. Orchestrator improvement hook is narrow
`orchestrator.py:965+` fires only for completed tasks and checks `is_factory_issued(task.created_by)`;
with no failed skill steps the derived gaps are empty, so prompt refinement never runs on the
classic (non-tool) path. **Fix:** define how prompt-gap observations are produced by the driver/LLM
and thread them into `improve_agent(prompt_gaps=...)` from the orchestrator.

## 8. Repeated improvement can create duplicate skills
Each `improve_agent` call with gaps registers fresh `gap-*` / `learned-*` skills; only per-call
`max_new_skills` is bounded, with no cross-cycle semantic dedup. **Fix:** dedup by description
hash / capability index before registering, and cap lifetime skill count.

## 9. Registries use SQLite without WAL / shared connection discipline
`SkillRegistry`, `PromptRegistry`, `AgentFactory` open independent connections (default journal).
The orchestrator's service and an external caller can hold two connections to the same DB
(concurrent writes may lock). Forge stores use WAL; these do not. **Fix:** set WAL and document one
writer connection per process.

## 10. No live-provider qualification of this flow
Verification was offline: 16 service tests + 1 orchestrator E2E with a mocked tool driver, plus
factory/capability/skill/orchestrator suites (76 tests), ruff, format, mypy. No live LLM/provider
run of request→task→improve, no multi-restart / concurrency soak. **Do not claim production
certification** until these are exercised with the configured provider and a real agent run.
