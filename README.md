# The Agency

The Agency is a governed multi-agent execution system. A human talks to a Butler
gateway, an orchestrator routes work to agents and tools, and every state change
passes through kernel policies, an audit log and sandbox isolation. The point is
autonomous work that stays bounded and provable, with a human holding the release
decision.

**Status: pre-production prototype.** It is not daily-driver qualified and not a
deployed production runtime. Beta model/tool ingress is disabled, and the legacy
HTTP business routes return 503 by design until peer execution authorization
exists. [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) records what is implemented,
what was verified and at which revision. Treat everything else as planned.

## What works today

- A single-owner pilot service over local HTTP: durable threads and tasks, three
  read-only tools (calculator, utc_time, web_search), attempt quotas, and typed
  receipts for verified work.
- Two browser surfaces: the dependency-free pilot workspace and the Mission
  Control console (React, built assets committed so a clone can serve it).
- Forge, a bounded software factory. Inspection, a static security scan, isolated
  test runs and review compose into one pipeline with SQLite evidence. Forge
  never authorizes its own merge or release; shipping needs human approval.
- Versioned agent, skill and workflow registries.
- Scoped memory (SMS) with an optional pinned Honcho mirror.
- Two terminal UIs (`agency tui` and `forge`) and a bounded autonomous loop.

Isolation and security are implemented but not independently audited. Do not
treat this codebase as production-hardened.

## Quick start

Python 3.11 or newer.

```bash
pip install -e ".[dev]"
cp .env.example .env    # then set your LLM provider, model and key
agency --help
```

The LLM backend can be a local Ollama or any OpenAI-compatible endpoint. Set
`AGENCY_LLM_PROVIDER`, `AGENCY_LLM_MODEL` and the matching key in `.env`. Keys
live in `.env` only, never in code, logs or commits.

```bash
agency start                              # API server on http://127.0.0.1:8000
agency tui                                # terminal UI
forge                                     # Forge spec intake
python -m agency.prototype.pilot_launcher # single-owner pilot + console
```

`agency agent list` and `agency task list` need the server running. The pilot
launcher binds loopback only and creates its own owner token. Container
artifacts and box deployment are covered in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Tests and gates (these match CI):

```bash
python -m pytest tests/ -q
ruff check src/ tests/
ruff format --check src/ tests/
python -m mypy src/
node --test tests/*.test.mjs
```

## Repository layout

```text
src/agency/   the package: butler, orchestrator, kernel, lattice, forge, memory, ...
tests/        Python and Node test suites
docs/         specs, status and guides; docs/README.md is the index
gui/          Mission Control console source (React + Vite)
scripts/      smoke tests, deployment helpers, bot launchers
config/       lattice configuration
ops/          owner trial bridges
```

## Where to read next

- [Current state](docs/CURRENT_STATE.md): what exists and what was verified.
- [Target design](docs/SOURCE_CONSOLIDATED_BRIEF.md) and the
  [code-backed gap matrix](docs/SPEC_AGENCY_CORE_RECONCILIATION.md): where the
  system is going and what is missing.
- [Architecture](docs/ARCHITECTURE.md), [deployment](docs/DEPLOYMENT.md),
  [roadmap](docs/ROADMAP.md), [known issues](docs/KNOWN_ISSUES.md).
- [docs/archive/](docs/archive/README.md): historical handoffs, research and
  verification records, kept for provenance.

## Boundaries

ATHENA is a separate peer platform, not an Agency component; no core capability
depends on it. The
[Athena-global-skills](https://github.com/athena254/Athena-global-skills) repo is
a source of patterns, not a dependency; nothing is auto-imported from it. Other
projects are consumed over HTTP, never vendored, and Forge's reviewer is
Agency-native. The spec set lives in `docs/SPEC_*.md`; Forge treats those paths
as protected, so changing them needs explicit human approval.

## License

MIT
