# Plan: Factory-managed default agents

Status: DRAFT for owner review. Builds on `SPEC_AGENT_FACTORY_V1.md`, `SPEC_PROMPT_LIBRARY_V1.md`, and `SPEC_CAPABILITY_CONTRACT_V1.md`.

## Goal
Convert the default domain agents (personal, work, business, security, research, general, blacksmith) from hand-written static `*_SYSTEM_PROMPT` constants into governed, factory-issued identities whose system prompts are versioned prompt records composed at activation. This makes their prompts auditable, versioned, attestation-gated, and improvable via the self-improvement loop, consistent with how requested agents already work.

## Current state
- Default agents ship as plain modules (`agents/{personal,work,business,security,research,general}/agent.py`, `autonomy/blacksmith.py`) with hardcoded prompt constants selected by domain in `orchestrator.py`.
- The factory path (`RequestedAgentService.request_agent`) already composes governed prompts from `BASE_PROMPT_REFS` + a purpose prompt, pins them, and activates an L0 identity.
- `agents/prompts.py:issued_system_prompt` reads the factory-issued prompt from `Agent.metadata["system_prompt"]` with fail-closed validation.
- The orchestrator prefers `issued_system_prompt` over the static fallback when present (tool loop + isolated chat).

## Build slice (ordered, each independently testable)
1. **Seed built-in prompt records.** Add one `PromptSpec` per default domain to `factory/prompts_builtin/` (or a new `default_agents/` bundle) with `builtin:` provenance, PUBLISHED, safety-paired where REAP packs are used. Reuse `agency-base-identity`, `agency-evidence-discipline`, `agency-l0-scope` segments.
2. **Seed factory blueprints.** On orchestrator/butler startup (when factory + prompt_registry are configured), create+approve+activate a blueprint per default domain (`personal-agent@1.0.0`, etc.) with the seeded prompt refs and domain capabilities, using the autonomy-policy authorizer (no self-approval violation). Idempotent: skip if already ACTIVE.
3. **Route by factory identity.** Butler `_ensure_default_agents` should prefer factory-issued identities; fall back to legacy `register_agent` only when factory is unavailable (offline/hermetic tests).
4. **Prompt refinement.** Wire `RequestedAgentService.improve_agent` for default agents too (currently gated on `is_factory_issued`), so successful tasks can propose DRAFT successors. Keep `auto_promote=False` by default for defaults (human/evaluator promotion required).
5. **Blacksmith.** Activate via `activate_blacksmith` at startup when factory is configured; pin the new `BLACKSMITH_SYSTEM_PROMPT` as a prompt record.
6. **Capability contract.** Project each default agent blueprint through `AgentBlueprintCapabilityAdapter` so composition/discovery/attestation flows apply; require attestation for PUBLISHED.

## Acceptance
- Default agents' `Agent.metadata["system_prompt"]` is non-empty and equals the composed prompt; `issued_system_prompt` returns it.
- Restart rehydrates via explicit `recover_active` (no auto-resurrection).
- Ruff + mypy + focused tests pass; no live provider calls.

## Out of scope
- Autonomous promotion, merge/release authority, peer authority (P2).
- Per-domain tool restrictions beyond existing `TOOL_CAPABLE_DOMAINS`.

## References
- `factory/service.py:359-691` (create/approve/activate), `factory/compose.py:34-84`, `factory/prompts.py`, `factory/prompts_builtin/manifest.yaml`, `agents/prompts.py:17-37`, `orchestrator.py` tool loop prompt selection.
EOF