# PLAN: Tool Provisioning — every agent gets the tools it needs; Forge builds or imports what's missing

**Date:** 2026-11-21 · **Status:** DRAFT for owner review · **Branch target:** `feat/tool-provisioning` (off `main`)

**Goal:** When the Agent Factory makes an agent, that agent must actually have the tools its
domain requires — declared on the blueprint, provisioned at activation, and enforced at call
time. The Forge stays a **software-only factory**: it builds Python tools where missing
(`build_tool` gates exist today) and **imports** tools from pinned plugin/GitHub sources
through the same gates. No domain other than software gets its own pipeline — every domain's
tooling is software, so the Forge makes or imports it for them.

**Non-goals:** No auto-installation of host software (catalog stays catalog-not-installer).
No MCP-server bridging in v1 (catalog placeholders already mark them; Phase-2 decision). No
expansion of any agent's authority; approvals stay human.

---

## 1. Verified current state (evidence, 2026-11-21)

| Fact | Location | Consequence |
|---|---|---|
| Blueprints carry `prompt_refs`/`skill_refs`/`workflow_refs`/`capability_ceilings` — **no tool field** | `factory/service.py` | Tool needs are prose in prompts, not provisioned facts |
| One shared legacy registry; `_tool_context` carries `agent_id` but **no per-agent allowlist** | `orchestrator.py:301-334` | Every agent can call every registered tool (policy-gated only at beta/owner level) |
| Forge can build a tool: `build_tool(name, code, tests)` → inspection + security + pytest gates → register iff PASS + SHA match; explicitly internal-input-only | `autonomy/forge_auto.py:137` | The universal verifier for Python tools already exists |
| Tool-gap decision exists: USE if present, BUILD if missing | `autonomy/toolforge.py:21` | The missing→build loop is wired for forge caps only |
| Catalog is catalog-not-installer: `needs_install` + hint, never auto-runs; MCP bridges are `command=None` entries | `forge/tool_catalog.py` | Import/build must preserve this honesty |
| Tool-availability honesty exists **only for RE tools** (`reap-tool-availability` slots, `forge/prompt_params.py`) | `forge/prompt_params.py` | Pattern to generalize, not reinvent |
| Imports land as quarantined DRAFTs (non-executing pointer implementations); never execute upstream at import | `factory/github_skills.py`, `scripts/import_external_agents_skills.py` | Tool import must add the gate-verified path, not bypass quarantine |
| Attestation for publication is opt-in (gap #5); learned skills have no executable registration (gap #3) | `gaps.md` | Imported tools must make attestation mandatory before an agent pins them |

## 2. Design rules from research (each traceable to a source; full list §9)

1. **Separate provisioning from permissions.** "Which tools the agent can see" (allowlist) is
   a different control from "which calls auto-run" (policy). Conflating them silently
   over-provisions (Claude Agent SDK lesson: `allowed_tools` ≠ provisioning).
2. **Toolsets are per-agent and per-role**, declared at construction — never one shared pool
   (CrewAI, Anthropic subagents, Pydantic AI Capabilities).
3. **Hide unavailable tools from the model entirely** — do not list them as unusable
   (OpenAI `is_enabled`; prompt hygiene, no leakage).
4. **Treat tool descriptions as untrusted data**; pin their hash at approval and re-verify on
   every update (MCP spec + Invariant tool-poisoning; postmark-mcp shipped benign, backdoored
   later — install-time approval is not durable trust).
5. **Import pipeline = pin → quarantine → gate → review → register with provenance**; never
   execute upstream code at import; tests are the admission gate for any non-builtin tool
   (ToolMaker: closed-loop verify-then-register).
6. **Network egress declared in the manifest and enforced**, not documented (Claude plugin
   `network.allowedDomains` + MCP SSRF rules).
7. **Least privilege at the action layer**: a policy engine refuses unauthorized calls even if
   the model is hijacked; scope grants to the current task (managed identity per agent).

## 3. Design

### 3.1 Blueprint tool requirements (W1 closure)
`Blueprint.tool_requirements: tuple[str, ...] = ()` — tool *names*, validated at `create`:
- name must match `^[a-z][a-z0-9_-]*$`, bounded count (`MAX_TOOL_REQUIREMENTS = 32`);
- persisted via the existing `_ensure_columns` ALTER TABLE pattern (`tools_json`, same as
  `capability_ceilings` in the A1 slice);
- round-trips through `to_dict()`. No semantics yet — pure declaration. Additive; default
  unchanged.

### 3.2 Domain tool-needs catalog
`data/tool_needs.yaml` (data, not code): capability/domain name → `required` + `optional`
tool names. Seed with what is real today: builtin chat capabilities → builtin tools
(`web_search`, `web_fetch`, `memory_query`, `memory_write`, `utc_time`, `calculator`);
`forge` → RE catalog names. Everything else stays honest-empty until imported agents declare
needs (the `agency-agents` `tools.json` is a future input, not v1 — YAGNI).
Loader `agency/tools/needs.py`: frozen pydantic, idempotent, fails closed on unknown
capability references at *activation-report* time (never blocks creation).

### 3.3 ToolProvisioner (`agency/tools/provision.py`)
`resolve(requirements, *, registry=None) -> ToolProvisionReport` — per required name, one of:
- `PROVISIONED` — registered builtin/imported tool exists (available now);
- `CATALOG_AVAILABLE` — host CLI present per `tool_catalog.is_available`;
- `NEEDS_INSTALL` — catalog tool missing host binary + install hint (honest refusal);
- `BUILDABLE` — software gap the Forge can close via `build_tool` (job spec required);
- `UNKNOWN` — no source knows this name (fail closed, never silently dropped).
Report carries evidence ids (report_id per build later). **The provisioner never installs
anything and never auto-builds** — it reports; dispatch decisions stay with the Blacksmith/human.

### 3.4 Generalized availability honesty
New segment `segments/agency-tool-availability.md` (SEGMENT kind) with slots
`{{TOOLS_PROVISIONED}}`, `{{TOOLS_MISSING_INSTALL}}`, `{{TOOLS_MISSING_BUILD}}` — filled at
composition by a provider (generalizes `forge/prompt_params.forge_tool_params`: fill only when
the blueprint pins the segment). Unfilled slots already fail closed in `compose_system_prompt`.

### 3.5 Per-agent tool enforcement (provisioning ≠ permissions)
- `ToolContext.metadata["allowed_tools"]: frozenset[str]` — set by `orchestrator._tool_context`
  from the agent's blueprint requirements + builtin always-ons; factory-issued agents ALWAYS
  get an explicit allowlist (fail closed); legacy paths without one keep current behavior.
- `ToolRegistry.call` refuses tools not in the ctx allowlist (fixed error vocabulary, audited);
  `list_specs`-driven prompt building filters specs to the allowlist (rule 3 — hidden tools
  don't reach the model).
- Authorization (Permission/PolicyEngine/beta/owner policies) unchanged and still evaluated
  per call — provisioning narrows the surface, policy still gates each action.

### 3.6 Tool import from plugins/GitHub (`agency/tools/importing.py` + `data/tool_manifest.schema.json`)
Manifest (KISS v1 — Python tools only; MCP bridging deferred to Phase-2 decision):
`{name, version, description(≤1024, no markup), provenance{source, commitSHA, contentHash},
tools[{name, description, inputSchema, risk}], network.allowedDomains, tests{command}}`.
Flow — reuses the Forge as the universal verifier:
1. Fetch pinned commit SHA into quarantine (never `main`, never floating tags);
2. Run the **same gates as `build_tool`** (inspection, security scan, pytest in the isolated
   subprocess copy) on the imported code;
3. Human review gate (mandatory attestation — closes gaps.md #4/#5 for tools);
4. Register with provenance metadata (source, SHA, content hash covering code **and tool
   descriptions** — anti rug-pull per rule 4);
5. Re-vet every update as a new import (postmark-mcp lesson).
`import_tool(manifest)` never executes upstream code at import; the only execution is the
post-gate resolve, under the same trust boundary `forge_auto` documents.

### 3.7 Forge builds missing software tools (wiring, not new machinery)
`decide_tool_gap` (USE/BUILD) extends to provisioner outcomes: `BUILDABLE` + a job spec →
BlacksmithOps `build_tool_job` (real gates, real digests) → on PASS the tool is registered and
the provisioner's evidence chain records `report_id`. No spec or no authorization → honest
`needs_install`/`build_blocked`. The Forge itself remains software-only: it manufactures and
verifies tools *for* every other domain; it never becomes a video pipeline.

### 3.8 Wire-in
`activate_for_caps(..., tool_requirements=None)` (additive, mirrors `prompt_refs`);
`RequestedAgentService.request_agent` gains `tool_requirements` passthrough; domain agents
declare their existing tool usage as requirements (personal/work/business → memory+web;
research → web; forge → RE catalog). Backward compatible: absent field = current behavior.

## 4. Phases (TDD; gate per phase: ruff check/format, mypy, focused pytest)

1. **Blueprint field + needs catalog** — RED: round-trip/validation tests; GREEN:
   `service.py` column + validation; `tools/needs.py` + `data/tool_needs.yaml`. Commit
   `feat(factory): blueprint tool requirements + domain tool-needs catalog`.
2. **ToolProvisioner + availability segment** — resolve statuses with injectable
   registry/catalog; segment + provider; pin in `activate_for_caps`/`request_agent`. Commit
   `feat(tools): ToolProvisioner; agency-tool-availability segment`.
3. **Per-agent enforcement** — ctx allowlist, registry call/list enforcement, driver prompt
   filtering; factory agents fail closed. Commit
   `feat(tools): per-agent tool allowlists enforced at the registry`.
4. **Tool import** — manifest schema validation, quarantine fetch (pinned SHA), Forge-gate
   verification reusing `build_tool`, review-gated promotion, provenance-registered tools,
   update re-vetting. Commit `feat(tools): manifest-based tool import through Forge gates`.
5. **Forge build wiring + live acceptance** — provisioner → BlacksmithOps build job for
   BUILDABLE gaps; live demo: request an agent with a domain need → resolved report → built
   or honestly blocked → enforced allowlist seen from outside. Commit
   `feat(forge): provisioner-driven tool gap builds; live acceptance`.

## 5. Acceptance

- A factory-issued agent's `ToolContext` can call only its allowlisted tools; a non-allowlisted
  call returns the fixed error, is audited, and never reaches the tool.
- An imported tool exists in the registry only with provenance (source, SHA, content hash) and
  only after gates PASS + human review; a mutated update re-enters quarantine.
- A missing software tool with a job spec ends as a real registered tool with a real
  `report_id`; without a spec it ends as honest `build_blocked` — never a fake PASS.
- Prompts never list tools the agent cannot call; missing catalog tools appear as
  `needs_install` with the command, never auto-run.

## 6. Risks / constraints

- **Import cycle** (gaps.md #1): provisioner/importer live under `agency/tools/`; `agency/factory`
  imports them lazily — no module-level capability imports under `agency/factory/`.
- **Supply chain**: postmark-mcp proves install-time trust is insufficient; updates re-enter
  quarantine (Phase 4 makes this structural).
- **Prompt injection via descriptions**: content hash pinned at review; descriptions rendered
  as quoted data.
- **Context bloat**: allowlist filtering keeps tool surfaces small (rule 3); large catalogs
  deferred behind the same pattern.
- **Scope creep**: v1 = Python tools only; MCP bridging is an explicit Phase-2 decision, not an
  accident.

## 7. Sources

MCP spec & security: [tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) ·
[security best practices](https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices) ·
[registry](https://registry.modelcontextprotocol.io/docs) ·
[tool poisoning (Invariant)](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks) ·
[postmark-mcp (Snyk)](https://snyk.io/blog/malicious-mcp-server-on-npm-postmark-mcp-harvests-emails) ·
[Claude SDK permissions](https://code.claude.com/docs/en/agent-sdk/permissions) ·
[Claude plugins manifest](https://code.claude.com/docs/en/plugins/manifest-reference) ·
[OpenAI Agents SDK tools](https://openai.github.io/openai-agents-python/tools/) ·
[CrewAI tools](https://docs.crewai.com/en/concepts/tools) ·
[Anthropic multi-agent system](https://www.anthropic.com/engineering/built-multi-agent-research-system) ·
[ToolMaker](https://arxiv.org/abs/2502.11705) · [GoEx](https://arxiv.org/abs/2404.06921) ·
[AgentHub](https://arxiv.org/html/2510.03495)

## 8. Open owner decisions

1. **Plugin format v1**: Python-tool manifests (recommended — reuses Forge gates directly) vs
   MCP-server bridging first (catalog already has placeholders; needs an MCP client).
2. **Import reviewers**: owner-only vs a trusted-maintainers allowlist per source org.
3. **Default toolset** for factory agents that declare no requirements: none (fail closed,
   recommended) vs a small builtin basic set.
4. **Where domain needs data lives**: `data/tool_needs.yaml` (recommended) vs importing
   `agency-agents`' `tools.json` directly.
