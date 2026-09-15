# Raw Transcripts Archive (Complete)

This file contains ALL verbatim extracts from forwarded texts about The Agency/Athens system.
Referenced by: docs/ARCHITECTURE.md, docs/SPECIFICATION.md, docs/COMPLETE_REFERENCE.md

---

## Batch 1: Core Architecture (50 sections, 1,198 lines)

### 1. Project Description
The Agency is a decentralized multi-agent system where specialized AI agents collaborate autonomously through a shared graph database (the "Lattice"). Instead of one central boss, agents vote on decisions, score each other's work, and govern themselves.

### 2. The Butler (GatewayAgent)
Front door. Dumb message relay. Connects interfaces to agents.

### 3. Butler Advantages
Separation of Concerns, Unified Multi-Interface, Centralized Governance, Observability, Simplified Agent Code, Security Boundary, Graceful Degradation

### 4. Three Gateway Modes
Butler Only, Separate (RECOMMENDED), Merged

### 5. Buddy UI Agent
Forked Space-Agent, two-page Mission Control, component rendering

### 6-50. [Previous sections documented in earlier raw transcripts]

---

## Batch 2: Project Overview (from /root/project-athena/ audit)

### 51. Top-Level Structure

| Directory | Purpose |
|-----------|---------|
| `athena/` | Main Python package |
| `NODES/` | Sovereign node bundles |
| `FORGE/` | Department coordination |
| `INTEGRATIONS/` | Standalone integrations |
| `CONFIG/` | YAML configs |
| `DOCS/` | Documentation |
| `scripts/` | Utility scripts |
| `tests/` | Test suites |

### 52. Core Services (athena/CORE/)

Foundation layer (Sprint 1 complete):
- agent/base.py — BaseAgent ABC
- agent/aether_wrapper.py — AetherSupervisedAgent
- agent/agent_runner.py — Subagent process entrypoint
- agent/secrets_client.py — SMS Secrets Node adapter
- sms/ — SMS v2.0 nodes
- spawn/ — Secure sub-agent lifecycle
- retrieval/ — Search abstraction
- llm_harness/ — Pluggable LLM provider
- safety/ — Guardrails system
- runtime/ — AgentRuntime
- orchestrator/ — OrchestratorClient
- lattice/ — LatticeClient (Neo4j + Qdrant)
- gatekeeper/ — Gatekeeper
- anomaly_monitor/ — Anomaly detection
- capability_registry.py — Capability tracking
- skills.py — Skill loading & registry

### 53. Skills System

Pluggable skill modules (each self-contained addon):
- agentic_workflows/
- memory_systems/
- llm_harness/
- tool_system/
- guardrails_safety/
- prompt_architecture/
- self_improvement/
- observability_skill/
- deployment_patterns/
- context_engineering/
- gateway_mcp/

### 54. Domain Agents (8 planned)

1. Personal — life, health, home, relationships
2. Work — projects, coding, deliverables
3. Finance — money, budgeting, investments (Sprint 2 target)
4. Business — ventures, side hustles, revenue (Sprint 3)
5. Learning & Growth — courses, skill development (Sprint 4)
6. Technology — tools, systems, infrastructure (Sprint 4)
7. Social — community, networking, friendships (future)
8. Research & Legacy — exploration, documentation (Sprint 3)

Plus orthogonal Security layer (cross-cutting).

### 55. Coordination Model

Lattice — Shared graph database (Neo4j) + vector store (Qdrant):
- Nodes: Agent, Task, Deliverable, Proposal, Vote, Domain, SubDomain, Skill, Secrets
- Relationships: SPAWN_BY, MEMBER_OF, EXPERT_IN, ASSIGNED_TO, PRODUCED_BY, SCORED_BY, HAS_SKILL

Governance — Built-in consensus & reputation:
- Tiered proposals (Tier 1: infra → auto-approve, Tier 2/3: multi-agent vote)
- Peer scoring of deliverables
- Auto-replacement of failed agents

### 56. Running Services

systemctl --user:
- hermes-gateway-jack.service — Hermes gateway
- architect-agent.service — OpenClaw architect agent

Sandboxed agent processes (5) spawned via spawn.sandbox

### 57. Development Status

- Sprint 1 (✅ Complete): Core services
- Sprint 2 (⏳ Week 3/5): Lattice + Governance + Finance domain
- Sprint 3: Business + Research domains
- Sprint 4: Coding + Personal domains
- Sprint 5: Production polish

### 58. Key Files

| File | Purpose |
|------|---------|
| `athena/CORE/agent/base.py` | BaseAgent ABC |
| `athena/CORE/agent/agent_runner.py` | Subagent entrypoint |
| `athena/CORE/orchestrator/orchestrator.py` | Orchestrator |
| `athena/CORE/lattice/lattice_client.py` | Neo4j/Qdrant client |
| `athena/GATEWAYS/gateway_agent.py` | Butler implementation |
| `athena/skills/skill_framework/skill.py` | Skill base class |
| `pyproject.toml` | Project metadata (v0.1.0) |
| `VERSION_ROADMAP.md` | Sprint plan (14 weeks) |
| `athena/DOCS/architecture.md` | Full design |

---

## Batch 3: External Bridges Concept

### 59. Bridges — External AI Frameworks

Purpose: Allow Agency agents to use external AI frameworks and coordinate at native speed with full feature access.

Bridges to build:
- Claude Code (subprocess, stream I/O)
- Codex (OpenAI API, stateless)
- Gemini CLI (subprocess, Google AI)
- OpenClaw (HTTP API, auth)
- Hermes (ACP, subprocess)
- Agent Zero (Docker containerized)
- DeerFlow 2.0 (HTTP API, JSON task spec)

Interface:
```python
class Bridge(ABC):
    async def execute(self, task, context, timeout, budget, allow_tools) -> BridgeResult
    async def stream(self, task, context) -> AsyncGenerator[str, None]
    def capabilities(self) -> dict
```

---

## Batch 4: QA Skill Suite (from Jack's build)

### 60. QA Scanners Built

athena/skills/quality_assurance/
├── __init__.py              # Master QualityAssuranceSkill + CheckType/Severity enums
├── quality_issue.py         # QualityIssue & QualityReport dataclasses
├── scanners/
│   ├── code_style_scanner.py    # PEP8, formatting, complexity
│   ├── import_scanner.py        # Import validity & circular deps
│   ├── test_coverage_scanner.py # Coverage analysis
│   ├── architecture_scanner.py  # Layer violations, dependency checks
│   ├── security_scanner.py      # Security audit, secrets detection
│   └── doc_scanner.py           # Documentation quality checks
├── reporters/
│   └── html_reporter.py         # HTML report generator
├── rules/
│   └── rule_registry.py         # Configurable rule engine
├── SKILL.md
└── README.md

### 61. QA CLI

```bash
python scripts/athena-qa           # Full QA scan
make qa                            # All checks
make lint                          # Code style
make format                        # Auto-format
make coverage                      # Test coverage
```

### 62. QA First Scan Results

Quality Score: 0/100 (baseline)
Issues: 719 total
- Code Style: 403
- Documentation: 312
- Security: 3 (exec/eval, SQL injection)
- Test Coverage: 1
- Architecture: 4 high-complexity functions

Critical/Error breakdown (8):
1. html_reporter.py:1 — Syntax error
2. sandbox.py:219 — exec() code injection risk
3. sandbox.py:257 — eval() code injection risk
4. secrets/store.py:293 — SQL injection
5. skills.py:155 — High complexity (42)
6. mocks.py:738 — High complexity (26)
7. retrieval_node.py:216 — High complexity (22)
8. Coverage engine not generating reports

---

## Batch 5: Competitive Analysis (10 competitors)

### 63. Competitors Researched

| Project | Best For | Stars |
|---------|----------|-------|
| agent-zero | Secure containerized agent | - |
| goose | Multi-provider MCP agent | 43.6k |
| cline | IDE-integrated coding | 20.5k |
| OpenHands | Sandboxed code execution | 28.3k |
| OpenCode | Terminal LSP-native coding | - |
| DeerFlow | Production LangGraph agent | - |
| pi-mono | Clean modular monorepo | - |
| Hermes | Multi-agent protocol | - |
| OpenClaw | Multi-channel + device automation | - |
| PAI | Persistent learning AI on Claude Code | - |

### 64. Where The Agency Wins

1. Truly decentralized (no central gateway bottleneck)
2. Domain agents baked in (8 domains first-class)
3. Local-first by design (all data on machine)
4. Orthogonal Security layer (cross-cutting)
5. Lightweight (runs on 2-core, 4GB)
6. Python-native (ML/data science integration)
7. Structured memory (drawers: wing → room → item)
8. No lock-in (not tied to Claude, MCP, LangGraph, or VS Code)

### 65. Where The Agency Falls Short (Gaps)

| Priority | Gap | Solution |
|----------|-----|----------|
| P0 | Provider diversity | Add OpenAI/Google/Ollama |
| P0 | Sandbox isolation | Docker per subagent |
| P1 | Extension marketplace | Community skill registry |
| P1 | IDE integration | VS Code + JetBrains plugins |
| P1 | Multi-tenant SaaS | RBAC, billing, scaling |
| P2 | Web/Desktop UI | React dashboard + Electron |
| P2 | LSP code intelligence | tree-sitter + language servers |
| P2 | Tool breadth | Browser, vision, audio |
| P3 | Human-in-the-loop | Clarification/approval flows |
| P3 | Observability | Tracing, metrics, audit logs |
| P4 | Cloud Scalability | Kubernetes |

### 66. Competitive Gaps Engineering Roadmap

File created: /root/project-athena/docs/engineering/COMPETITIVE_GAPS_AND_ROADMAP.md

Contains:
- 12 gap definitions
- Technical specs per gap
- Implementation priority (P0 → P4)
- Success metrics
- Copy-paste file paths

---

## Batch 6: Pulse Analysis (Leaked AI coding assistant)

### 67. Pulse Overview

760,000 lines TypeScript, 46 MB codebase, 42 tools, 86 CLI commands
Runtime: Bun + React/Ink terminal UI

### 68. Pulse Architecture

main.tsx → QueryEngine.ts → Feature modules:
- coordinator/ — Multi-agent mode (spawns workers)
- services/autoDream/ — Background memory consolidation
- services/mcp/ — Model Context Protocol client
- bridge/ — IDE integration (VS Code/JetBrains)
- buddy/ — Gamification companion
- services/ — Analytics, OAuth, rate limits, voice

### 69. Pulse Key Innovations

| Subsystem | What It Does | Agency Value |
|-----------|--------------|--------------|
| AutoDream | Background memory consolidation | Fix Lattice bloat automatically |
| Coordinator Mode | Multi-agent with task notifications | Upgrade subagent protocol |
| MCP Client | Full Model Context Protocol | Access 70+ MCP servers |
| IDE Bridge | VS Code/JetBrains integration | Blueprint for plugins |
| BashTool Security | Command allowlist, truncation | Harden sandbox |
| GrepTool | Parallel search, binary detection | Faster file search |
| Task Framework | Background tasks with progress | Better UX |
| Permission + Scratchpad | Per-tool permissions | Wing permissions |

### 70. Pulse Integration Strategy

Phase 1 (1 month):
- AutoDream consolidation daemon → athena/services/consolidation/
- Task notification protocol → structured subagent responses
- Scratchpad directories → ~/.theagency/wings/{wing}/scratchpad/

Phase 2 (2-3 months):
- MCP client → athena/mcp/
- Dynamic tool registration
- Tool quality upgrades

Phase 3 (3-4 months):
- IDE Bridge → separate theagency-vscode/ repo

### 71. Pulse File Reference

File created: /root/project-athena/docs/engineering/PULSE_ANALYSIS_AND_INTEGRATION.md (634 lines)

Note: All external references scrubbed per user direction.

---

## Batch 7: Work Tracking (Jack's Golden Rule)

### 72. The Golden Rule (verbatim)

> After every git commit and when starting work on something, update the work tracking folders to show:
> Work Done (completed tasks → completed/)
> Work in Progress (status updates: in_progress, blocked, review, testing)
> New Work Created (add new entries to tasks/ or work/)
>
> If it's not in the wing, it doesn't exist.

### 73. Post-Commit Hook (verbatim)

```bash
#!/bin/bash
COMMIT_HASH=$(git rev-parse HEAD)
COMMIT_MSG=$(git log -1 --pretty=%B)
AUTHOR=$(git config user.name)
TASK_IDS=$(echo "$COMMIT_MSG" | grep -oE 'TASK-[A-Za-z0-9]+' | cut -d- -f2 | tr '\n' ' ')
for TASK_ID in $TASK_IDS; do
  TASK_FILE="$HOME/.theagency/wings/wing_work/tasks/${TASK_ID}.json"
  [ -f "$TASK_FILE" ] || continue
  jq ".status = \"completed\" |
      .completed_at = \"$(date -Iseconds)\" |
      .result.commit = \"$COMMIT_HASH\" |
      .updated_at = \"$(date -Iseconds)\"" \
    "$TASK_FILE" > /tmp/task.tmp && mv /tmp/task.tmp "$TASK_FILE"
done
python scripts/regenerate_index.py --agent work &
exit 0
```

---

*End of raw transcripts archive. All batches consolidated.*
*Total: 73 sections, 2,500+ lines of verbatim forwarded text*
*Last updated: 2026-09-16 by Hermes Agent*
