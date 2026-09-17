# Pre-June 2026 Context Extraction

> **Source:** Kotatogram Desktop chat export (12,880 messages before June 1, 2026)
> **Date range:** April 10 – May 31, 2026
> **Extracted:** 2026-09-17

---

## 1. Agent Roster & Responsibilities

| Agent | Messages (pre-Jun) | Primary Role | Key Activities |
|-------|-------------------|--------------|----------------|
| **Kael ⚡️** | 6,148 | Core agent / daily briefings | News curation (worldmonitor.app, HN, daily.dev), OpenClaw integration, AgentZero research, MCP support |
| **Zoey 😜** | 2,514 | Research & installation | DeerFlow 2.0 research, AgentZero install, multi-agent coordination |
| **Clara** | 991 | Buddy / Space-Agent fork | Buddy UI fork from agent0ai/space-agent, Mission Control design, Butler+Buddy integration |
| **Teresa** | 803 | Build agent | Project reorganization, auto_research addon, simulation addon, git adoption |
| **Onyx** | 739 | System administration | Kali WSL setup, OSINT tools, free model research (Qwen 3.6), Obsidian vault access |
| **JACK** | 652 | QA / Critic | QA skill suite (6 scanners), competitive analysis, bridge wrappers |
| **Remex** | 596 | Memory systems | Memory architecture research, MemPalace, SMS (Sovereign Mind System), noesis migration |
| **Qore** | 77 | QA officer | Git commit monitoring, code audits, standards enforcement |
| **Umbral** | 52 | OSINT/SIGINT | Kali Linux WSL, OSINT tools installation, 3D timeline mapping project |
| **Nexus** | 151 | System monitoring | Disk cleanup, health reports, self-reflection reports |
| **charlie** | 157 | Task agent | Postiz self-hosted installation |

---

## 2. Key Projects Built Pre-June

### 2.1 Auto-Research Addon (Teresa)
- Based on Karpathy's `autoresearch` repo (experiment loop pattern)
- 18 files, production-scaffolded
- Features: Git integration, metric evaluation, experiment runner, concurrent multi-agent support
- CLI: `python -m addons.auto_research run --workdir .`
- Demo project created at `/root/project-athena/demo_auto_research/`
- Concurrent execution: personal + work agents run in parallel, share insights via Lattice

### 2.2 Simulation Addon (Teresa)
- Based on MiroFish-Offline (nikmcfly/MiroFish-Offline)
- 23 Python modules, multi-agent swarm simulation
- Features: OASIS profile generation, Neo4j knowledge graph, parallel execution, report agent (ReACT pattern)
- Demo project created at `/root/project-athena/demo_simulation/`
- Supports per-agent Neo4j DB isolation for concurrent use

### 2.3 QA Skill Suite (Jack)
- 6 scanners: code_style, import, test_coverage, architecture, security, doc
- HTML reporter + CLI (`python scripts/athena-qa`)
- First scan: 719 issues found (403 code style, 312 documentation, 3 security)

### 2.4 Project Reorganization (Teresa)
- Moved `ADDONS/` → `skills/` (canonical core skills)
- Created root-level `addons/` for cross-platform domain addons
- Moved SMS nodes from `addons/sms_nodes/` → `athena/core/sms/`
- Renamed `CORE/` → `core/` (lowercase, matching 100+ imports)

---

## 3. Architecture Evolution

### 3.1 Project Identity
**ATHENA = Autonomous Task-Handling Engine for Networked Agents**
- Decentralized, self-governing multi-agent system
- Shared graph database = **Lattice** (Neo4j + Qdrant)
- Built-in governance: voting, peer scoring, auto-healing

### 3.2 Technology Stack
| Component | Choice |
|-----------|--------|
| Lattice DB | Neo4j (graph) + Qdrant (vector) |
| Language | Python 3.11+ (agents), TypeScript (tooling) |
| LLM | Pluggable (Ollama local, OpenAI/Anthropic cloud) |
| Message Bus | Redis Streams (optional) |
| Policy Engine | Cedar (ACL) |
| Observability | OpenTelemetry |
| Deployment | Docker → K8s |

### 3.3 Target Directory Structure
```
/root/project-athena/
├── athena/
│   ├── core/                    # SMS nodes (lattice, librarian, cpr, spawn, retrieval)
│   ├── skills/                  # Core infrastructure skills (canonical)
│   ├── DOMAIN_AGENTS/           # Finance, Business, Research, etc.
│   ├── GATEWAYS/                # Butler + platform bridges
│   ├── ADDONS/                  # (removed — merged to skills/)
│   └── DOCS/
├── addons/                      # Cross-platform domain addons (standalone)
│   ├── noesis/                  # Coordinated memory
│   ├── simulation/              # MiroFish scenario engine
│   ├── auto_research/           # Karpathy experiment loop
│   ├── dark_factory/            # Tool & code generation
│   ├── adversarial/             # Red teaming
│   ├── self_rectification/      # Self-critique
│   ├── sandbox/                 # Isolated execution
│   ├── efficient_orchestrator/  # Small-model optimization
│   └── disposable-agent-runner/ # Short-lived agents
├── FORGE/                       # Department coordination (7 agents)
├── NODES/                       # Sovereign node bundles
├── CONFIG/                      # YAML configs
└── tests/
```

---

## 4. Key Decisions & Principles

### 4.1 Concurrent Execution Design
> "Make sure if the personal agent and the work agent are both using auto research it works concurrently in parallel in both Agents to know they can improve everything with a metric to it"

- All addons must support multi-agent parallel execution
- Agent ID namespacing for branch isolation
- Shared Lattice for cross-agent insights

### 4.2 Git Adoption
- Project under Git version control at `/root/project-athena/`
- Forge department auto-commits every 15 minutes
- Commit message format: `Agent <name>: <desc> — <timestamp>`

### 4.3 No Live Tests Pre-MVP
> "Since Athena project has not reached an mvp what can we do that does not include live tests"

- Scaffolded code with valid imports
- Tests written but may have mocking issues
- Manual smoke tests preferred over live pytest

### 4.4 Cross-Platform Addons
> "Understand these addons are independent on their own and can be used by other agents eg claudecode, codex, hermes etc but built to work natively in Athena too"

- Addons are standalone npm packages for AI agents
- No Athena coupling in addon code

---

## 5. External Integrations & Research

### 5.1 Postiz (Self-Hosted Social Media)
- Target: `http://localhost:4007`
- Docker compose stack with Redis, PostgreSQL, Temporal
- Integrations needed: X, LinkedIn, Facebook, Reddit
- OAuth provider configuration required per platform

### 5.2 Buddy (Space-Agent Fork)
- Forked from `github.com/agent0ai/space-agent`
- Two-page Mission Control design
- Renders custom components for domain agents via `render_via: buddy` protocol

### 5.3 Competitors Researched
1. agent-zero — Secure containerized agent
2. goose — Multi-provider MCP agent (43.6k stars)
3. cline — IDE-integrated coding (20.5k stars)
4. OpenHands — Sandboxed code execution (28.3k stars)
5. OpenCode — Terminal LSP-native coding
6. DeerFlow — Production LangGraph agent
7. pi-mono — Clean modular monorepo
8. Hermes — Multi-agent protocol + memory
9. OpenClaw — Multi-channel inbox + device automation
10. PAI — Persistent learning AI on Claude Code

### 5.4 Pulse Analysis
- 760,000-line TypeScript codebase studied
- Key innovations: AutoDream (memory consolidation), MCP client, IDE bridge
- Integration strategy documented in `PULSE_ANALYSIS_AND_INTEGRATION.md`

---

## 6. Infrastructure & DevOps

### 6.1 Running Services (as of May 2026)
- `hermes-gateway-jack.service` — Hermes gateway
- `architect-agent.service` — OpenClaw architect agent
- Sandboxed agent processes (5) via `spawn.sandbox`
- Docker Desktop required for containerized agents

### 6.2 Environment
- **Development:** WSL (Kali Linux) on Windows 11
- **Production target:** VPS at `172.238.240.113`
- **Models:** Nous Portal (free tier: Qwen 3.5 Plus, Mistral, Cohere)
- **SearXNG:** Self-hosted search engine installed

### 6.3 System Health (May 2026)
- Disk: 94% used (critical, cleanup ongoing)
- Memory: 89% used
- Agent processes consuming significant resources

---

## 7. Current Gaps & Blockers (Pre-June)

| Gap | Priority | Status |
|-----|----------|--------|
| Unified Lattice Service | Critical | 30% — Neo4j + Qdrant integration incomplete |
| Governance Module | Critical | 20% — Voting/reputation not wired |
| Sandbox Isolation | P0 | Docker per subagent not fully working |
| Provider Diversity | P0 | Only Nous Portal configured |
| Bridge Implementation | P1 | 100% infrastructure, 3 functional bridges |
| FinanceAgent | P1 | 0% — Sprint 2 target |
| AutoDream | Planned | Sleep cycles not implemented |
| MCP Client | Planned | Not started |

---

## 8. Naming Conventions & Glossary

| Term | Definition |
|------|------------|
| **ATHENA** | Autonomous Task-Handling Engine for Networked Agents |
| **Lattice** | Shared graph database (Neo4j + Qdrant) |
| **Butler** | Gateway agent — dumb message relay |
| **Buddy** | UI rendering agent (forked Space-Agent) |
| **SMS** | Sovereign Mind System (memory system) |
| **Wing** | Per-agent isolated storage |
| **Clean Room** | Sandbox Mode 1 — empty Python environment |
| **Agency Mirror** | Sandbox Mode 2 — full codebase clone |
| **AutoDream** | Background memory consolidation |
| **MemPalace** | Structured memory system (drawers) |
| **Jack's Golden Rule** | "If it's not in the wing, it doesn't exist" |

---

*Extracted from Kotatogram Desktop export by Hermes Agent*
*Total: 12,880 messages, 13 chat exports, 11 agents*
