# Nexus — Roadmap

## Phase 1: Foundation (Complete ✅)

| Item | Status | Notes |
|------|--------|-------|
| Secrets Manager | ✅ | Encrypted credential storage with ACL |
| Retrieval System | ✅ | Multi-mode search (semantic + graph + hybrid) using Neo4j |
| Spawn System | ✅ | Secure sub-agent lifecycle with sandboxing |
| LLM Harness | ✅ | Pluggable provider abstraction (OpenAI, Anthropic, Ollama) |
| Test Harness | ✅ | Unified testing framework |
| Base Agent | ✅ | Common agent foundation with lattice integration |

## Phase 2: Addons (Complete ✅)

| Item | Status | Notes |
|------|--------|-------|
| Sandbox Addon | ✅ | Docker/process backends, HTTP API, standalone service |
| QA Critic Addon | ✅ | Quality enforcement with parallel rule execution |
| Auto-Research | ✅ | Autonomous research loop |
| Simulation | ✅ | Graph-based social simulation |
| Dark Factory | ✅ | Self-improvement with A/B testing |
| Bridge System | ✅ | 100% infrastructure, 3 functional bridges |
| Gateway System | ✅ | Three-mode Butler architecture |
| Buddy UI | ✅ | Forked Space-Agent, two-page Mission Control |

## Phase 3: Core Infrastructure (In Progress 🟡)

| Item | Status | Priority |
|------|--------|----------|
| Unified Lattice Service | 🟡 30% | **Critical** — Central coordination DB |
| Governance Module | 🟡 20% | **Critical** — Voting, reputation, escalation |
| Gatekeeper | 🟡 40% | Auth/routing logic incomplete |
| Librarian | 🟡 20% | Normalization/deduplication missing |
| Dream | 🟡 20% | Sleep cycles not implemented |
| CPR (Compression) | 🟡 20% | Core logic minimal |
| Orchestrator | 🟡 60% | Integration incomplete |
| Aether (Durable Execution) | 🟡 30% | Wrapper exists, full system missing |

## Phase 4: Domain Agents (Not Started ❌)

| Item | Status | Target Sprint |
|------|--------|---------------|
| FinanceAgent | ❌ 0% | Sprint 2 |
| ├─ MarketAnalyst | ❌ | |
| ├─ PortfolioManager | ❌ | |
| ├─ RiskAssessor | ❌ | |
| └─ NewsAggregator | ❌ | |
| BusinessAgent | ❌ 0% | Sprint 3 |
| ResearchAgent | ❌ 0% | Sprint 3 |
| CodingAgent | ❌ 0% | Sprint 4 |
| PersonalAgent | ❌ 0% | Sprint 4 |

## Phase 5: Integration & Production (Planned)

| Item | Status | Target |
|------|--------|--------|
| Noesis Shim (OpenClaw compat) | ❌ 0% | Sprint 5 |
| Micro-Webs (subgraphs) | ❌ 0% | Sprint 5 |
| Docker/K8s Deployment | ❌ 0% | Sprint 6 |
| CI/CD Pipeline | ❌ 0% | Sprint 6 |
| OpenTelemetry Observability | ❌ 0% | Sprint 6 |
| Security Audit | ❌ 0% | Sprint 6 |

## Current Sprint Focus

**Sprint 2 (Weeks 3-7): Lattice + Governance + Finance domain**

Actual progress: ~30% toward sprint goal

What needs to happen:
1. **Unified Lattice Service** (2 weeks) — Neo4j + vector DB, unified API
2. **Governance Module** (2 weeks) — voting, reputation, escalation
3. **FinanceAgent + 4 sub-agents** (3 weeks) — domain specialists
4. **Integration & E2E tests** (1 week)

Total remaining: ~8 weeks minimum

## Timeline Estimate

| Milestone | Original | Revised |
|-----------|----------|---------|
| v0.1.0 (Foundation) | Complete | ✅ Complete |
| v0.2.0 (Addons) | Complete | ✅ Complete |
| v0.3.0 (Finance Alpha) | Sprint 2 | 8+ weeks |
| v0.4.0 (Multi-domain) | Sprint 3 | 12+ weeks |
| v1.0.0 (Production) | Sprint 6 | 20+ weeks |
