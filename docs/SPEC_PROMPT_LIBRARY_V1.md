# Prompt Library v1 — versioned prompts for factory-issued agents

Status: implemented bounded slice, including runtime consumption and Forge
tool-availability wiring. This extends the Agent Factory v1 contract
(`SPEC_AGENT_FACTORY_V1.md`) without changing its governance model. Source of
target intent: `SOURCE_CONSOLIDATED_BRIEF.md` §7.2 (system instructions, role
definitions, templates) and §7.3 (reusable capability factory).

## Boundary and contract

- Store versioned prompt records in SQLite, keyed by `(prompt_id, version)`.
  Each record carries name, description, domain, kind (`BASE`/`SEGMENT`/`PACK`),
  body, provenance and lifecycle status. Bodies are inert text: the factory
  never executes, imports, evaluates or fetches prompt content. No credentials
  or executable code are stored.
- New prompt versions start at `DRAFT` and advance one step at a time through
  `TESTING → APPROVED → PUBLISHED → DEPRECATED → RETIRED`. Transitions to
  `APPROVED` and `PUBLISHED` require nonempty evidence. Records are immutable;
  changing a prompt means a new version.
- Built-in records are seeded idempotently at `PUBLISHED` through
  `PromptRegistry.seed_builtin`. Seeding never overwrites an existing row and
  requires provenance beginning with `builtin:`.
- Blueprints pin prompts exactly: `prompt_refs` accepts only `id@X.Y.Z` form.
  Validation checks every pin against an injected `is_prompt_published`
  callback and fails closed when the callback is missing or throws.
- Blueprints may carry bounded slot parameters (`prompt_params`, keys matching
  `[A-Z][A-Z0-9_]{0,63}`, values ≤512 chars, ≤8 KiB total) and one bounded
  per-agent override (`prompt_override`, ≤4096 chars). Parameters and overrides
  require at least one prompt ref.
- Activation composes one system prompt deterministically from the pinned
  refs, in blueprint order, with duplicate bodies collapsed. Factory slots use
  `{{SLOT_NAME}}`; unfilled slots and unused parameters fail closed. Upstream
  angle-bracket placeholders (`<FEATURE>`) pass through untouched for the
  human or agent to fill at use time. No model is called.
- Activation attaches `system_prompt`, `prompt_refs`,
  `prompt_composed_sha256` and (when present) `prompt_params` to
  `Agent.metadata`. Activation fails closed when prompts are pinned but no
  resolver is configured. Issued identities remain `TrustLevel.UNKNOWN` with
  L0-only claims; prompts never grant permission.
- Runtime consumption: `agency.agents.prompts.issued_system_prompt` validates
  the issued metadata (nonblank string, bounded size) and the orchestrator
  applies it to the tool-loop, classic-executor and isolated-conversation
  paths. A malformed issued prompt fails the turn closed rather than silently
  falling back to an ungoverned default prompt. Agents registered directly
  (no issued prompt) keep the legacy per-domain prompts.
- Optional dynamic parameters: `AgentFactory(prompt_param_provider=...)` may
  supply slot values from runtime sources at activation. Provider results are
  bounds-checked like blueprint params; a value conflicting with an explicitly
  pinned blueprint value fails activation closed.

## Built-in library

`src/agency/factory/prompts_builtin/` ships shared segments plus the REAP
(REA Investigation Prompts) library:

- Segments: `agency-base-identity`, `agency-evidence-discipline`,
  `agency-l0-scope`, `reap-core-methodology`, `reap-safety-scope`,
  `reap-tool-availability`.
- Packs: 26 REAP packs covering REA sections 0–22 plus the wildcard, prompt
  index and prompt-writing tips. Bodies are verbatim upstream prompt text
  (MIT); the upstream commit is pinned in `manifest.yaml` and recorded in every
  provenance string.

REAP domain packs are governance-paired: any pinned `reap-*` pack (except
`reap-safety-scope`, `reap-wildcard`, `reap-prompt-writing-tips`, `reap-prompts`)
requires `reap-safety-scope` to also be pinned. Validation and approval fail
closed otherwise. REAP content is MIT licensed; attribution and license text
ship in `prompts_builtin/THIRD_PARTY_LICENSES.md`.

## Tool availability

`src/agency/forge/prompt_params.py` bridges the Forge tool catalog to prompt
slots without installing or running anything:

- `rea_prompt_params()` reports `REA_READY`, `RE_TOOLS_AVAILABLE` and
  `RE_TOOLS_MISSING` from `forge/tool_catalog.py` (names only; bridge-only
  catalog entries are excluded because they cannot be probed).
- `forge_tool_params` is a drop-in `prompt_param_provider`. It returns values
  only when the blueprint pins `reap-tool-availability`, so prompts without
  those slots never receive unused parameters (composition would fail closed).
- Wire it as `AgentFactory(..., prompt_param_provider=forge_tool_params)`.
  Values are captured in the composed prompt and its sha256 at activation;
  availability changes require a new blueprint version and approval.

## Acceptance

Tests cover prompt registry persistence/restart, duplicate-version rejection,
malformed ids/versions, body caps, lifecycle evidence, idempotent seeding and
non-overwrite, built-in coverage and provenance, unpinned refs, missing
callbacks failing closed, unpublished pins, REAP safety pairing, parameter and
override bounds, deterministic composition and slot handling, and activation
metadata attachment (`tests/test_prompt_registry.py`,
`tests/test_prompt_library_builtin.py`, `tests/test_factory_prompt_refs.py`,
`tests/test_factory_prompt_compose.py`). Runtime consumption and tool-parameter
wiring are covered by `tests/test_runtime_prompt_consumption.py` and
`tests/test_forge_prompt_params.py` (including fail-closed malformed metadata
and provider-conflict rejection).

## Out of scope

LLM-based prompt generation, skill-content execution, installing or executing
reverse-engineering tools, and API/CLI wiring for prompt management remain
separate future slices.
