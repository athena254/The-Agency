# Agent Factory v1 — governed identity creation

Status: design, not completed functionality. Source: `SOURCE_CONSOLIDATED_BRIEF.md` sections 6, 17–18, 26–32, 40–50. The existing `agency.kernel.identity.Agent` is the runtime identity. Do not create a second runtime agent model.

## Boundary and contract

- Store versioned blueprint records in SQLite, keyed by `(id, version)`. Include name, domain, declared capabilities, pinned skill/workflow references, creator, lifecycle state, evidence, created timestamp, and active runtime agent ID. No executable code or credentials in a blueprint.
- Creation starts at `DRAFT` only. `validate` checks nonempty names, semver, bounded input, allowed capabilities and exact pinned references against injected published-skill and published-workflow checks. Missing refs fail closed. Validation is deterministic; no LLM is needed.
- `approve` requires an injected trusted authorizer for `agent.approve`, a nonempty evidence reference, and an approver different from the creator. No method may approve itself using caller-controlled actor permissions. Auth is supplied by a trusted caller, not HTTP request fields.
- `activate` only from approved blueprint. It creates an `Agent` with `TrustLevel.UNKNOWN` and L0 capability claims, registers through existing kernel and runtime registries, records the runtime ID and version. It issues NO `Permission`; claims are not grants. Any downstream capability issuance follows normal policy and governance separately.
- `revoke` records evidence, marks the blueprint revoked and revokes the runtime identity through existing kernel registry. Revoke cannot erase provenance; operations are idempotent or fail explicitly.
- A new blueprint version does not change an active identity; rollout needs a new approval. Never execute arbitrary source or copy ATHENA state.

## Acceptance

Tests cover persistence/restart, duplicate-version rejection, malformed and unpinned refs, unauthorized/self-approval denial, no preapproval activation, no permission grant, runtime registry registration, revocation, and version isolation. If existing registry APIs cannot safely implement an operation, fail closed and document it rather than bypassing them.

This foundation does not implement autonomous agent creation, agent evaluation, model selection, runtime process isolation, safe permission issuance, or production deployment. These remain separate future slices.