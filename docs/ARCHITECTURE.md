# The Agency — Architecture (canonical current + labeled history)

> **Normative direction:** `docs/SOURCE_CONSOLIDATED_BRIEF.md` (target design) and
> `docs/SPEC_AGENCY_CORE_RECONCILIATION.md` (code-backed gap matrix).
> This file states the **current** architecture first, then preserves older material
> explicitly labeled **HISTORICAL / SUPERSEDED** — it is not deleted, but it is not
> the current design.

## 0. Platform boundary (normative)

**ATHENA and The Agency are peer platforms.** ATHENA is **not** a subsystem,
dependency, parent process, or component of The Agency. The Agency operates
independently with its own agents, Agent Factory (planned), skills (planned),
workflows (planned), governance, Butler, and Forge (planned). Any future ATHENA
interaction is an explicit, optional adapter across a trust boundary — never a hidden
source of truth for Agency state. (Source: consolidated brief §2.)

Anything below that depicts "ATHENA Core" as the Agency's internal core, or routes
all Agency execution through ATHENA bridges/daemons, is **historical/superseded**.
It is preserved in §5+ for context, not as build guidance.

## 1. Canonical current Agency diagram (actual code)

```text
                    HUMAN
                      |
                    BUTLER                  human interaction + scoped context
                      |                     ButlerService.handle_message(message,
                      |                     sender, context); sender-scoped recall
                      |                     via chat-{sender}; NO independent
                      |                     thread/workspace store yet
                      |                     (src/agency/butler/service.py,
                      |                      router.py, server.py, config.py)
                      v
                ORCHESTRATOR                task submit + execute pipeline
                      |                     (src/agency/orchestrator.py,
                      |                      src/agency/kernel/tasks.py)
        +-------------+-------------+
        |                           |
      AGENTS                       TOOLS
      - research                    - registry + driver
        (agents/research/agent.py)  - builtin: web_search, web_fetch,
      - general                       memory_write, sandbox_exec
        (agents/general/agent.py)     (src/agency/tools/)
      - demo, planner, executor,    - policy-checked at the driver level
        loop, registry, verifier      (deterministic where possible)
        (agents/loop.py,              per brief §§9, 25)
         agents/registry.py,
         agents/verifier.py)
                      |
        +-------------+-------------+------------------+
        |             |             |                  |
      LATTICE       MEMORY      POLICIES/AUDIT      SANDBOX
      coordination/ scoped stores governance in code,
      governance    + retrieval not prompts
      substrate     (memory/sms/  (kernel/policies.py,
      (lattice/      store.py,     kernel/audit.py,
       api.py,       retrieval.py, evidence/,
       backends/     secrets.py,   risk/, security/
       sqlite.py,    lifecycle.py, red|blue|purple/)
       models.py,    cpr.py)
       governance.py              QA/adversarial review:
       reputation.py,              verifier + red/blue/purple
       factory.py,                 critics can block work
       config.py)                 (planned: formal blocking
                                  decision contract)
      governance: in-memory
      proposals + reputation-
      weighted votes, best-effort
      backend mirror; durable
      vote persistence PLANNED

   +----------------------------------------------------------+
   | PLANNED — drawn dashed, not implemented:                 |
   |  Agent Factory (versioned spec → validate → evaluate →   |
   |    register → deploy → retire)                           |
   |  Skill registry (immutable skill_id/version)             |
   |  Workflow registry + deterministic executor              |
   |    (bounded DAG, pinned versions, allowlisted ops)       |
   |  Forge — Agency-native software factory + coding agent   |
   |  Independent thread / workspace / project store          |
   +----------------------------------------------------------+
```

Cross-cutting (all partial unless noted): security, governance, policy, provenance,
auditability, observability, validation, adversarial QA, memory/context management
(brief §3). Deterministic execution first; LLMs only where judgment is required
(brief §§9, 25).

External agents/bridges (`src/agency/bridges/`: claude, codex, hermes, openclaw +
coordinator) are **optional integrations behind explicit adapters**, never
architectural dependencies (brief §21). `Athena-global-skills`
(`https://github.com/athena254/Athena-global-skills`) is a separate source of
patterns for future Forge/skills work — not a dependency, not auto-imported
(brief §11.4).

## 2. Component map: actual vs planned

| Component | State | Code evidence |
|-----------|-------|---------------|
| Butler gateway | Partial | `src/agency/butler/service.py` (routing, audit, `chat-{sender}` recall), `server.py`, `router.py`, `config.py`, `cli.py` |
| Orchestrator / tasks | Partial | `src/agency/orchestrator.py`, `src/agency/kernel/tasks.py`, `kernel/registry.py`, `kernel/identity.py` |
| Research agent | Exists, partial | `src/agency/agents/research/agent.py` (`ResearchAgent` + system prompt); prior "0%" claims were wrong; behavior unverified in this pass |
| General / demo / planner / executor / verifier | Exists, partial | `src/agency/agents/` |
| Tools (registry, driver, builtin web/memory/sandbox) | Partial | `src/agency/tools/` |
| Lattice + governance + reputation | Partial | `src/agency/lattice/api.py`, `backends/sqlite.py`, `models.py`, `governance.py`, `reputation.py`, `factory.py` |
| Memory (SMS store, retrieval, lifecycle, secrets, CPR) | Partial | `src/agency/memory/sms/` |
| Sandbox / security / audit / evidence / risk | Partial | `src/agency/security/sandbox/`, `security/red|blue|purple/`, `kernel/audit.py`, `kernel/policies.py`, `evidence/`, `risk/`; isolation guarantees unaudited |
| API server + CLI | Partial | `src/agency/api/server.py`, `api/routers/`, `src/agency/cli/main.py` (`agency` entry point) |
| CI workflow | Exists, outcome unverified | `.github/workflows/ci.yml`, `cd.yml` exist; success/live operation not verified here |
| Threads / workspaces / projects | Planned | No module under `src/agency`; other agents are implementing threads elsewhere — not claimed here |
| Agent Factory | Planned | Only identity + runtime registries; no versioned factory gate |
| Skill registry / workflow registry + executor | Planned | No Agency-native modules; other agents implementing elsewhere — not claimed here |
| Forge + native coding agent | Planned | `src/agency/addons/dark_factory/`, `ghost_factory` are standalone prototypes, not an integrated Forge |

No precise completion percentages are stated: the evidence does not warrant them.

## 3. What changed vs the historical docs (contradictions fixed)

- **ATHENA as internal core** (old §"System Overview", "ATHENA CORE (Python)" diagram,
  "Three Integrated Layers" with Athena Core): contradicts brief §2. Now labeled
  historical/superseded (§5+). Current system has no ATHENA dependency.
- **"ResearchAgent 0%"** (old README status table, old ROADMAP Phase 4): contradicts
  `src/agency/agents/research/agent.py`. Corrected to "exists, partial".
- **"CI/CD Pipeline 0% / planned"** (old ROADMAP Phase 5): contradicts
  `.github/workflows/ci.yml` + `cd.yml`. Corrected to "exists, outcome unverified".
- **"Production-ready" / "Fully functional" + absolute percentages** (old README/HEALTH/ROADMAP):
  unwarranted without evidence (brief §§47, 50: do not claim planned features as
  implemented). Replaced with coarse Implemented/Partial/Planned states.
- **Quickstart** (`python -m theagency.gateways.launcher`, `pip install -e ".[all]"`,
  `docker-compose -f docker-compose.sandbox.yml`): targets do not exist in this
  checkout (package is `src/agency`, extras are `dev/neo4j/qdrant/ollama/llm`, only
  `docker-compose.yml` exists). Removed; README lists verified commands only.

## 4. Target direction (summary, not status)

Per the consolidated brief: Butler with persistent multi-threaded workspaces;
Agent Factory owning the agent lifecycle; versioned skill and workflow registries
with deterministic execution; Lattice as deterministic coordination/governance
substrate; Forge as the Agency-native software factory operated by a bounded coding
agent; capability-based security, sandboxing, provenance, and audit enforced in code.
`Athena-global-skills` informs future skill patterns. See brief §§5–22 and the
reconciliation spec's build slice (thread store → skill registry → workflow
registry/executor → Agent Factory; Forge and full approvals later).

---

# HISTORICAL / SUPERSEDED MATERIAL (preserved, not current)

> Everything below this line is preserved historical specification. It contains the
> pre-reconciliation design in which ATHENA appeared as the Agency's internal core.
> Do not build from it without checking §§0–4 above and the two normative docs.
> It is kept so historical detail is not gratuitously lost.

## System Overview (HISTORICAL — superseded by §§0–2 above)

**The Agency** was previously described as a **Qubes OS-inspired decentralized
multi-agent architecture** where:

- **Athena Core** is a single, minimal agent capable of handling **any and all user requests** without addons
- **Domain Agents** (Personal, Work, Finance, Business, Learning, Technology, Social, Research, Security) handle broad life areas
- **Sub-Specialists** are spawned per-domain (e.g., Finance → InvestmentAgent, PersonalBanker, CryptoAgent, StocksExpert)
- **Addons/Nodes** act as "superpowers" — removable modules that multiply agent capability
- **External Coordinator** bridges Claude Code, Codex, Goose, OpenClaw, Hermes, AgentZero, DeerFlow, Pi agent, OpenCode

### Three Integrated Layers (HISTORICAL)

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Athena Core** | Python | Domain agents, bridges, SMS/Noesis, coordination |
| **Paperclip** | Node.js + React | Business Agent UI (companies, org chart, tasks) |
| **Mission Control** | Python Textual TUI | Unified UI for all agents (embeds Paperclip for Business) |

---

## Architecture Diagram (HISTORICAL — superseded; ATHENA-as-core is not the current design)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    MISSION CONTROL (Textual TUI)                     │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ [Dashboard] [IDE] [Memories] [Calendar] [Tasks]                ││
│  │ [Personal] [Work] [Finance] [Learning] [Technology]           ││
│  │ [Social] [Research] [BUSINESS]                                ││
│  │                                                                 ││
│  │  ═══════════════════════════════════════════════════════════   ││
│  │  Business Tab = WebView (Paperclip React UI on :5173)         ││
│  │  All other tabs = Native Textual widgets                       ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                                   │ Direct filesystem + HTTP
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ATHENA BRIDGE HTTP API (FastAPI)                   │
│                    http://localhost:8000                              │
│  POST /execute/{bridge}   → execute task via bridge                 │
│  GET  /bridges            → list available bridges                  │
│  GET  /agents/domain      → list domain agents                     │
│  POST /bridges/{b}/start  → start bridge daemon                     │
│  POST /bridges/{b}/stop   → stop bridge daemon                      │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ATHENA CORE (Python)                              │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ Domain Agents: Personal, Work, Finance, Business, Learning,   │ │
│  │                Technology, Social, Research                    │ │
│  ├────────────────────────────────────────────────────────────────┤ │
│  │ Bridges: Codex, Claude, Hermes, Gemini, OpenClaw,            │ │
│  │          AgentZero, DeerFlow 2.0, Pi, OpenCode, Goose        │ │
│  ├────────────────────────────────────────────────────────────────┤ │
│  │ MemPalace (ChromaDB)  │  Wings (filesystem)                   │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                                   │ HTTP (Paperclip)
                                   │ Shared PostgreSQL
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    PAPERCLIP (Forked, Node.js + React)                │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Companies (Holding)      Org Chart        Tasks (company)     ││
│  │  Dashboard: MRR, Headcount, Runway, Costs, Metrics            ││
│  └─────────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  BUILT-IN ADAPTER: "Athena"                                     ││
│  │  Maps: Paperclip "Software Engineer" → Work Agent (Codex)      ││
│  │         Paperclip "CFO" → Finance Agent (Claude)               ││
│  │         Paperclip "CEO" → Business Agent (Claude)              ││
│  │  Routes tasks → Athena HTTP API (:8000)                        ││
│  │  Streams results ← Bridge output                               ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

---

## Mission Control Pages (HISTORICAL — Textual TUI plan, not implemented)

| Page | Technology | Content |
|------|------------|---------|
| **Main Dashboard** | Textual widgets | All 8 domain agents + bridges status, activity feed, system health |
| **IDE** | Textual Terminal | PTY-attached bridges, chat, file browser, logs |
| **Memories** | Textual widgets | MemPalace explorer, search, delta reports |
| **Calendar** | Textual widgets | Schedule view, events, agent availability |
| **Tasks** | Textual widgets | Kanban board, drag-drop, filter by agent |
| **Personal** | Textual widgets | Habits, health, relationships |
| **Work** | Textual widgets | Projects, sprints, deliverables |
| **Finance** | Textual widgets | Budget, accounts, investments |
| **Learning** | Textual widgets | Courses, skills, growth |
| **Technology** | Textual widgets | Systems, infrastructure, tools |
| **Social** | Textual widgets | Network, community, events |
| **Research** | Textual widgets | Papers, experiments, knowledge gaps |
| **Business** | **WebView (Paperclip)** | Companies, org chart, business tasks, budgets |

---

## Bridge HTTP API (HISTORICAL — old Athena-bridge contract; current adapters live in `src/agency/bridges/`)

### Endpoints

```http
POST /v1/bridges/{bridge_type}/execute
Content-Type: application/json

{
  "task_id": "task_abc123",
  "content": "Implement OAuth login flow",
  "metadata": {
    "source": "paperclip",
    "agent_role": "software_engineer",
    "company_id": "comp_001",
    "priority": "high"
  }
}
```

Response (streaming SSE):
```json
{
  "output": "Here's the implementation...",
  "artifacts": ["src/auth/oauth.ts"],
  "usage": {
    "prompt_tokens": 1234,
    "completion_tokens": 567,
    "cost_usd": 0.042
  },
  "status": "completed",
  "duration_seconds": 42.1
}
```

### Other Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | /v1/bridges | List available bridges |
| POST | /v1/bridges/{type}/start | Start bridge daemon |
| POST | /v1/bridges/{type}/stop | Stop bridge daemon |
| GET | /v1/agents/domain | List domain agents (for Paperclip mapping) |
| GET | /v1/health | Health check |

### Auth

Simple API key: `Authorization: Bearer <key>`

---

## Paperclip Integration (HISTORICAL — TypeScript adapter sketch, not Agency-native)

### AthenaAdapter (TypeScript)

```typescript
// paperclip/packages/adapters/src/athena.ts
export class AthenaAdapter extends BaseAdapter {
  async executeTask(task: PaperclipTask): Promise<AdapterResult> {
    const mapping = this.config.mappings[task.agent.role];
    const bridgeType = mapping.bridge;

    const response = await fetch(`${this.config.athenaEndpoint}/execute/${bridgeType}`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${this.config.apiKey}` },
      body: JSON.stringify({
        task_id: task.id,
        content: task.description,
        metadata: { role: task.agent.role, company: task.companyId }
      })
    });

    const result = await response.json();
    return {
      status: result.status,
      output: result.output,
      cost: result.cost,
      artifacts: result.artifacts
    };
  }
}
```

### Agent Role Mapping (HISTORICAL)

```yaml
software_engineer:
  athena_agent: work
  bridge: codex
  budget_daily: 10.00

cfo:
  athena_agent: finance
  bridge: claude
  budget_daily: 8.00

ceo:
  athena_agent: business
  bridge: claude
  budget_daily: 15.00

cto:
  athena_agent: technology
  bridge: codex
  budget_daily: 12.00

research_analyst:
  athena_agent: research
  bridge: hermes
  budget_daily: 6.00
```

---

## Domain Agents (HISTORICAL roster — current code has research/general/demo agents only)

| Agent | Bridge | Specializations |
|-------|--------|-----------------|
| **Personal** | Claude | Habits, health, relationships, calendar |
| **Work** | Codex | Coding, projects, deliverables |
| **Finance** | Claude | Budgeting, accounts, investments |
| **Business** | Claude | Companies, revenue, strategy (CEO) |
| **Learning** | Claude | Courses, skills, growth |
| **Technology** | Codex | Infrastructure, DevOps, tools |
| **Social** | Claude | Network, community, events |
| **Research** | Hermes | Papers, experiments, knowledge |

---

## Bridges (HISTORICAL table — current adapters: `src/agency/bridges/` claude/codex/hermes/openclaw)

| Bridge | Type | Invocation | Effort |
|--------|------|------------|--------|
| **ClaudeCodeBridge** | Subprocess | `claude --loop --stdio` | Medium |
| **CodexBridge** | API | OpenAI API | Easy |
| **HermesBridge** | ACP | `hermes agent run` | Medium |
| **GeminiBridge** | Subprocess/API | `gemini` CLI or Google AI API | Medium |
| **OpenClawBridge** | HTTP API | `POST /api/agent` | Medium-Hard |
| **AgentZeroBridge** | Subprocess | Docker container | Hard |
| **DeerFlowBridge** | HTTP API + SSE | `POST /v1/flow` | Hard |
| **PiAgentBridge** | Subprocess/API | `pi-agent` CLI | Easy-Medium |
| **OpenCodeBridge** | Subprocess | `opencode` CLI | Easy |
| **GooseBridge** | ACP | `goose run` | Medium |

---

## Task Message Flow (HISTORICAL — Paperclip-originated flow)

```
Paperclip UI: Create task "Implement OAuth login"
    ↓
Paperclip backend: POST /api/companies/comp_001/tasks
    ↓
Assign to agent: "Software Engineer" (role: software_engineer)
    ↓
AthenaAdapter receives task
    ↓
Lookup mapping: software_engineer → Work Agent + CodexBridge
    ↓
Translate to Athena TaskMessage:
    {
      "task_id": "paperclip-123",
      "type": "code_generation",
      "content": "Implement OAuth login flow...",
      "metadata": {
        "source": "paperclip",
        "company": "comp_001",
        "agent_role": "software_engineer"
      }
    }
    ↓
POST to Athena HTTP API: /execute/codex
    ↓
CodexBridge executes (subprocess or API)
    ↓
Streams output back via HTTP
    ↓
AthenaAdapter translates to Paperclip result
    ↓
Paperclip posts comment: "✅ Code generated. Cost: $0.42"
    ↓
Task status: completed
```

---

## Build Plan, 3 Parallel Tracks (HISTORICAL — superseded by brief §§45–46 and the reconciliation build slice)

### Track 1: Athena Bridge HTTP API (2 days)
- [ ] FastAPI server with bridge execution endpoint
- [ ] Bridge management endpoints
- [ ] Domain agent listing
- [ ] API key auth + CORS
- [ ] Mock tests

### Track 2: Paperclip Fork + AthenaAdapter (2-3 weeks)
- [ ] Fork Paperclip → athena-paperclip/
- [ ] Add AthenaAdapter (TypeScript)
- [ ] Create agent-mappings.yaml
- [ ] Modify agent creation UI for "Athena Domain Agent"
- [ ] Set up shared PostgreSQL
- [ ] Test end-to-end

### Track 3: Mission Control (6 weeks)
- [ ] Dashboard, IDE, Memories, Calendar, Tasks pages
- [ ] Personal, Work, Finance, Learning, Technology, Social, Research pages
- [ ] Business page (WebView embedding Paperclip)
- [ ] Polish, themes, plugins

---

## Data Models (HISTORICAL sketches — current models live in `src/agency/kernel/`, `src/agency/lattice/models.py`)

### TaskMessage

```python
@dataclass
class TaskMessage:
    task_id: str
    type: str                    # "code_generation", "analysis", etc.
    content: str                 # The actual task description
    metadata: dict = field(default_factory=dict)
    # metadata contains: source, agent_role, company_id, priority, budget
    created_by: str = "system"
    created_at: datetime = field(default_factory=datetime.utcnow)
```

### ResultMessage

```python
@dataclass
class ResultMessage:
    task_id: str
    bridge_type: str
    status: str                  # "completed", "failed", "timeout"
    output: str
    artifacts: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    # usage contains: prompt_tokens, completion_tokens, cost_usd
    duration_seconds: float = 0.0
    error: Optional[str] = None
    bridge_id: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
```

### CapabilityAdvertisement

```python
@dataclass
class CapabilityAdvertisement:
    bridge_type: str
    capabilities: list[str]      # ["code_generation", "analysis", ...]
    supported_task_types: list[str]
    supports_streaming: bool
    supports_tools: bool
    metadata: dict = field(default_factory=dict)
```

---

## Quality Assurance (HISTORICAL — current QA: verifier + red/blue/purple critics)

### QA Critic
- 10 quality dimensions (correctness, security, completeness, etc.)
- Severity-weighted scoring (critical=4x, high=3x, medium=2x)
- Enforcement actions: ALLOW, BLOCK, RETRY, ESCALATE, etc.

### Built-in Rules
- output_not_empty (HIGH)
- no_sensitive_leak (CRITICAL)
- execution_within_limits (MEDIUM)
- error_free_execution (HIGH)

### Test Approach
- 100% mock-tested (no live dependencies)
- All bridges tested with mocked subprocess/API calls
- Integration tests use temporary directories and JSON fallback

---

## Competitive Analysis (HISTORICAL research notes — brief §22 remains the normative positioning)

### Competitors Researched (10)
1. **agent-zero** — Secure containerized agent
2. **goose** — Multi-provider MCP agent (43.6k stars)
3. **cline** — IDE-integrated coding (20.5k stars)
4. **OpenHands** — Sandboxed code execution (28.3k stars)
5. **OpenCode** — Terminal LSP-native coding
6. **DeerFlow** — Production LangGraph agent
7. **pi-mono** — Clean modular monorepo
8. **Hermes** — Multi-agent protocol + memory
9. **OpenClaw** — Multi-channel inbox + device automation
10. **PAI** — Persistent learning AI

### Where The Agency Wins (HISTORICAL claims — validate through implementation per brief §22)
1. Truly decentralized (no central gateway bottleneck)
2. Domain agents baked in (8 specialized domains)
3. Local-first by design (all data on machine)
4. Orthogonal Security layer
5. Lightweight (runs on 2-core, 4GB)
6. Python-native (ML/data science)
7. Structured memory (MemPalace drawers)
8. No lock-in (not tied to Claude, MCP, LangGraph, VS Code)

### Gaps to Close (12) (HISTORICAL)
1. Provider diversity (P0)
2. Sandbox isolation (P0)
3. Extension marketplace (P1)
4. IDE integration (P1)
5. Multi-tenant SaaS (P1)
6. Web/Desktop UI (P2)
7. LSP code intelligence (P2)
8. Tool breadth (P2)
9. Human-in-the-loop (P3)
10. Observability (P3)
11. Cloud scalability (P4)
12. Test coverage (ongoing)

---

## Glossary (HISTORICAL — partially superseded; Butler/Lattice/QA meanings updated in §§0–2)

| Term | Definition |
|------|------------|
| **Lattice** | Shared graph database (Neo4j + Qdrant) |
| **Butler** | Gateway agent — dumb message relay |
| **Buddy** | UI rendering agent (forked Space-Agent) |
| **Clean Room** | Sandbox Mode 1 — empty Python environment |
| **Agency Mirror** | Sandbox Mode 2 — full codebase clone |
| **QA Critic** | Quality enforcement system |
| **Canary** | Lightweight smoke test |
| **Bridge** | External AI framework wrapper |
| **AgentBuilder** | Fluent utility for child agent construction |
| **Paperclip** | Business Agent UI (forked, Node.js + React) |
| **Mission Control** | Unified TUI (Python Textual, embeds Paperclip) |
| **AutoDream** | Background memory consolidation |
| **MemPalace** | Structured memory system (drawers) |
| **Jack's Golden Rule** | "If it's not in the wing, it doesn't exist" |
| **Wing** | Per-agent isolated storage (~/.athena/wings/{name}/) |
| **Drawer** | Memory container within a wing |

---

## Pre-June 2026 Context (HISTORICAL extraction notes)

**Full extraction from chat exports (12,880 messages):** See `docs/EXTRACTED_CONTEXT.md`

Key additions from pre-June chats:
- **Complete agent roster** with responsibilities (11 agents including Kael, Zoey, Clara, Teresa, Onyx, JACK, Remex, Qore, Umbral, Nexus, charlie)
- **Built addons:** auto_research (Karpathy), simulation (MiroFish), QA suite (Jack)
- **Project reorganization:** `ADDONS/` → `skills/`, SMS nodes → `core/sms/`, `CORE/` → `core/`
- **Concurrent execution design:** all addons support multi-agent parallel execution
- **Git adoption:** project under version control, Forge auto-commits every 15 min
- **External integrations:** Postiz (social media), Buddy (Space-Agent fork), SearXNG
- **Infrastructure:** WSL Kali, Docker Desktop, VPS target at 172.238.240.113

---

*Historical section preserved from the pre-reconciliation architecture doc (last updated
2026-09-17). Current architecture is §§0–4 above.*
