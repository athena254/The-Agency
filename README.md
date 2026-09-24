# The Agency — Governed Multi-Agent Execution System

> Human → Butler → orchestrator/agents/tools, with Lattice coordination,
> memory, policies/audit/sandbox as cross-cutting infrastructure.
> Agent Factory, skill/workflow registries, and Forge are **planned**, not implemented.

**Canonical direction:** `docs/SOURCE_CONSOLIDATED_BRIEF.md` (target design, all 51 sections)
and `docs/SPEC_AGENCY_CORE_RECONCILIATION.md` (code-backed gap matrix and build slice).
These two documents are normative for where the system is going. This README describes
what actually exists today and labels the rest as planned.

**Platform boundary:** ATHENA is a **separate peer platform**, not an Agency component.
No Agency core capability depends on ATHENA. Any future ATHENA integration would be an
explicit, optional adapter. Diagrams below that show an "ATHENA Core" inside the Agency
are historical/superseded (see `docs/ARCHITECTURE.md`).

## Current architecture (actual)

```text
                    HUMAN
                      |
                    BUTLER                human interaction + scoped context
                      |                   (src/agency/butler/service.py;
                      |                    sender-scoped recall only,
                      |                    no independent thread store yet)
                      v
                ORCHESTRATOR              task submit/execute
                      |                   (src/agency/orchestrator.py)
        +-------------+-------------+
        |                           |
      AGENTS                       TOOLS
      - research                    - registry + driver
        (src/agency/agents/          (src/agency/tools/registry.py,
         research/agent.py)           src/agency/tools/driver.py)
      - general                     - web_search / web_fetch / memory /
      - demo / planner /              sandbox_exec adapters
        executor / verifier
                      |
        +-------------+-------------+
        |             |             |
      LATTICE       MEMORY      POLICIES / AUDIT / SANDBOX
      coordination/ governance  scoped stores   kernel policies,
      substrate     + retrieval audit log,      secrets, sandbox
      (src/agency/  (src/agency/  isolation
       lattice/)     memory/sms/) (src/agency/kernel/,
                                   src/agency/security/sandbox/)

   PLANNED — not implemented: Agent Factory, skill registry,
   workflow registry/executor, Forge (integrated software factory),
   independent thread/workspace store.
```

Cross-cutting (partial, code-enforced where noted): security policies
(`src/agency/kernel/policies.py`), audit (`src/agency/kernel/audit.py`),
provenance/evidence (`src/agency/evidence/`), sandbox isolation
(`src/agency/security/sandbox/`), adversarial QA critics
(`src/agency/security/red|blue|purple/`, `src/agency/agents/verifier.py`).

## Current vs planned state

| Area | State | Evidence / note |
|------|-------|-----------------|
| Butler gateway | Partial | `src/agency/butler/service.py`, `server.py`, `router.py`; sender-scoped history only, no durable thread store |
| Orchestrator + task pipeline | Partial | `src/agency/orchestrator.py`, `src/agency/kernel/tasks.py` |
| Research agent | Exists, partial | `src/agency/agents/research/agent.py` exists (a prior status table wrongly said 0%); end-to-end behavior not verified here |
| General/demo agents, planner, verifier | Exists, partial | `src/agency/agents/`; capability/eval coverage not claimed |
| Lattice coordination/governance | Partial | `src/agency/lattice/api.py`, `backends/sqlite.py`, `governance.py` (in-memory proposals, best-effort backend mirror); durable vote persistence is planned |
| Memory | Partial | `src/agency/memory/sms/`; scoped per sender, hierarchical isolation planned |
| Sandbox / security / audit | Partial | Isolation guarantees not audited; do not treat as production-hardened |
| CI workflow | Exists, outcome unverified | `.github/workflows/ci.yml` (and `cd.yml`) exist; a prior roadmap row wrongly said 0%/planned. Workflow success and live operation were not verified for this docs pass |
| Threads / workspaces | Planned | No thread/workspace module under `src/agency`; separate agents are working on threads on other branches — not claimed here |
| Agent Factory | Planned | Identity (`kernel/registry.py`) and runtime (`agents/registry.py`) registries exist; no versioned factory with validation/evaluation gate |
| Skill registry, workflow registry/executor | Planned | No Agency-native modules under `src/agency`; separate agents are working on skills/workflows elsewhere — not claimed here |
| Forge (integrated software factory) | Planned | `src/agency/addons/dark_factory/` and `ghost_factory` are standalone prototypes, not an integrated Forge; no complete Forge is promised |
| External bridges (Claude, Codex, Hermes, OpenClaw) | Partial adapters | `src/agency/bridges/`; optional integrations behind explicit boundaries, never architectural dependencies |

Status words used here are deliberately coarse (Exists / Partial / Planned / Missing).
No precise percentages are given because the evidence does not warrant them, and nothing
above should be read as a production-readiness claim.

## Quick Start (verified commands only)

Only commands whose targets exist in this checkout are listed. Broken historical
instructions (`python -m theagency.gateways.launcher`, `pip install -e ".[all]"`,
`docker-compose -f docker-compose.sandbox.yml`) have been removed; no replacement
commands are invented for servers or services that are not defined here.

```bash
# Install (extras defined in pyproject.toml: dev, neo4j, qdrant, ollama, llm)
pip install -e ".[dev]"

# CLI entry point (defined as agency = "agency.cli:main" in pyproject.toml)
agency --help
agency agent list
agency task list

# Tests
python -m pytest tests/ -q

# Lint / typecheck (match .github/workflows/ci.yml)
ruff check src/ tests/
python -m mypy src/
```

Do not run live services as part of this docs pass. Server lifecycle commands
(`agency start`/`stop`/`status`, `src/agency/api/server.py`, Butler HTTP) are
described by `agency --help` and `src/agency/cli/main.py`; live operation was not
verified here.

## What is The Agency?

The Agency is intended to become a general-purpose, governed multi-agent execution
system: a human-facing Butler, an orchestrator over agents/skills/workflows/tools,
a Lattice coordination/governance substrate, memory, sandboxing, audit/provenance,
and an internal software factory (Forge). See `docs/SOURCE_CONSOLIDATED_BRIEF.md`
sections 3–22 for the target model and `docs/SPEC_AGENCY_CORE_RECONCILIATION.md`
for the code-backed gap matrix.

Useful historical concepts retained as direction (not status claims): Butler gateway,
sandbox modes, QA Critic/adversarial review, governance, Lattice, domain agents,
foundation services. Historical specifications under `docs/` are preserved; where they
contradict the platform boundary or current code, `docs/ARCHITECTURE.md` labels them
historical/superseded rather than deleting them.

## Documentation

| Document | Purpose |
|----------|---------|
| `docs/SOURCE_CONSOLIDATED_BRIEF.md` | Preserved source of truth — target architecture/product direction (normative for goals) |
| `docs/SPEC_AGENCY_CORE_RECONCILIATION.md` | Preserved reconciliation spec — code-backed gap matrix, build slice, acceptance gates |
| `docs/ARCHITECTURE.md` | Canonical current architecture + labeled historical diagrams |
| `docs/ROADMAP.md` | Honest implemented / partial / planned roadmap |
| `docs/HEALTH.md` | Honest project health assessment |
| `docs/SPECIFICATION.md`, `docs/SPEC_*.md` | Historical specifications (preserved; superseded where they conflict with the brief) |

## Notes and non-goals

- `Athena-global-skills` (`https://github.com/athena254/Athena-global-skills`) is a
  separate source of patterns and candidate capabilities, not a dependency. Nothing is
  auto-imported from it.
- Addons and bridges are not labeled production-ready. Prototypes (e.g. dark/ghost
  factory) do not imply a complete Forge.
- Thread/skill/workflow work happening on other branches is not claimed here.
- No commits and no live-service runs were part of this documentation pass.

## License

MIT
