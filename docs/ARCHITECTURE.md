# The Agency — Architecture (Final)

## System Overview

**The Agency** is a decentralized multi-agent orchestration system. Three integrated layers:

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Athena Core** | Python | Domain agents, bridges, MemPalace, coordination |
| **Paperclip** | Node.js + React | Business Agent UI (companies, org chart, tasks) |
| **Mission Control** | Python Textual TUI | Unified UI for all agents (embeds Paperclip for Business) |

---

## Architecture Diagram

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

## Mission Control Pages (Textual TUI)

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

## Bridge HTTP API

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

## Paperclip Integration

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

### Agent Role Mapping

```yaml
# paperclip/config/agent-mappings.yaml
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

## Domain Agents

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

## Bridges (External AI Frameworks)

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

## Task Message Flow

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

## Build Plan (3 Parallel Tracks)

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

## Data Models

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

## Quality Assurance

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

## Competitive Analysis

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

### Where The Agency Wins
1. Truly decentralized (no central gateway bottleneck)
2. Domain agents baked in (8 specialized domains)
3. Local-first by design (all data on machine)
4. Orthogonal Security layer
5. Lightweight (runs on 2-core, 4GB)
6. Python-native (ML/data science)
7. Structured memory (MemPalace drawers)
8. No lock-in (not tied to Claude, MCP, LangGraph, VS Code)

### Gaps to Close (12)
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

## Glossary

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

## Pre-June 2026 Context

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

*Last updated: 2026-09-17 by Hermes Agent*
*Status: Architecture defined. Pre-June context extracted. Awaiting build instructions.*
