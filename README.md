# Nexus — Decentralized Multi-Agent Orchestration System

> A lattice of domain agents, governed by peers, healing itself, thinking together.

## Quick Start

```bash
# Clone
git clone https://github.com/danny-dis/nexus.git
cd nexus

# Install
pip install -e ".[all]"

# Run gateway (default: separate mode)
python -m nexus.gateways.launcher

# Or run sandbox standalone
docker-compose -f docker-compose.sandbox.yml up -d
```

## What is Nexus?

Nexus is a **decentralized multi-agent system** where specialized AI agents collaborate autonomously through a shared graph database (the "Lattice"). Instead of one central boss, agents vote on decisions, score each other's work, and govern themselves.

**Three Gateway Modes:**
- **Butler Only** — Routes directly to domain agents (headless, API-only)
- **Butler + Buddy Separate** — Production mode with full UI (RECOMMENDED)
- **Butler + Buddy Merged** — Single process for dev/demo

**Two Sandbox Modes:**
- **Clean Room** — Empty Python environment for algorithm testing
- **Nexus Mirror (Gemini)** — Full codebase clone for testing system changes

**QA Critic Addon:**
- Simultaneously reviews every agent's work
- Enforces strict QA rules across 10 quality dimensions
- Actions: ALLOW, BLOCK, REQUIRE_APPROVAL, RETRY, ESCALATE, QUARANTINE

## Documentation

| Document | Purpose |
|----------|---------|
| `docs/ARCHITECTURE.md` | High-level system architecture |
| `docs/SPECIFICATION.md` | Detailed APIs, data models, interfaces |
| `docs/SPEC_SUMMARY.md` | Quick-reference index |
| `docs/SPEC_GATEWAY.md` | Gateway/Butler addon specification |
| `docs/SPEC_BUDDY.md` | Buddy UI agent specification |
| `docs/SPEC_SANDBOX.md` | Sandbox addon specification |
| `docs/SPEC_ADVERSARIAL.md` | QA Critic addon specification |
| `docs/ROADMAP.md` | Implementation roadmap and status |
| `docs/HEALTH.md` | Project health assessment |

## Project Status

| Component | Status |
|-----------|--------|
| Foundation Services | ✅ Production-ready |
| Sandbox Addon | ✅ Fully functional |
| QA Critic Addon | ✅ Fully functional |
| Gateway System | ✅ Fully functional |
| Buddy UI Agent | ✅ Fully functional |
| Governance Module | 🟡 20% complete |
| Lattice (unified) | 🟡 30% complete |
| Domain Agents | ❌ 0% complete (Finance, Business, Research, Coding, Personal) |

See `docs/HEALTH.md` for full assessment.

## Research Foundations

| Source | Patterns Adopted |
|--------|------------------|
| [Sparfuchs QA](https://github.com/Sparfuchs-Corporation/sparfuchs-qa) | Multi-layer adapter, preflight gating, coverage babysitting, gap healing |
| [Cisco Skill Scanner](https://github.com/cisco-ai-defense/skill-scanner) | Pack-based composition, policy knobs, SARIF reporting |
| [Giskard OSS](https://github.com/Giskard-AI/giskard-oss) | Scenario-based testing, LLM checks, hierarchical scoring |

## License

MIT
