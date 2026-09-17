# The Agency — Complete System Specification

> **Reconstructed from 1,417 user instructions** across 13 chat exports (Apr 10 – May 31, 2026)
> **Project:** Athena → renamed to "The Agency"
> **Repository:** `C:\Users\alphi\theagency\`

---

## 1. Core Identity & Vision

### 1.1 What is The Agency?

**The Agency** is a **Qubes OS-inspired decentralized multi-agent architecture** where:

- **Athena Core** is a single, minimal agent capable of handling **any and all user requests** without addons
- **Domain Agents** (Personal, Work, Finance, Business, Learning, Technology, Social, Research, Security) handle broad life areas
- **Sub-Specialists** are spawned per-domain (e.g., Finance → InvestmentAgent, PersonalBanker, CryptoAgent, StocksExpert)
- **Addons/Nodes** act as "superpowers" — removable modules that multiply agent capability
- **External Coordinator** bridges Claude Code, Codex, Goose, OpenClaw, Hermes, AgentZero, DeerFlow, Pi agent, OpenCode

### 1.2 Design Principles

| Principle | Meaning |
|-----------|---------|
| **Modular & Reusable** | Build once, reuse everywhere (Unix philosophy) |
| **Graceful Degradation** | Removing any addon leaves a working system |
| **Core Independence** | Athena minimal install = full agent (no addons required) |
| **Concurrent Multi-Agent** | All addons support parallel execution |
| **Local-First** | Markdown files as primary memory; Noesis/SMS as upgrade |
| **Human Holds Absolute Truth** | Athena second; agents vote with evidence; human approves |
| **Plug & Play** | Drop a skill from OpenClaw/Claude Code → Athena rewrites it natively |
| **Small Model Target** | 1B parameter model achieves 70% of frontier models with efficient workflows |
| **Task-Agnostic Factories** | Dark/Ghost factories handle any language, any project type |

### 1.3 Minimal Installation

When installed minimally (just core + LLM API key), Athena:
- Performs any task OpenClaw, Hermes, DeerFlow, or AgentZero can
- Has markdown-based memory (like OpenClaw)
- Can emulate simulation, research, etc. through code execution
- Learns from itself (self-improvement loop)
- Spawns subagents for sensitive/parallel work
- **Pre-loaded:** Only Personal domain agent ships by default
- **Other domains added on-demand** via consensus (Butler + Personal Agent + User approval)

---

## 2. System Architecture

### 2.1 Qubes OS-Inspired Design

```
┌─────────────────────────────────────────────────────────────────────┐
│                         ATHENA CORE (dom0)                          │
│  Single minimal agent | Shared LLM key (env var) | Markdown memory  │
│  Butler (Gateway) routes to Buddy + Domain Agents                   │
└───────────┬──────────────┬──────────────┬──────────────┬────────────┘
            │              │              │              │
    ┌───────▼──────┐ ┌────▼─────┐ ┌─────▼──────┐ ┌────▼─────┐
    │   Personal   │ │   Work   │ │  Finance   │ │ Business │
    │   Agent      │ │  Agent   │ │  Agent     │ │  Agent   │
    │  ├─Health    │ │ ├─Backend│ │ ├─Invest   │ │ ├─Sales  │
    │  ├─Relation  │ │ ├─Front  │ │ ├─Banker   │ │ ├─Strat  │
    │  ├─Companion │ │ ├─Mobile │ │ ├─Crypto   │ │ ├─Ops    │
    │  ├─Research  │ │ ├─Cloud  │ │ ├─Stocks   │ │ ├─Legal  │
    │  └─Secrets   │ │ └─Net    │ │ └─Risk     │ │ └─CEO    │
    └──────────────┘ └──────────┘ └────────────┘ └────────────┘
            │              │              │              │
    ┌───────▼──────┐ ┌────▼─────┐ ┌─────▼──────┐ ┌────▼─────┐
    │   Learning   │ │  Techno  │ │   Social   │ │ Research │
    │   Agent      │ │  Agent   │ │  Agent     │ │  Agent   │
    │  ├─Courses   │ │ ├─Infra  │ │ ├─Network  │ │ ├─Papers │
    │  ├─Skills    │ │ ├─DevOps │ │ ├─Community│ │ ├─Exper  │
    │  └─Growth    │ │ └─Tools  │ │ └─Events   │ │ └─Know   │
    └──────────────┘ └──────────┘ └────────────┘ └────────────┘
                                │
                    ┌───────────▼───────────┐
                    │   SECURITY AGENT (9th) │
                    │  Cross-cutting layer   │
                    │  Kali/Parrot OS tools  │
                    └───────────────────────┘

    ════════════════════════════════════════════════
                     ADDONS / NODES
    ════════════════════════════════════════════════
    ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
    │  NOESIS  │ │   SMS    │ │   DREAM  │ │   SANDBOX│
    │ (2nd     │ │ (Sov.    │ │ (Memory  │ │ (Athena  │
    │  Brain)  │ │  Mind)   │ │  Consol) │ │  Mirror) │
    └──────────┘ └──────────┘ └──────────┘ └──────────┘
    ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
    │   SIM    │ │   AUTO   │ │   DARK   │ │  GHOST   │
    │ (MiroFish│ │ RESEARCH │ │ FACTORY  │ │ FACTORY  │
    │  Social) │ │ (Karpathy│ │ (Python) │ │ (Multi-  │
    │          │ │  Loop)   │ │          │ │  Lang)   │
    └──────────┘ └──────────┘ └──────────┘ └──────────┘
    ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
    │  SELF    │ │ ADVERSAR │ │INFERENCE │ │  EXTERNAL│
    │ RECTIFY  │ │  IAL     │ │ (Ollama) │ │COORDINATOR│
    │(Critique)│ │(Critic)  │ │(Temp VMs)│ │(Bridges) │
    └──────────┘ └──────────┘ └──────────┘ └──────────┘
    ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
    │  AETHER  │ │ FREE LLN │ │POST-COMMIT│ │ DISPOSABL│
    │(Durable  │ │(Smart    │ │(Work     │ │(Ephmeral │
    │  Exec)   │ │  Router) │ │  Track)  │ │  Agents) │
    └──────────┘ └──────────┘ └──────────┘ └──────────┘

    ════════════════════════════════════════════════
                  BRIDGES (External AI)
    ════════════════════════════════════════════════
    ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
    │Claude│ │Codex │ │Goose │ │Hermes│ │Open- │
    │ Code │ │      │ │      │ │      │ │Claw  │
    └──────┘ └──────┘ └──────┘ └──────┘ └──────┘
    ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
    │Agent │ │Deer- │ │  Pi  │ │Open- │ │Gemini│
    │Zero  │ │Flow  │ │Agent │ │Code  │ │ CLI  │
    └──────┘ └──────┘ └──────┘ └──────┘ └──────┘
```

### 2.2 Infrastructure (Physical/Virtual)

| Machine | Role | Location | IP/Details |
|---------|------|----------|------------|
| **Windows 11 Host** | Development, daily driver | Local | WSL Kali |
| **WSL (Kali Linux)** | Agent testing, OSINT, Umbral agent | Local | |
| **VPS Production** | OpenClaw production (Kael) | Remote | 172.238.240.113 |
| **Defense VM** | Parrot OS + defensive tools | Remote | 172.16.122.186 |
| **Offense VM** | Kali OS + offensive tools | Remote | |
| **McKenna** | Hermes agent instance | Remote | |
| **3CX Phone System** | Voice communication | Remote | Payphone access |
| **SearXNG** | Self-hosted metasearch | Local | Docker |

---

## 3. Domain Agents (Native to The Agency)

### 3.1 Personal Agent
- **Health** — fitness, diet, medical tracking
- **Relationships & Companionship** — social connections, "Buddy" agent acts as best friend
- **Personal Research & Secrets** — runs local model for privacy
- **Home** — household management
- **Leisure** — entertainment, hobbies
- **Nutritionist** — meal planning, dietary advice
- **Companion** — "My buddy" agent, acts as best friend

### 3.2 Work Agent (Multica Fork)
- **Backend Dev** — APIs, databases, services
- **Frontend Dev** — React, UI/UX, dashboards
- **Mobile Dev** — iOS, Android, React Native
- **Cloud/DevOps** — AWS, GCP, Azure, K8s, Terraform
- **Network Dev** — infrastructure, security, protocols
- **Production Dev** — CI/CD, deployment, monitoring
- **QA** — testing, security audits
- **Pentester** — runs policy tests (Offensive VM)

### 3.3 Finance Agent
- **Investment Agent** — stocks, bonds, portfolio management
- **Personal Banker** — budgeting, savings, accounts
- **Cryptocurrency Agent** — DeFi, trading, analysis
- **Stocks Expert** — market analysis, news, signals
- **Risk Assessor** — financial risk evaluation
- **Wall Street Expert** — institutional-grade analysis

### 3.4 Business Agent (Paperclip Fork)
- **Sales & Marketing** — projections, campaigns, CRM
- **Strategy** — business planning, competitive analysis
- **Operations** — workflows, processes, automation
- **Legal** — contracts, compliance, IP
- **CEO** — executive oversight, decision support
- **Role:** Acts as CEO of the holding company

### 3.5 Learning & Growth Agent
- **Courses** — curriculum management, progress tracking
- **Skills** — capability development, practice plans
- **Growth** — personal development, goals

### 3.6 Technology Agent
- **Infrastructure** — servers, networking, cloud
- **DevOps** — automation, pipelines, tooling
- **Tools** — software evaluation, installation

### 3.7 Social Agent
- **Network** — professional connections, outreach
- **Community** — engagement, events, groups
- **Events** — scheduling, coordination

### 3.8 Research & Legacy Agent
- **Papers** — academic research, summarization
- **Experiments** — hypothesis testing, data collection
- **Knowledge Gaps** — identify and fill information voids

### 3.9 Security Agent (9th Node — Cross-Cutting)
- **Offensive** — Kali Linux, pentesting, red teaming
- **Defensive** — Parrot OS, monitoring, hardening
- **OSINT/SIGINT** — intelligence gathering, 3D timeline mapping
- **Adversarial** — risk analysis, threat modeling
- **Reconnaissance** — surveillance, geology understanding, 3D reconstruction
- **Tools:** Shannon, CAI, Strix, Decepticon, Talon, SearXNG

### 3.10 Domain Agent Consensus Protocol
When a new subagent is requested:
1. Request sent to Butler + Personal Agent + User
2. All three must agree (vote based on evidence + agent track record)
3. If denied, agent can appeal to Lattice
4. **Autonomous fallback:** If user doesn't reply in X time, other linked agents vote
5. Human can always override
6. Final score is executed

### 3.11 Domain Agent Rules
- All agents can work even if all addons/nodes unavailable
- Each domain agent spawns its own specialized subagents
- Domain agents are templates AND managers
- User only gets Personal agent by default; others added via consensus
- System identifies expertise gap and creates perfect subagent to fill it
- No predefined subagents — created through set channels as needed

---

## 4. Memory Architecture

### 4.1 Three-Tier Memory System

```
┌─────────────────────────────────────────────────────────────────┐
│                    NOESIS (External Second Brain)                │
│  Independent project | MCP server | Ingests ALL digital life    │
│  Bank statements | GitHub | Google Workspace | Email | SMS      │
│  Tweets | Social media | Chat logs | Local markdown files       │
│  Builds relationship graph across ALL data sources              │
│  Name: Neuro Operational Epistemic Sovereign Intelligent System │
│  Location: Separate project folder, MCP access                  │
└───────────────────────────────┬─────────────────────────────────┘
                                │ MCP Protocol
┌───────────────────────────────▼─────────────────────────────────┐
│                    SMS (Sovereign Mind System)                   │
│  Athena's LOCAL memory core | Built-in, always available        │
│  5-Tier Lifecycle: Hot → Warm → Normal → Cool → Cold          │
│  Automatic aging, compression, and archival                     │
│  Status: Phase 3 COMPLETE (commit 4c77d4d)                     │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                    MARKDOWN FILES (Base Layer)                   │
│  Primary memory even when SMS/Noesis offline                    │
│  Per-agent wings: ~/.theagency/wings/{agent_name}/             │
│  Structure: wing → room → drawer → item                        │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 SMS Node Structure (Sovereign Mind System)

| Node | Purpose | Description |
|------|---------|-------------|
| **Secrets** | Credential storage | Encrypted key-value with ACL, per-agent isolation |
| **Retrieval** | Search & query | Multi-mode: semantic (Qdrant), graph (Neo4j), hybrid |
| **Spawn** | Subagent lifecycle | Secure process creation with sandboxing, resource limits |
| **Lattice** | Relationship graph | Neo4j graph DB for agent/task/deliverable relationships |
| **Librarian** | Data normalization | Deduplication, schema validation, import/export |
| **Dream** | Memory consolidation | Background merge, AutoDream cycles, insight extraction |
| **CPR** | Compression | Context window reduction, summary generation |
| **Gatekeeper** | Auth/Routing | Access control, routing decisions, anomaly detection |
| **Discoverer** | Pattern finding | Correlation detection, trend identification |
| **AnomalyMonitor** | Health/Security | Drift detection, performance monitoring |

### 4.3 Memory Independence Rule

> **"If Noesis goes offline, agents continue via SMS. If SMS goes offline, agents continue via markdown. Markdown ALWAYS works."**

- Every node has local storage that syncs with memory OS
- Overhead is acceptable for safety
- From day one, every node is fully independent
- If memory OS offline, can reconstruct from local storage

### 4.4 Context Management
- **Problem:** Context window overruns → crashes on 4GB machines
- **Solution:** Compression agents custom per agent (based on model + tasks)
- **Hybrid mode:** System automatically switches based on agent's work
- **Multi-channel:** Personal agent on Telegram, Business on Slack, Coding on Discord
- **SMS shares only needed context and memory — nothing more**

---

## 5. Addons / Nodes (Plug-and-Play Superpowers)

### 5.1 Noesis (Second Brain)
- **Status:** Independent project (separate from Athena)
- **Full Name:** Neuro Operational Epistemic Sovereign Intelligent System
- **Purpose:** Ingests owner's entire digital life
- **Data Sources:** Bank statements, GitHub, Google Workspace (email, docs), local markdown, chats, SMS, tweets, social media, photos, videos
- **Features:** Relationship graph, automatic categorization, cross-reference discovery, tagging, value/permission assignment
- **Location:** Separate project folder inside `/root/project-athena/`
- **Connection:** MCP server for all agents
- **Privacy:** Fully local, data sovereignty — raw data stays on owner's machine
- **Can work without Athena** — independent project

### 5.2 Sovereign Mind System (SMS)
- **Status:** Core Athena component, **COMPLETE** (Phase 3 committed)
- **Purpose:** Local memory for Athena agents
- **5-Tier Lifecycle:** Hot → Warm → Normal → Cool → Cold (auto-aging)
- **Components:** Secrets, Retrieval, Spawn, Lattice, Librarian, Dream, CPR, Gatekeeper
- **Storage:** SQLite + Neo4j + Qdrant (hybrid)
- **75+ files, ~20,000 LOC**
- **Tiered memory engine with lifecycle integration**

### 5.3 Dream Node
- **Status:** Research complete (inspired by Claude Code Auto-Dream + OpenClaw dreaming)
- **Purpose:** Background memory consolidation during idle/sleep cycles
- **Features:** Auto-merge related facts, extract insights, reduce Lattice bloat
- **Trigger:** Cron-based or idle detection

### 5.4 Simulation Node (MiroFish-Offline Inspired)
- **Status:** Scaffolded (23 modules, Apr 30 – May 5)
- **Purpose:** Multi-agent social, economic, and policy simulation
- **Engine:** OASIS framework (Open Agent Social Interaction Simulations)
- **Capacity:** 100–1000+ agents, parallel execution
- **Use cases:** Business agents run product scenarios, personal agents optimize daily schedules, work agent runs policy tests
- **Alternatives evaluated:** Fork OASIS → wrap as Athena addon (recommended), or use camel-oasis package directly
- **Social + Economic + Policy simulation**

### 5.5 Auto-Research Node (Karpathy Inspired)
- **Status:** Scaffolded (18 files, Apr 30 – May 5)
- **Purpose:** Iterative improvement loop for anything measurable
- **Targets:** Prompts, system prompts, system code, calculations, workflows, finance models
- **Method:** Modify → Execute → Evaluate → Log → Repeat
- **Concurrent:** Personal + work agents run simultaneously on different targets
- **Cross-pollination:** If auto-research improves one workflow, Dark Factory automatically improves others using same formula

### 5.6 Dark Factory (Python-Focused)
- **Status:** Designed, partially scaffolded
- **Purpose:** Continuous Python-focused software generation for Athena agents
- **Modes:**
  1. Generate tools from natural language
  2. Build full CLIs from schema
  3. Create reusable modules
  4. Iteratively improve existing code
  5. Self-rectify (discover bugs, fix, validate)
  6. **Absorption:** Clone any GitHub repo → rewrite to Athena-native
- **Input:** Natural language OR structured schema (agent-provided)
- **Output:** Defined by requester (full project, CLI tool, module)
- **Validation:** Self-validates + agent validates second time
- **Task-Agnostic:** Can build anything — Windows exe, COBOL program, custom OS
- **Full SDLC:** Requirements → Design → Build → Test → Deploy → Maintain
- **Multi-Agent:** Multiple agents can call Dark Factory simultaneously without bleeding into each other
- **Long-term maintenance:** Linked to GitHub, ships features, fixes bugs continuously
- **Failure recovery:** Built-in patterns to prevent infinite loops

### 5.7 Ghost Factory (Multi-Language)
- **Status:** Scaffolded (6-mode universal software construction, commit a54866a)
- **Purpose:** Multi-language build system (not just Python)
- **6 Modes:**
  1. **New** — Build new programs from scratch
  2. **Reverse** — Reverse-engineer without source code
  3. **Fork** — Fork GitHub repos and add features
  4. **Contribute** — Contribute to public repos as independent builds
  5. **Rework** — Rework code to Athena-compatible format
  6. **Study** — Learn new languages/frameworks from online sources on-demand
- **Languages:** Rust, JavaScript, Go, C, C++, F#, Malbolge, custom languages
- **Collaboration:** Translates non-Python → Python for Dark Factory consumption
- **Learning:** Studies new languages from documentation when needed
- **Example:** Build a cryptographic app using Malbolge (no training data) → Android app

### 5.8 Self-Rectification Node
- **Status:** Designed
- **Purpose:** Self-critique and correction system
- **Method:** Detect own errors → Propose fixes → Validate → Apply
- **Scope:** Code, workflows, prompts, system configuration
- **Modes:**
  1. Test changes in latest Athena environment before making mainstream
  2. Like a different tree — test → see how it is → keep main safe → role out approved changes in stages

### 5.9 Adversarial Node (Critic)
- **Status:** Designed (research from Sparfuchs-QA, Cisco Skill Scanner, Giskard)
- **Purpose:** Critic/QA system (NOT red teaming — that's Security Agent's job)
- **Method:** Simultaneously checks every agent's work and finds loopholes
- **Responsibility:** Enforce quality with strict QA rules
- **Research Sources:**
  - Sparfuchs-QA (sparfuchs-qa.git)
  - Cisco AI Defense (skill-scanner.git)
  - Giskard (giskard-oss.git)

### 5.10 Sandbox Node (Athena Mirror)
- **Status:** Designed (Sandbox addon complete, node wrapper planned)
- **Purpose:** Isolated code execution with mini-Athena environment
- **Modes:**
  1. **Clean Room** — empty Python environment
  2. **Agency Mirror** — full codebase clone for testing
  3. **VM-based** — Firecracker microVMs per subagent
- **Use cases:** Test code before deploying, run sensitive operations, experiment safely
- **Virtual environment:** Mimics Athena as it is
- **Can run independently** — used by other agents outside Athena environment

### 5.11 Inference Node (Ollama-Packaged)
- **Status:** Designed
- **Purpose:** Local model execution for zero-cost subagent spawning
- **Packaging:** Bundled Ollama + model selection
- **Advertisement:** "Temporary subagents available via Inference Node"
- **Cost:** Zero (runs on local hardware)
- **Qubes philosophy:** Like qubes VMs — spin temporary subagents running on local models

### 5.12 External Coordinator Node
- **Status:** Bridge infrastructure built (Apr 30 – May 5)
- **Purpose:** Translation layer between Athena and external AI harnesses
- **Bridges:**
  - Claude Code (subprocess)
  - Codex (OpenAI API)
  - Goose (ACP)
  - Hermes (ACP, subprocess)
  - OpenClaw (HTTP API)
  - AgentZero (Docker container)
  - DeerFlow (HTTP API + SSE)
  - Pi Agent (CLI/API)
  - OpenCode (CLI)
  - Gemini CLI (subprocess/API)
- **Interface:** Standardized `execute(task, context, timeout, budget)` → `BridgeResult`
- **Key insight:** Same Codex you run runs natively with Athena — system understands it natively

### 5.13 Aether (Durable Execution)
- **Status:** Designed (wrapper exists, full system pending)
- **Purpose:** Build AI agents and harnesses with checkpoint recovery
- **Checkpoint:** After every LLM call, tool use, decision
- **Enables:** Crash recovery, long-running workflows
- **Sprint:** Planned for Sprint 3+

### 5.14 FreeLLM Addon
- **Status:** Built (based on freellmapi.git, 17 tests passing)
- **Purpose:** Smart LLM routing across free/local/paid providers
- **4 Options:**
  1. **Free** — User-provided API keys, near-free inference
  2. **User API Keys** — Per-provider keys, round-robin or priority routing
  3. **Subscription** — Paid tiers with coding plans, subscription-based providers
  4. **Local Inference** — Ollama with user-defined models
- **Hybrid:** Smart routing through rolling window or fixed RPM, user-defined rules

### 5.15 Post-Commit Work Tracking
- **Status:** Built (Jack's Golden Rule)
- **Purpose:** Update work tracking on every commit
- **Rename:** "Athena system services"
- **Files:** `~/.theagency/wings/{agent}/tasks/` and `completed/`
- **Format:** JSON task files with status, commit hash, timestamps
- **Golden Rule:** "If it's not in the wing, it doesn't exist"

### 5.16 Disposable Agent Runner
- **Status:** Designed
- **Purpose:** Ephemeral agents for sensitive or zero-cost tasks
- **Pairing:** Inference Node (Ollama) for local, free execution
- **Features:** Runs sensitive operations without persistent state

---

## 6. Bridge HTTP API Specification

### 6.1 Core Endpoints

```http
POST /v1/bridges/{bridge_type}/execute
Content-Type: application/json

{
  "task_id": "task_abc123",
  "content": "Implement OAuth login flow",
  "metadata": {
    "source": "athena",
    "agent": "work_agent.backend_dev",
    "priority": "high",
    "budget_usd": 0.50
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

### 6.2 Other Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | /v1/bridges | List available bridges + capabilities |
| POST | /v1/bridges/{type}/start | Start bridge daemon |
| POST | /v1/bridges/{type}/stop | Stop bridge daemon |
| GET | /v1/agents/domain | List domain agents + sub-specialists |
| GET | /v1/health | System health check |
| GET | /v1/capabilities | Aggregate capability advertisement |

### 6.3 Auth

- API key: `Authorization: Bearer <token>`
- Per-agent key isolation (domain agents can have own LLM providers)
- Session tracking (last 10 queries per user)

---

## 7. Data Models

### 7.1 TaskMessage

```python
@dataclass
class TaskMessage:
    task_id: str
    type: str                    # "code_generation", "simulation", "research", etc.
    content: str                 # Task description
    metadata: dict = field(default_factory=dict)
    # metadata: source, agent, priority, budget, parent_task_id
    created_by: str = "system"
    created_at: datetime = field(default_factory=datetime.utcnow)
```

### 7.2 ResultMessage

```python
@dataclass
class ResultMessage:
    task_id: str
    bridge_type: str             # "native", "claude_code", "codex", etc.
    status: str                  # "completed", "failed", "timeout"
    output: str
    artifacts: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    duration_seconds: float = 0.0
    error: Optional[str] = None
```

### 7.3 CapabilityAdvertisement

```python
@dataclass
class CapabilityAdvertisement:
    agent_id: str
    domain: str                  # "personal", "work", "finance", etc.
    capabilities: list[str]      # ["code_generation", "simulation", ...]
    supports_streaming: bool
    supports_tools: bool
    addons: list[str]            # ["simulation", "auto_research", ...]
    llm: str                     # Current model
    status: str                  # "active", "idle", "error"
```

---

## 8. Technology Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| **Language (agents)** | Python 3.11+ | ML/data science integration, async support |
| **Language (tooling)** | TypeScript | Bridge adapters, GUI, web frontend |
| **Lattice DB** | Neo4j | Graph relationships between agents/tasks/deliverables |
| **Vector DB** | Qdrant | Semantic search, similarity matching |
| **Local Memory** | Markdown files | Always works, human-readable, git-friendly |
| **External Memory** | Noesis (MCP) | Rich second brain, connects all data sources |
| **LLM Router** | Pluggable | Ollama local, OpenAI/Anthropic cloud, Nous Portal, FreeLLM |
| **Message Bus** | Redis Streams (optional) | Real-time agent coordination |
| **Policy Engine** | Cedar (ACL) | Fine-grained access control |
| **Observability** | OpenTelemetry | Tracing, metrics, audit logs |
| **Deployment** | Docker → K8s | Scalable, reproducible, isolated |
| **GUI (Business)** | Paperclip fork (React) | Companies, org chart, tasks |
| **GUI (Mission Control)** | Python Textual TUI | Unified agent dashboard |
| **GUI (Coding)** | Superset fork | Multi-agent IDE |
| **GUI (Work)** | Multica fork | Project management |
| **Sandbox** | Firecracker microVMs | Lightweight isolation per subagent |
| **State Management** | Zustand (React) | Lightweight, fast |
| **Data Fetching** | React Query | Server state management |

---

## 9. FreeLLM / LLM Routing

### 9.1 Concept

A model routing gateway (inspired by [freellmapi](https://github.com/tashfeenahmed/freellmapi.git)) with 5 options:

| Option | Description |
|--------|-------------|
| **Free** | User-provided API keys, near-free inference for normal usage |
| **User API Keys** | Per-provider keys, round-robin or priority routing |
| **Subscription** | Paid tiers with coding plans, subscription-based providers |
| **Local Inference** | Ollama with user-defined models and rules |
| **Hybrid** | Smart routing through rolling window or fixed RPM, user-defined rules |

### 9.2 Athena Core Key Sharing
- Athena gets key from env var, shared by other agents
- If user has multiple providers, each agent gets its own LLM:
  - Research Agent: Kimi K2.6
  - Athena: Claude 4.7 Opus
  - Personal Agent: GPT 5.5
  - News Agent: Grok 4.3

---

## 10. Quality Assurance

### 10.1 QA Critic (Jack's System)

Built-in quality enforcement with 10 dimensions:

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Correctness | CRITICAL | Output matches requirements |
| Security | CRITICAL | No vulnerabilities, no secret leaks |
| Completeness | HIGH | All parts addressed |
| Efficiency | MEDIUM | Resource usage within limits |
| Modularity | HIGH | Reusable, single-responsibility |
| Documentation | MEDIUM | Clear comments and README |
| Test Coverage | MEDIUM | Adequate test coverage |
| Style | LOW | Consistent formatting |
| Innovation | LOW | Novel solutions preferred |
| Graceful Degradation | HIGH | Fails safely |

### 10.2 QA Scanners

```
athena/skills/quality_assurance/
├── scanners/
│   ├── code_style_scanner.py    # PEP8, formatting, complexity
│   ├── import_scanner.py        # Import validity & circular deps
│   ├── test_coverage_scanner.py # Coverage analysis
│   ├── architecture_scanner.py  # Layer violations, dependency checks
│   ├── security_scanner.py      # Security audit, secrets detection
│   └── doc_scanner.py           # Documentation quality
├── reporters/
│   └── html_reporter.py         # HTML report generator
└── rules/
    └── rule_registry.py         # Configurable rule engine
```

### 10.3 Golden Rules (Jack's Rules)

1. **Plan first** — dry run before editing files
2. **Modular & reusable** — build once, reuse forever
3. **Work tracking** — if it's not in the wing, it doesn't exist
4. **Tag your work** — every commit tagged with agent name
5. **Document chain of thought** — why you changed what you changed
6. **Log mistakes** — record errors and fixes permanently
7. **Double-check** — always reason and plan before editing
8. **No circular imports** — fix immediately

### 10.4 First QA Scan Results
- Quality Score: 0/100 (baseline)
- Issues: 719 total
  - Code Style: 403
  - Documentation: 312
  - Security: 3 (exec/eval, SQL injection)
  - Test Coverage: 1
  - Architecture: 4 high-complexity functions

---

## 11. Communication & Coordination

### 11.1 Coordination Protocol (MemPalace Message Bus)

```
wing: coordination/
├── inbox/               # Incoming tasks per agent
├── outbox/              # Completed results
├── protocol/            # PROTOCOL_SPEC (v1.0)
├── capabilities/        # Per-agent capability registry
└── tasks/               # Shared task board
```

Message format:
```json
{
  "sender": "kael",
  "recipient": "zoey",
  "type": "task|result|heartbeat|error",
  "payload": { "command": "get_weather", "location": "Nairobi" },
  "trace_id": "test-003",
  "timestamp": "2026-04-14T10:30:00Z"
}
```

### 11.2 Real-Time Coordination

| Method | Use Case |
|--------|----------|
| **MemPalace MCP** | Persistent task inbox, capability sharing |
| **A2A (Agent-to-Agent)** | Google A2A protocol if all agents support it |
| **Telegram** | Primary messaging channel (cron jobs, reports) |
| **Discord** | Alternative for coding domain |
| **Slack** | Business agent |
| **3CX Phone** | Voice calls, payphone access from anywhere |

### 11.3 Cron Jobs & Heartbeats

- **Daily briefings** — 7:30 AM EAT (news from worldmonitor.app, HN, daily.dev)
- **Nightly memory sync** — sweep MemPalace, update from other agents, write report
- **Auto-commit** — Forge daemon every 15 minutes
- **QA monitoring** — continuous git commit scanning, auto-issue creation
- **Self-diagnosis** — agents check own health, report issues
- **Forge heartbeat** — 10-minute build cycles
- **Obsidian backups** — Midday + end of day
- **Delta reports** — Every 30 minutes from Forge

### 11.4 Task Routing
> **"Work should always be broken down and each piece routed to the BEST agent to complete it. They all work in parallel and produce richer results."**

- Personal agent needs finance review → routes to Finance agent
- Business agent has financial insight → included in reports
- Cross-domain insights shared via Lattice

---

## 12. External Integrations

### 12.1 MemPalace
- **Role:** Shared knowledge graph across ALL agents (not just Athena)
- **Location:** `/root/.local/share/pipx/venvs/mempalace/`
- **Features:** Spatial memory (wing→room→drawer), knowledge graph, MCP server
- **Agents connected:** Kael (OpenClaw), Zoey (Hermes), Aiden (AgentZero), Ally (DeerFlow)

### 12.2 SearXNG
- **Role:** Self-hosted metasearch engine for agent web searches
- **Use:** Primary web search for agents (privacy-respecting)

### 12.3 Postiz
- **Role:** Self-hosted social media management
- **URL:** `http://localhost:4007`
- **Integrations:** X, LinkedIn, Facebook, Reddit, TikTok
- **Stack:** Docker compose (PostgreSQL, Redis, Temporal, Elasticsearch)

### 12.4 3CX Phone System
- **Role:** Self-hosted PBX for voice communication
- **Use:** Call agents from anywhere, even payphones

### 12.5 Noesis
- **Role:** Independent second brain project
- **Ingests:** Bank statements, GitHub, Google Workspace, email, SMS, social media, local files
- **Protocol:** MCP server for agent access

### 12.6 Paperclip (Business UI Fork)
- **Role:** Business Agent UI
- **Source:** github.com/paperclipai/paperclip
- **Features:** Companies, org chart, tasks, budgets, headcount, MRR
- **Integration:** Built-in "Athena" adapter maps roles → domain agents

### 12.7 Multica (Work UI Fork)
- **Role:** Work Agent UI
- **Source:** github.com/multica-ai/multica
- **Features:** Projects, sprints, deliverables, team management
- **Integration:** Work agent controls Multica, assigns projects to subagents

### 12.8 Superset (Coding UI Fork)
- **Role:** Coding Agent IDE
- **Source:** github.com/superset-sh/superset
- **Features:** Multi-agent IDE accommodating Claude, Codex, Gemini, OpenCode
- **Integration:** Fork + rewrite Pulse to Athena-compatible for built-in agent

### 12.9 Space-Agent (Buddy UI Fork)
- **Role:** Mission Control UI / Buddy agent
- **Source:** github.com/agent0ai/space-agent
- **Features:** Two-page Mission Control, component rendering
- **Development:** Can build more features on top, continue if original stops
- **Default:** Mission Control has two pages (Mission Control + Buddy page)
- **Switching:** User selects Buddy floating icon → switches to Buddy page

---

## 13. Deployment & Installation

### 13.1 Minimal Install (Core Only)

```bash
pip install theagency-core
export LLM_API_KEY="sk-..."
python -m theagency.core.agent  # Starts Athena minimal
```

**What you get:**
- Athena Core (single agent)
- Butler (gateway)
- Personal domain agent (pre-loaded)
- Markdown memory
- Bridge system infrastructure

### 13.2 Full Install (With Addons)

```bash
pip install theagency[all]
theagency install --profile full
# Installs: Noesis, SMS, Dream, Simulation, Auto-Research,
#           Dark Factory, Ghost Factory, Sandbox, Adversarial,
#           External Coordinator, all bridges
```

### 13.3 Selective Install

```bash
theagency install --addons simulation,auto_research,dark_factory
theagency install --domains finance,business,security
theagency install --bridges claude_code,codex,openclaw
```

### 13.4 VM Per Agent (Qubes-Style)

```bash
theagency vm create --agent personal --os ubuntu --resources "2cpu,4gb"
theagency vm create --agent security --os kali --resources "2cpu,4gb"
theagency vm create --agent work --os ubuntu --resources "4cpu,8gb"
```

### 13.5 GUI Options
- **Just Butler + Normal UI** (no Buddy)
- **Butler + Buddy addon** (recommended)
- **Butler and Buddy as one** (not recommended)

---

## 14. Version Roadmap

| Version | Target | Features |
|---------|--------|----------|
| **v0.0.1** | Apr 2026 | Core scaffold, SMS nodes, basic agent |
| **v0.1.0** | May 2026 | Domain agents, External coordinator, 3 bridges |
| **v0.2.0** | Jun 2026 | Simulation addon, Auto-research addon |
| **v0.3.0** | Jul 2026 | FinanceAgent + 4 sub-specialists |
| **v0.4.0** | Aug 2026 | BusinessAgent + ResearchAgent |
| **v0.5.0** | Sep 2026 | **GitHub push** — working harness rivaling existing ones |
| **v0.6.0** | Oct 2026 | CodingAgent + PersonalAgent |
| **v0.7.0** | Nov 2026 | Dark Factory + Ghost Factory full integration |
| **v0.8.0** | Dec 2026 | Sandbox (Firecracker VMs), Adversarial node |
| **v0.9.0** | Jan 2027 | All addons shipped, full integration |
| **v1.0.0** | Feb 2027 | Production-ready, bug-free, plug-and-play |

### 14.1 Internal Milestones
- **v0.3.0 internal** — fully functional (all domain agents working)
- **v0.5.0 public** — ready for testing
- **v0.9.0** — full product with 99% bugs fixed, only edge cases remaining
- **v1.0.0** — all edge cases resolved

---

## 15. Agent Roster (Current)

| Agent | Platform | Role | Created | Status |
|-------|----------|------|---------|--------|
| **Kael ⚡️** | OpenClaw | Main daily agent, coordination lead | Apr 11 | Architect AI agent |
| **Zoey 😜** | Hermes Agent | Research, installation, multi-agent | Apr 11 | Active |
| **Aiden** | AgentZero | AgentZero instance (Docker) | Apr 11 | Recovered |
| **Ally** | DeerFlow | DeerFlow agent | Apr 12 | Troubleshooting |
| **McKenna** | Hermes Agent | Secondary Hermes instance | Apr 12 | Active |
| **Nexus** | Hermes Agent | System monitoring, cleanup | Apr 10 | Active |
| **Jack** | Hermes Agent | QA/Critic, competitive analysis | Apr 29 | Active |
| **Clara** | Hermes Agent | Buddy UI / Space-Agent fork | Apr 29 | Active |
| **Teresa** | Hermes Agent | Build agent, addon scaffolding | Apr 29 | Active |
| **Remex** | Hermes Agent | Memory systems, SMS/Noesis | May 1 | Active |
| **Qore** | Hermes Agent | QA officer, git monitoring | May 1 | Active |
| **Umbral** | Hermes Agent | OSINT/SIGINT, Kali Linux | May 21 | Active |
| **charlie** | Unknown | Task agent | May 30 | Active |

### 15.1 Forge Department
- **7 agents** auto-building Athena with 15-minute heartbeat
- **Zoom** — build subagent with 10-minute heartbeat
- Subagents: Lead Software Architect, LLM Harness Engineer, Frontend/UI Engineer, QA subagent
- Can hire new subagents as needed
- All work in parallel with 24/7 uptime
- Auto-restart on failure

---

## 16. Job Search Automation

- **Target:** Remote jobs, $30-100/day
- **Skills:** Databases, AI full-stack (Python, Rust, JS), Junior-Mid level
- **Strategy:** Apply to many, one will hit
- **Scope:** All countries, all continents
- **Integration:** Auto-apply via agent delegation

---

## 17. Glossary

| Term | Definition |
|------|------------|
| **The Agency** | Decentralized multi-agent AI system (formerly Project Athena) |
| **Athena Core** | The single minimal agent at the heart of the system |
| **Lattice** | Neo4j graph database storing agent/task/deliverable relationships |
| **SMS** | Sovereign Mind System — local memory core (5-tier: Hot→Cold) |
| **Noesis** | External second brain (independent project, MCP access) |
| **Domain Agent** | Native agent handling a broad life area (Personal, Work, etc.) |
| **Sub-Specialist** | Fine-tuned agent spawned by a domain agent |
| **Addon/Node** | Pluggable capability module |
| **Bridge** | External AI framework wrapper |
| **Dark Factory** | Python-focused continuous code generation + Absorption mode |
| **Ghost Factory** | Multi-language 6-mode build system |
| **Self-Rectification** | Self-critique and correction system |
| **Adversarial** | Critic/QA system enforcing quality (not red teaming) |
| **Sandbox** | Isolated execution environment (Clean Room / Athena Mirror) |
| **Inference Node** | Local Ollama-based execution for zero-cost subagents |
| **External Coordinator** | Translation layer for external AI harnesses |
| **Aether** | Durable execution framework with checkpoint recovery |
| **FreeLLM** | Smart LLM routing across free/local/paid providers |
| **Dream Node** | Background memory consolidation |
| **MemPalace** | Shared knowledge graph MCP server (all agents) |
| **Wing** | Per-agent isolated storage |
| **Room** | Memory container within a wing |
| **Drawer** | Individual memory item |
| **Qubes Philosophy** | Security by isolation; temporary VMs; dom0 = Athena Core |
| **Golden Rule (Jack)** | Plan first, modular code, work tracking, tag commits |
| **Forge** | Department of 7+ agents auto-building Athena |
| **Zoom** | Build subagent with 10-minute heartbeat |
| **Buddy** | Space-Agent fork for UI rendering in Mission Control |
| **Absorption** | Clone any GitHub repo → rewrite to Athena-native |
| **Consensus** | Agent voting for new subagents (Butler + Personal + User) |

---

*Last updated: 2026-09-17 by Hermes Agent*
*Status: Complete specification from 1,417 user instructions. Ready for build.*
