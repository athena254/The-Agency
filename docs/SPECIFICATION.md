# The Agency — Full System Specification

> **Reconstructed from 1,417 user instructions** across 13 chat exports (Apr 10 – May 31, 2026)
> **Project:** Athena (autonomous multi-agent framework, renamed to "The Agency")
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
| **Graceful Degradation** | Removing any addon leaves a working system; each is independent |
| **Core Independence** | Athena minimal install = full agent (no addons required) |
| **Concurrent Multi-Agent** | All addons support parallel execution by personal + work + domain agents |
| **Local-First** | Markdown files as primary memory; Noesis/SMS as upgrade, not dependency |
| **Human Holds Absolute Truth** | Athena second; agents vote with evidence; human approves |
| **Plug & Play** | Drop a skill from OpenClaw/Claude Code → Athena rewrites it natively |
| **Small Model Target** | 1B parameter model achieves 70% of Claude/GPT with efficient workflows |

### 1.3 Minimal Installation

When installed minimally (just core + LLM API key), Athena:
- Performs any task OpenClaw, Hermes, DeerFlow, or AgentZero can
- Has markdown-based memory (like OpenClaw)
- Can emulate simulation, research, etc. through code execution
- Learns from itself (self-improvement loop)
- Spawns subagents for sensitive/parallel work

---

## 2. System Architecture

### 2.1 Qubes OS-Inspired Design

```
┌─────────────────────────────────────────────────────────────────────┐
│                         ATHENA CORE (dom0)                          │
│  Single minimal agent | Shared LLM key (env var) | Markdown memory  │
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
    └──────────────┘ └──────────┘ └────────────┘ └──────────┘
            │              │              │              │
    ┌───────▼──────┐ ┌────▼─────┐ ┌─────▼──────┐ ┌────▼─────┐
    │   Learning   │ │  Techno  │ │   Social   │ │ Research │
    │   Agent      │ │  Agent   │ │  Agent     │ │  Agent   │
    │  ├─Courses   │ │ ├─Infra  │ │ ├─Network  │ │ ├─Papers │
    │  ├─Skills    │ │ ├─DevOps │ │ ├─Community│ │ ├─Exper  │
    │  └─Growth    │ │ └─Tools  │ │ └─Events   │ │ └─Know   │
    └──────────────┘ └──────────┘ └────────────┘ └──────────┘
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
    │(Critique)│ │(Red Team)│ │(Temp VMs)│ │(Bridges) │
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

| Machine | Role | Location |
|---------|------|----------|
| **Windows 11 Host** | Development, daily driver | Local |
| **WSL (Kali Linux)** | Agent testing, OSINT | Local |
| **VPS 172.238.240.113** | OpenClaw production (Kael) | Remote |
| **McKenna** | Hermes agent instance | Remote |
| **VPS 172.16.122.186** | Defense VM (Parrot OS) | Remote |
| **Defense VM** | Parrot OS + defensive tools | Remote |
| **Offense VM** | Kali OS + offensive tools | Remote |
| **3CX Phone System** | Voice communication, payphone access | Remote |

---

## 3. Domain Agents (Native to The Agency)

### 3.1 Personal Agent
- **Health** — fitness, diet, medical tracking
- **Relationships & Companionship** — social connections, "Buddy" agent acts as best friend
- **Personal Research & Secrets** — runs local model for privacy
- **Home** — household management
- **Leisure** — entertainment, hobbies

### 3.2 Work Agent
- **Backend Dev** — APIs, databases, services
- **Frontend Dev** — React, UI/UX, dashboards
- **Mobile Dev** — iOS, Android, React Native
- **Cloud/DevOps** — AWS, GCP, Azure, K8s, Terraform
- **Network Dev** — infrastructure, security, protocols
- **Production Dev** — CI/CD, deployment, monitoring

### 3.3 Finance Agent
- **Investment Agent** — stocks, bonds, portfolio management
- **Personal Banker** — budgeting, savings, accounts
- **Cryptocurrency Agent** — DeFi, trading, analysis
- **Stocks Expert** — market analysis, news, signals
- **Risk Assessor** — financial risk evaluation

### 3.4 Business Agent
- **Sales & Marketing** — projections, campaigns, CRM
- **Strategy** — business planning, competitive analysis
- **Operations** — workflows, processes, automation
- **Legal** — contracts, compliance, IP
- **CEO** — executive oversight, decision support

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

---

## 4. Memory Architecture

### 4.1 Tiered Memory System

```
┌─────────────────────────────────────────────────────────────────┐
│                    NOESIS (External Second Brain)                │
│  Independent project | MCP server | Ingests ALL digital life    │
│  Bank statements | GitHub | Google Workspace | Email | SMS      │
│  Tweets | Social media | Chat logs | Local markdown files       │
│  Builds relationship graph across ALL data sources              │
└───────────────────────────────┬─────────────────────────────────┘
                                │ MCP Protocol
┌───────────────────────────────▼─────────────────────────────────┐
│                    SMS (Sovereign Mind System)                   │
│  Athena's LOCAL memory core | Built-in, always available        │
│  Hot → Warm → Normal → Cool → Cold tiered lifecycle            │
│  Automatic aging, compression, and archival                     │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                    MARKDOWN FILES (Base Layer)                   │
│  Primary memory even when SMS/Noesis offline                    │
│  Per-agent wings: ~/.athena/wings/{agent_name}/                │
│  Structure: wing → room → drawer → item                        │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 SMS Node Structure

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

This is a design-from-day-one feature: every node is fully independent.

---

## 5. Addons / Nodes (Plug-and-Play Superpowers)

### 5.1 Noesis (Second Brain)
- **Status:** Independent project, connects via MCP
- **Purpose:** Ingests owner's entire digital life (bank statements, GitHub, email, SMS, social media, local files)
- **Features:** Relationship graph, automatic categorization, cross-reference discovery
- **Alternative name:** Neuro Operational Epistemic Sovereign Intelligent System

### 5.2 Sovereign Mind System (SMS)
- **Status:** Core Athena component (built-in)
- **Purpose:** Local memory for Athena agents
- **Lifecycle:** Hot → Warm → Normal → Cool → Cold (auto-aging)
- **Components:** Secrets, Retrieval, Spawn, Lattice, Librarian, Dream, CPR, Gatekeeper

### 5.3 Dream Node
- **Status:** Planned (research complete — inspired by Claude Code Auto-Dream + OpenClaw dreaming)
- **Purpose:** Background memory consolidation during idle/sleep cycles
- **Features:** Auto-merge related facts, extract insights, reduce Lattice bloat
- **Trigger:** Cron-based or idle detection

### 5.4 Simulation Node (MiroFish-Offline Inspired)
- **Status:** Scaffolded (23 modules, Apr 30 – May 5)
- **Purpose:** Multi-agent social, economic, and policy simulation
- **Engine:** OASIS framework (Open Agent Social Interaction Simulations)
- **Capacity:** 100–1000+ agents, parallel execution
- **Use cases:** Business agents run product scenarios, personal agents optimize daily schedules

### 5.5 Auto-Research Node (Karpathy Inspired)
- **Status:** Scaffolded (18 files, Apr 30 – May 5)
- **Purpose:** Iterative improvement loop for anything measurable
- **Targets:** Prompts, system code, calculations, workflows, finance models
- **Method:** Modify → Execute → Evaluate → Log → Repeat
- **Concurrent:** Personal + work agents run simultaneously on different targets

### 5.6 Dark Factory
- **Status:** Designed, partially scaffolded
- **Purpose:** Continuous Python-focused software generation for Athena agents
- **Modes:**
  1. Generate tools from natural language
  2. Build full CLIs from schema
  3. Create reusable modules
  4. Iteratively improve existing code
  5. Self-rectify (discover bugs, fix, validate)
  6. **Absorption:** Clone any GitHub repo → rewrite to Athena-native
- **Collaboration:** Works with Ghost Factory (multi-language input → Python)

### 5.7 Ghost Factory
- **Status:** Scaffolded (6-mode universal software construction)
- **Purpose:** Multi-language build system (not just Python)
- **Capabilities:**
  - Build new programs from scratch
  - Reverse-engineer without source code
  - Fork GitHub repos and add features
  - Contribute to public repos as independent builds
  - Rework code to Athena-compatible format
- **Collaboration:** Translates non-Python → Python for Dark Factory consumption

### 5.8 Self-Rectification Node
- **Status:** Designed
- **Purpose:** Self-critique and correction system
- **Method:** Detect own errors → Propose fixes → Validate → Apply
- **Scope:** Code, workflows, prompts, system configuration

### 5.9 Adversarial Node
- **Status:** Planned
- **Purpose:** Red teaming and risk analysis
- **Method:** Attack own systems → Report findings → Recommend hardening
- **Pairing:** Works with Security Agent for defense validation

### 5.10 Sandbox Node (Athena Mirror)
- **Status:** Designed (Sandbox addon complete, node wrapper planned)
- **Purpose:** Isolated code execution with mini-Athena environment
- **Modes:**
  1. **Clean Room** — empty Python environment
  2. **Agency Mirror** — full codebase clone for testing
  3. **VM-based** — Firecracker microVMs per subagent
- **Use cases:** Test code before deploying, run sensitive operations, experiment safely

### 5.11 Inference Node (Ollama)
- **Status:** Planned
- **Purpose:** Local model execution for zero-cost subagent spawning
- **Packaging:** Bundled Ollama + model selection
- **Advertisement:** "Temporary subagents available via Inference Node"
- **Cost:** Zero (runs on local hardware)

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

### 5.13 Post-Commit Hook (Work Tracking)
- **Status:** Built (Jack's Golden Rule)
- **Purpose:** Update work tracking on every commit
- **Files:** `~/.theagency/wings/{agent}/tasks/` and `completed/`
- **Format:** JSON task files with status, commit hash, timestamps

### 5.14 Disposable Agent Runner
- **Status:** Designed
- **Purpose:** Ephemeral agents for sensitive or zero-cost tasks
- **Pairing:** Inference Node (Ollama) for local, free execution

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
| **Sandbox** | Firecracker microVMs | Lightweight isolation per subagent |

---

## 9. FreeLLM / LLM Routing

### 10.1 Concept

A model routing gateway (inspired by [freellmapi](https://github.com/tashfeenahmed/freellmapi.git)) with 4 options:

| Option | Description |
|--------|-------------|
| **Free** | User-provided API keys, near-free inference for normal usage |
| **User API Keys** | Per-provider keys, round-robin or priority routing |
| **Subscription** | Paid tiers with coding plans, subscription-based providers |
| **Local Inference** | Ollama with user-defined models and rules |
| **Hybrid** | Smart routing through rolling window or fixed RPM, user-defined rules |

### 10.2 Routing Strategy

```python
class LLMRouter:
    """Smart routing across providers with cost optimization."""
    
    def route(self, task: TaskMessage) -> Provider:
        # 1. Check user-defined rules
        if rule := self.rules.match(task):
            return rule.provider
        
        # 2. Check free tier availability
        if free := self.free_tier.available(task):
            return free
        
        # 3. Check local inference capability
        if self.ollama.can_handle(task):
            return self.ollama
        
        # 4. Fall back to lowest-cost paid provider
        return self.paid_tier.cheapest(task)
```

---

## 11. Quality Assurance

### 11.1 QA Critic (Jack's System)

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

### 11.2 QA Scanners

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

### 11.3 Golden Rules (Jack's Rules)

1. **Plan first** — dry run before editing files
2. **Modular & reusable** — build once, reuse forever
3. **Work tracking** — if it's not in the wing, it doesn't exist
4. **Tag your work** — every commit tagged with agent name
5. **Document chain of thought** — why you changed what you changed
6. **Log mistakes** — record errors and fixes permanently

---

## 12. Communication & Coordination

### 12.1 Coordination Protocol (MemPalace Message Bus)

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

### 12.2 Real-Time Coordination

| Method | Use Case |
|--------|----------|
| **MemPalace MCP** | Persistent task inbox, capability sharing |
| **A2A (Agent-to-Agent)** | Google A2A protocol if all agents support it |
| **Telegram** | Primary messaging channel (cron jobs, reports) |
| **Discord** | Alternative (evaluated, Telegram chosen for simplicity) |
| **3CX Phone** | Voice calls, payphone access from anywhere |

### 12.3 Cron Jobs & Heartbeats

- **Daily briefings** — 7:30 AM EAT (news from worldmonitor.app, HN, daily.dev)
- **Nightly memory sync** — sweep MemPalace, update from other agents, write report
- **Auto-commit** — Forge daemon every 15 minutes
- **QA monitoring** — continuous git commit scanning, auto-issue creation
- **Self-diagnosis** — agents check own health, report issues

---

## 13. External Integrations

### 13.1 MemPalace
- **Role:** Shared knowledge graph across ALL agents (not just Athena)
- **Location:** `/root/.local/share/pipx/venvs/mempalace/`
- **Features:** Spatial memory (wing→room→drawer), knowledge graph, MCP server
- **Agents connected:** Kael (OpenClaw), Zoey (Hermes), Aiden (AgentZero), Ally (DeerFlow)

### 13.2 SearXNG
- **Role:** Self-hosted metasearch engine for agent web searches
- **Location:** Docker container on local machine
- **Use:** Primary web search for agents (privacy-respecting)

### 13.3 Postiz
- **Role:** Self-hosted social media management
- **URL:** `http://localhost:4007`
- **Integrations:** X, LinkedIn, Facebook, Reddit, TikTok
- **Stack:** Docker compose (PostgreSQL, Redis, Temporal, Elasticsearch)

### 13.4 3CX Phone System
- **Role:** Self-hosted PBX for voice communication
- **Use:** Call agents from anywhere, even payphones
- **Setup:** Dedicated VM or Raspberry Pi

---

## 14. Deployment & Installation

### 14.1 Minimal Install (Core Only)

```bash
pip install theagency-core
export LLM_API_KEY="sk-..."
python -m theagency.core.agent  # Starts Athena minimal
```

### 14.2 Full Install (With Addons)

```bash
pip install theagency[all]
theagency install --profile full
# Installs: Noesis, SMS, Dream, Simulation, Auto-Research,
#           Dark Factory, Ghost Factory, Sandbox, Adversarial,
#           External Coordinator, all bridges
```

### 14.3 Selective Install

```bash
theagency install --addons simulation,auto_research,dark_factory
theagency install --domains finance,business,security
theagency install --bridges claude_code,codex,openclaw
```

### 14.4 VM Per Agent (Qubes-Style)

```bash
theagency vm create --agent personal --os ubuntu --resources "2cpu,4gb"
theagency vm create --agent security --os kali --resources "2cpu,4gb"
theagency vm create --agent work --os ubuntu --resources "4cpu,8gb"
```

---

## 15. Version Roadmap

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

---

## 16. Agent Roster (Current)

| Agent | Platform | Role | Created |
|-------|----------|------|---------|
| **Kael ⚡️** | OpenClaw | Main daily agent, coordination lead | Apr 11 |
| **Zoey 😜** | Hermes Agent | Research, installation, multi-agent | Apr 11 |
| **Aiden** | AgentZero | AgentZero instance (Docker) | Apr 11 |
| **Ally** | DeerFlow | DeerFlow agent | Apr 12 |
| **McKenna** | Hermes Agent | Secondary Hermes instance | Apr 12 |
| **Nexus** | Hermes Agent | System monitoring, cleanup | Apr 10 |
| **Jack** | Hermes Agent (new profile) | QA/Critic, competitive analysis | Apr 29 |
| **Clara** | Hermes Agent (new profile) | Buddy UI / Space-Agent fork | Apr 29 |
| **Teresa** | Hermes Agent (new profile) | Build agent, addon scaffolding | Apr 29 |
| **Remex** | Hermes Agent (new profile) | Memory systems, SMS/Noesis | May 1 |
| **Qore** | Hermes Agent (new profile) | QA officer, git monitoring | May 1 |
| **Umbral** | Hermes Agent (new profile) | OSINT/SIGINT, Kali Linux | May 21 |
| **charlie** | Unknown | Task agent | May 30 |

---

## 17. Glossary

| Term | Definition |
|------|------------|
| **The Agency** | Decentralized multi-agent AI system (formerly Project Athena) |
| **Athena Core** | The single minimal agent at the heart of the system |
| **Lattice** | Neo4j graph database storing agent/task/deliverable relationships |
| **SMS** | Sovereign Mind System — local memory core for Athena agents |
| **Noesis** | External second brain (independent project, connects via MCP) |
| **Domain Agent** | Native agent handling a broad life area (Personal, Work, Finance, etc.) |
| **Sub-Specialist** | Fine-tuned agent spawned by a domain agent (e.g., StocksExpert under Finance) |
| **Addon/Node** | Pluggable capability module (Simulation, Auto-Research, Dark Factory, etc.) |
| **Bridge** | External AI framework wrapper (Claude Code, Codex, Goose, etc.) |
| **Dark Factory** | Python-focused continuous code generation and self-improvement system |
| **Ghost Factory** | Multi-language build system (reverse engineering, forking, contributing) |
| **Self-Rectification** | Self-critique and correction system |
| **Adversarial** | Red teaming and risk analysis system |
| **Sandbox** | Isolated execution environment (Clean Room or Athena Mirror) |
| **Inference Node** | Local Ollama-based execution for zero-cost subagents |
| **External Coordinator** | Translation layer for external AI harnesses |
| **Dream Node** | Background memory consolidation during idle cycles |
| **MemPalace** | Shared knowledge graph MCP server (connects all agents) |
| **Wing** | Per-agent isolated storage (`~/.theagency/wings/{name}/`) |
| **Room** | Memory container within a wing |
| **Drawer** | Individual memory item within a room |
| **Hot/Warm/Normal/Cool/Cold** | Memory tier lifecycle (auto-aging) |
| **Qubes Philosophy** | Security by isolation; temporary VMs; dom0 controls |
| **Golden Rule (Jack)** | Plan first, modular code, work tracking, tag commits |
| **FreeLLM** | Smart LLM routing across free/local/paid providers |

---

*Last updated: 2026-09-17 by Hermes Agent*
*Status: Full specification reconstructed from pre-June chat exports. Ready for build.*
