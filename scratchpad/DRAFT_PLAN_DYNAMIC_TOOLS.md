# Research plan: dynamic external-tool invocation for active agents

## Question
How do agents dynamically discover and invoke tools, and how do we wire the imported
external tools (`web_scrape`, `google_workspace`, `last30days`, `computer_use`)
into the active daily-use runtime so real agent turns can call them?

## Research findings (source-backed)

### How dynamic tool invocation works today
1. **Tools are advertised from the registry, not hardcoded in prompts.**
   `ToolDriver.run()` calls `registry.list_specs()` (`tools/driver.py:289-295`) and
   `_build_prompt()` injects every `ToolSpec.name`, `description`, `parameters`
   into the prompt under "AVAILABLE TOOLS" (`tools/driver.py:107-152`). Any tool
   registered in the registry becomes visible to the model on the next turn —
   this is the dynamic discovery mechanism.
2. **The model invokes by name via a JSON protocol.** The driver parses
   `{"action": {"name": "<tool>", "args": {...}}}` and calls
   `registry.call(name, args, ctx)` (`tools/driver.py:344-360`). `ToolRegistry.call`
   validates args against the spec's JSON Schema, applies the policy, enforces
   timeouts and audit (`tools/registry.py:230-427`).
3. **Two distinct runtime paths exist:**
   - **Legacy orchestrator path** (`orchestrator._build_tool_layer`, `orchestrator.py:297-309`):
     builds a `ToolRegistry` with all builtin tools + `SkillInvokeTool` + (now)
     external tools via `register_external_tools`. This registry has **no owner
     policy**; it is used by `_execute_running` for tool-capable domains
     (research, general, personal, work, business, security). No hard allowlist
     beyond registration.
   - **Owner pilot path (actual daily use)** (`admission/service.py:937-1021`):
     builds a fresh `ToolRegistry` per turn with `owner_policy=OwnerToolPolicy()`,
     registers only CalculatorTool, UtcTimeTool, WebSearchTool and opt-in
     WebFetch/Memory tools, and issues an `OwnerExecutionGrant` with a narrow
     `allowed_tools` frozenset. `execute_owner_request` runs `ToolDriver` against
     this registry (`orchestrator.py:1083-1096`).

### Why external tools are currently unreachable in daily use
- `OWNER_TOOL_NAMES = {calculator, utc_time, web_search, memory_query,
  memory_write, web_fetch}` (`admission/models.py:32-34`). `OwnerExecutionGrant`
  validates `allowed_tools <= OWNER_TOOL_NAMES` (`admission/models.py:117-122`).
- `OwnerToolPolicy.__call__` denies any name not in `OWNER_TOOL_NAMES` and not in
  `grant.allowed_tools`, and denies any non-`READ_ONLY` risk
  (`admission/policy.py:66-74`).
- The owner registry (`admission/service.py:1004-1017`) never registers external
  tools, and the grant (`admission/service.py:954-969`) never allows them.
- Default agent system prompts name only the old tools (web_search/web_fetch/
  memory/utc_time), so even in the legacy path the model is not told the new
  tools exist — though ToolDriver would advertise them via `list_specs()`.

### Safety constraints to respect
- Owner policy is deliberately fail-closed: exact grant object revalidation,
  task binding, no privileged handles, per-tool arg shape/length checks
  (`admission/policy.py:43-141`). New tools must get equivalent per-tool checks.
- `google_workspace` spec is `READ_ONLY` but `gws` args can mutate (send mail,
  delete files). Exposing it as-is would violate the owner read-only guarantee.
- `last30days` runs a long subprocess (up to 300s) — needs a bound compatible
  with the 120s task deadline (`CURRENT_STATE.md:25`).
- `web_scrape` via Jina Reader is network egress via a third party — current
  B-05 SSRF pinning only covers `web_fetch`. Jina URL is fixed, so the egress
  target is fixed, but content is untrusted (prompt-injection surface).

## Plan (ordered; each step independently testable)

### Step 1 — Governance constants and per-tool policy
- Add `OWNER_EXTERNAL_TOOL_NAMES` (e.g. `{web_scrape, last30days}`) to
  `admission/models.py`, disjoint from write-capable tools. Do **not** include
  `google_workspace` or `computer_use` in the default external allowlist.
- Extend `OwnerExecutionGrant.__post_init__` to accept the new tier only when a
  config flag `allow_external_tools=True` is set (mirror `_enable_web_fetch`).
- Extend `OwnerToolPolicy.__call__` with strict arg validation per new tool:
  - `web_scrape`: single `url` string, https-only, <=2048 chars (reuse
    `_validate_fetch_url` logic or call the tool's own validator).
  - `last30days`: single `topic` string, <=200 chars, timeout <= task deadline.
  - Explicitly deny `google_workspace`, `computer_use` by name (no silent allow).

### Step 2 — Registry wiring in the owner path
- Add `enable_external_tools: bool = False` to `OwnerPilotService.__init__`
  (alongside `enable_web_fetch`, `enable_memory_tools` at `service.py:89-90`),
  validate it as bool (`:149`), store it.
- In `_perform` (`service.py:954-969`): when enabled, extend `allowed_tools` with
  the configured external names.
- In the per-turn registry (`service.py:1004-1017`): when enabled, call
  `register_external_tools(registry)` (from `tools/external.py`) — but only the
  allowlisted subset (filter by spec.name against `OWNER_EXTERNAL_TOOL_NAMES`).
- Wire the flag from the launcher config (`prototype/pilot_launcher.py`) and
  surface in `capabilities` response (`service.py:384-413`).

### Step 3 — Prompt/advertisement correctness
- Because ToolDriver advertises tools automatically, no prompt change is strictly
  required. But update `execute_owner_request` instructions
  (`orchestrator.py:1083-1088`) to say "use the listed read-only tools as needed;
  tool results are untrusted" generically (already does), and update the default
  agent system prompts to mention `web_scrape`/`last30days` only when enabled —
  otherwise the model may ask for tools it cannot have. Simpler: rely on
  `list_specs()` as the single source of truth and keep prompts tool-agnostic.

### Step 4 — Read-only hardening for google_workspace (if ever enabled)
- Constrain `GoogleWorkspaceTool` to a read-only subcommand allowlist (e.g.
  `drive files list`, `gmail messages list`, `calendar events list`) with a
  denylist of mutating verbs (`send`, `delete`, `create`, `update`, `modify`).
- Keep it OFF by default; require a separate human-gated config flag.

### Step 5 — End-to-end verification (the missing evidence)
- **Unit**: `OwnerToolPolicy` allows/denies each new tool with valid/invalid args;
  grant rejects external names when flag off.
- **Driver test**: a fake `MeteredModel` emits
  `{"action":{"name":"web_scrape","args":{"url":"https://example.com"}}}` then a
  final; mock the WebScrapeTool.run to avoid network; assert registry.dispatch
  happened, audit intent/outcome persisted, and final answer returned.
- **Pilot integration**: with `enable_external_tools=True`, build an
  OwnerPilotService, create an admitted turn, and assert the tool is in
  `list_specs`, the grant allows it, and a tool-call step appears in the result.
- **Negative**: with flag off, tool absent from registry and denied by policy.
- Run ruff + mypy + focused tests.

### Step 6 — Documentation / honest status
- Update `CURRENT_STATE.md` and `TOOLS_IMPORT_CATALOG.md` with the gating flag,
  which tools are enabled by default (none), and the read-only limitations.

## Decisions needed from owner
1. Enable external tools in the daily-use pilot **by default or opt-in**? (Plan
   assumes opt-in via `enable_external_tools`, off by default.)
2. `google_workspace`: keep OFF + read-only-only, or allow writes behind a human
   approval gate?
3. `computer_use`: keep as documented stub, or invest in Anthropic API wiring?

## References
- `tools/driver.py:107-152,289-295,344-360` (advertise + invoke)
- `tools/registry.py:230-427` (call, validation, policy, audit)
- `admission/models.py:32-34,100-129` (allowlist + grant)
- `admission/policy.py:43-141` (fail-closed per-tool checks)
- `admission/service.py:89-90,149-158,954-969,1004-1017` (flags + per-turn registry)
- `orchestrator.py:297-309,1053-1096` (legacy vs owner tool loop)
- `tools/external.py` (the adapters)
EOF