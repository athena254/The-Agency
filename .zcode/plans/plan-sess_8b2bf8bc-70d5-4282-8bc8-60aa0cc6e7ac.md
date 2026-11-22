# PLAN: Tool Provisioning — every agent gets the tools it needs; Forge builds or imports what's missing

**Deliverable:** `docs/PLAN_TOOL_PROVISIONING.md` (DRAFT for owner review, repo plan convention) committed to main. Goal: when the Agent Factory makes an agent, that agent gets the tools its domain requires — declared on the blueprint, resolved at activation, enforced at call time. The Forge stays software-only and becomes the universal maker/importer of tools for every other domain.

## Context

Today: blueprints carry prompt_refs/skill_refs/capability_ceilings but **no tool field**; the orchestrator builds ONE shared tool registry with no per-agent allowlist (every agent can call everything); tool-availability honesty exists only for RE tools; GitHub imports land as quarantined non-executing drafts. Meanwhile `forge_auto.build_tool` already runs tools through inspection+security+pytest gates and registers only on PASS+SHA-match — the universal verifier for Python tools exists. Research (MCP spec, Claude SDK permissions, OpenAI Agents SDK, CrewAI, tool-poisoning/postmark-mcp supply-chain incidents, ToolMaker/GoEx) confirms the design rules used below; full source list lives in the doc.

## Design (7 pieces)

1. **Blueprint `tool_requirements`** — tuple of tool names on `factory/service.py` Blueprint, validated + persisted via the existing `_ensure_columns` pattern (like A1's capability_ceilings). Pure declaration.
2. **Domain tool-needs catalog** — `data/tool_needs.yaml` + `agency/tools/needs.py` loader: capability/domain → required/optional tool names. Seeded with builtin chat caps → builtin tools; `forge` → RE catalog; others honest-empty until imported agents declare needs.
3. **ToolProvisioner** (`agency/tools/provision.py`) — resolves each required name to exactly one honest status: PROVISIONED / CATALOG_AVAILABLE / NEEDS_INSTALL (with hint) / BUILDABLE (Forge can close it) / UNKNOWN (fail closed). Never installs, never auto-builds — reports; dispatch stays with Blacksmith/human.
4. **Generalized availability honesty** — new segment `agency-tool-availability.md` with slots filled at composition by a provider (generalizes `forge/prompt_params.forge_tool_params`); unfilled slots already fail closed in compose.
5. **Per-agent enforcement** — `ToolContext.metadata["allowed_tools"]` set by `_tool_context` from the blueprint; `ToolRegistry.call` refuses non-allowlisted tools (fixed error, audited); prompt-building filters specs to the allowlist (hidden tools never reach the model). Factory agents fail closed; legacy paths unchanged.
6. **Tool import (plugins/GitHub)** — `agency/tools/importing.py` + manifest schema `{name, version, description, provenance{source, commitSHA, contentHash}, tools[{name, description, inputSchema, risk}], network.allowedDomains, tests}`. Flow: pin commit SHA → quarantine → **same Forge gates as build_tool** → mandatory human review/attestation → register with provenance (hash covers code AND tool descriptions — anti rug-pull) → updates re-enter quarantine (postmark-mcp lesson). Never executes upstream at import.
7. **Forge build wiring** — `BUILDABLE` gaps + a job spec → BlacksmithOps `build_tool_job` → real gates, real `report_id`; no spec → honest `build_blocked`. Forge remains software-only: it *makes and verifies* tools for every domain; it never becomes a video pipeline.

## Phases (TDD, gate per phase: ruff, mypy, focused pytest)

1. Blueprint field + needs catalog → commit `feat(factory): blueprint tool requirements + domain tool-needs catalog`
2. ToolProvisioner + availability segment + wire into `activate_for_caps`/`request_agent` (additive, mirrors prompt_refs)
3. Per-agent allowlist enforcement at registry + driver prompt filtering
4. Manifest-based tool import through Forge gates (schema, quarantine fetch, review-gated promotion, update re-vetting)
5. Provisioner-driven gap builds + live acceptance: request an agent with a domain need → resolved report → built or honestly blocked → enforced allowlist verified from outside

## Constraints respected

- gaps.md #1 import cycle: provisioner/importer live under `agency/tools/`; factory imports lazily
- gaps.md #4/#5: attestation mandatory before any factory agent pins an imported tool
- No auto-install of host software (catalog stays catalog-not-installer)
- v1 = Python tools only; MCP-server bridging is an explicit Phase-2 owner decision (catalog already has placeholders)

## Verification

Focused TDD per phase; final live acceptance: a factory-issued agent's ToolContext can call only allowlisted tools (non-allowlisted call → fixed audited error); an imported tool exists only with provenance and only after gates PASS + review; a missing software tool with a job spec becomes a real registered tool with real report_id, without one ends as honest build_blocked; prompts never list un-callable tools.

## Open owner decisions (recorded in the doc, answered during review)

1. Plugin format v1: Python-tool manifests (recommended) vs MCP bridging first
2. Import reviewers: owner-only vs trusted-maintainer allowlist
3. Default toolset for agents declaring no requirements: none/fail-closed (recommended) vs small builtin set
4. Needs data location: `data/tool_needs.yaml` (recommended) vs agency-agents `tools.json` import