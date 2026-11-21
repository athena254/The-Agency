# REVIEW: agency-agents import — catalog status, integrity, and the road to promotion

**Date:** 2026-11-21 · **Status:** REVIEW COMPLETE — promotion pending owner go-ahead + SkillSpector install · **Scope:** all four quarantined inbox clones

## 1. Catalog state (evidence: `data/prompts.db`, 2,890 records, all DRAFT)

The import pipeline (`scripts/import_external_agents_skills.py`, provenance
`import-quarantine:`, SPEC_AGENT_FACTORY_V2 §12.3 quarantine pattern) has registered every
markdown source from the four inbox clones as immutable DRAFT prompt records; nothing is
promoted, nothing executes, and re-runs are idempotent (895 re-digests skipped on the
verification pass).

| Source | Records | Highlights |
|---|---|---|
| **agency-agents** | 308 agents | engineering 132, marketing 75, research 65, specialized 35, security 27, gis 26, testing 23, sales 22, design 21, healthcare 13, product 13, academic 12, finance 10, support/strategy/paid-media/project-management… |
| ECC | 194 agents + 314 skills | skills as non-executing SkillSpec drafts in `data/skills.db` |
| skills | 28 agents + 39 skills | |
| rstack | 5 agents + 7 skills | |
| **Total** | **535 agents + 360 skills (+ README/index debris)** | all DRAFT |

## 2. Integrity findings

- Sampled bodies across divisions are intact (6–11k chars of real persona content).
- **6 debris records must never be promoted** (clone pointers / changelog stubs, bodies < 40
  chars): `AGENTS-9802a78f8251` ("CLAUDE.md"), `CLAUDE-05d31a664c47` ("@AGENTS.md"),
  `SKILL-c19909c0bbe6`, `SKILL-79585f5cc10e`, `SKILL-99a1a2c49c0f` (pointer stubs),
  `changelog-d0e1742150f0`. They are DRAFTs; the promotion query must exclude
  `length(body) < 500`.
- 746 names appear in more than one clone (ECC/agency-agents/skills overlap). Harmless while
  DRAFT (ids are digest-suffixed); promotion must dedupe by name+division priority.

## 3. Division → tool-needs mapping (connects the catalog to PLAN_TOOL_PROVISIONING)

Tool inventory today: **registered builtins** (web_search, web_fetch, memory_query,
memory_write, utc_time, calculator, forge_read), **opt-in locals** (sandbox_exec),
**opt-in externals** (web_scrape, google_workspace, last30days, computer_use),
**Forge catalog** (RE toolchain), **MCP bridging** (configurable via `data/mcp_servers.json`).

| Division cluster | Serves today with | Gap the Forge must close (BUILDABLE/IMPORT) |
|---|---|---|
| research/academic | web_search, web_fetch, memory | citation managers, dataset fetchers |
| engineering/testing | sandbox_exec, forge_read, web | language-specific runners/linters |
| marketing/design/product | web, memory, web_scrape | image/video pipeline tools (ffmpeg wrappers, asset fetchers) |
| finance/sales/strategy | web, calculator, memory | market-data connectors, spreadsheet tools |
| security | forge RE catalog, sandbox | nothing new — catalog already covers |
| healthcare/gis | web, memory | domain API connectors (respect privacy policy) |
| SKILL records (1,113) | n/a — executable skills are gated by SkillSpec review, not tool provisioning | per-skill operation registration (gaps.md #3) |

Every gap is a **software tool**, so per PLAN_TOOL_PROVISIONING the Forge builds or imports
it when a promoted agent's `tool_requirements` declare it — no new pipeline needed for any
domain.

## 4. Promotion gate state (honest)

- **SkillSpector is not installed on this host** — the owner-decided reviewer for promotion
  (`skillspector scan <path> --no-llm --format json`; clone at `D:/agency-toolprov/SkillSpector`).
  Install: `uv tool install --python 3.13 git+https://github.com/NVIDIA/skillspector.git`.
  Until installed, promotion of any batch is `REVIEWER_UNAVAILABLE` and stays blocked
  (fail-closed by design).
- Recommended promotion flow per batch: select division → dedupe → SkillSpector scan each
  record bundle → gate on score ≥ 80 / zero critical-high → promote DRAFT→PUBLISHED with the
  scan report as evidence → attach to a factory agent's blueprint (`prompt_refs` +
  `tool_requirements` via `request_agent`).

## 5. Recommended next steps (owner order)

1. Install SkillSpector (one command, above).
2. Pick the first promotion batch (suggested: `research` + `engineering` — 197 records, both
   fully servable by existing tools; lowest tool-gap).
3. Run the batch promotion with SkillSpector gates; report survivors per division.
4. Iterate division by division; the tool-gap table above drives the Forge build/import queue.
