# The Agency — Complete Reference

## Project Overview

**The Agency** is a decentralized multi-agent orchestration system where specialized AI agents collaborate autonomously through a shared graph database (the "Lattice"). Agents vote, score each other's work, and govern themselves.

> A lattice of domain agents, governed by peers, healing itself, thinking together.

---

## System Architecture

### Core Components

| Component | Purpose |
|-----------|---------|
| **Lattice** | Shared graph DB (Neo4j + Qdrant) for coordination & memory |
| **Gateway/Butler** | Message relay from interfaces → agents (3 modes) |
| **Domain Agents** | 8 specialized managers (Finance, Personal, Work, etc.) |
| **Sub-Agents** | Dynamically spawned specialists via template factory |
| **Buddy** | UI rendering agent (forked Space-Agent) |
| **Governance** | Voting, proposals, reputation, access control |
| **SMS** | Semantic Memory Store (retrieval, secrets, spawn, dream, librarian) |
| **QA Critic** | Quality enforcement with parallel rule execution |
| **Sandbox** | Isolated code execution (Clean Room + Agency Mirror) |
| **Bridges** | External AI framework wrappers (Claude Code, Codex, etc.) |

---

## Top-Level Directory Structure

```
theagency/
├── athena/                    # Main Python package
│   ├── core/                  # Core services (agent base, LLM harness, etc.)
│   ├── gateways/              # Gateway/Butler system
│   ├── skills/                # Pluggable skill modules
│   ├── addons/                # Optional modules (sandbox, adversarial, simulation)
│   ├── bridges/               # External AI framework wrappers
│   ├── tools/                 # Tool libraries (finance, personal, etc.)
│   ├── DOMAIN_AGENTS/         # Domain manager agents
│   └── DOCS/                  # Architecture docs, version specs
├── NODES/                     # Sovereign node bundles
├── FORGE/                     # Department coordination
├── INTEGRATIONS/              # Standalone integrations
├── CONFIG/                    # YAML configs
├── docs/                      # Specification documents
├── tests/                     # Test suites
└── scripts/                   # Utility scripts
```

---

## Core Services (athena/core/)

### Foundation Layer (Sprint 1 Complete)

| Service | Purpose |
|---------|---------|
| `agent/base.py` | BaseAgent ABC — implement `execute()` |
| `agent/aether_wrapper.py` | AetherSupervisedAgent (durable execution) |
| `agent/agent_runner.py` | Subagent process entrypoint |
| `agent/secrets_client.py` | SMS Secrets Node adapter |
| `sms/` | SMS v2.0 nodes (secrets, retrieval, librarian, cpr, dream) |
| `spawn/` | Secure sub-agent lifecycle (sandbox, ACL, audit, resource limits) |
| `retrieval/` | Search abstraction (vector + graph hybrid) |
| `llm_harness/` | Pluggable LLM provider abstraction |
| `safety/` | Guardrails system |
| `runtime/` | AgentRuntime — process supervision |
| `orchestrator/` | OrchestratorClient — governance & evaluation |
| `lattice/` | LatticeClient — Neo4j + Qdrant connectivity |
| `gatekeeper/` | Gatekeeper — request admission control |
| `anomaly_monitor/` | Anomaly detection |
| `capability_registry.py` | Capability tracking |
| `skills.py` | Skill loading & registry |

---

## Gateway System (Butler)

### Three Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **Butler Only** | Routes directly to domain agents, no Buddy | Headless, API-only |
| **Separate** (RECOMMENDED) | Butler + Buddy as separate processes | Production |
| **Merged** | Single process combining both | Dev/demo |

### Routing Decision Tree

```python
async def route_message(message, sender):
    # 1. Explicit Buddy targeting
    if message.target == "buddy" or message.command.startswith("/buddy"):
        await route_to_buddy_conversation(message)
        return
    
    # 2. Agent mention
    if message.target and message.target.startswith("@"):
        await route_to_domain_agent(message)
        return
    
    # 3. Domain agent render hint
    if message.has_rendering_hint and message.render_via == "buddy":
        await route_response_to_buddy(message)
        return
    
    # 4. Default: route to Buddy
    await route_to_buddy_conversation(message)
```

### Gateway Files

| File | Purpose |
|------|---------|
| `launcher.py` | Unified entry point |
| `gateway_agent.py` | Core gateway with governance hooks |
| `butler_only.py` | Butler-only mode |
| `butler_separate.py` | Separate mode (production) |
| `butler_merged.py` | Merged mode |
| `adapters/telegram_adapter.py` | Telegram connector |
| `adapters/cli_adapter.py` | CLI connector |
| `adapters/vscode_adapter.py` | VS Code connector |
| `adapters/discord_adapter.py` | Discord connector (stub) |
| `CONFIG/gateway_modes.yaml` | Central configuration |

### Resilience Features

| Feature | Implementation |
|---------|----------------|
| Circuit Breaker | CLOSED → OPEN → HALF_OPEN state machine |
| Health Monitoring | Per-agent health scores (0-1) |
| Confidence Classifier | Keyword-based domain classification |
| Response Validator | Auto-detects render_via flag |
| Buddy Fallback | Mission Control when Buddy offline |
| Session Context | Last 10 queries per user |
| Retry Logic | 2 retries, exponential backoff |
| Clarification | Ask user when confidence < threshold |

### Circuit Breaker Configuration

```yaml
butler:
  circuit_breaker:
    failure_threshold: 3
    recovery_timeout: 30.0
    error_rate_threshold: 0.5
    minimum_calls: 5
    per_agent:
      personal_agent:
        failure_threshold: 5
  routing:
    confidence_threshold: 0.6
  health_check_interval: 30.0
```

---

## Buddy — UI Agent

### What It Is
Buddy is a fork of agent0ai/space-agent, adapted as a The Agency node. Renders visualizations in the browser.

### Two Pages
1. **Mission Control** (default) — Domain agent summaries
2. **Buddy Page** (floating icon) — Complex visualizations

### Component Renderers

| Component | Description |
|-----------|-------------|
| `chart_bar` | Vertical/horizontal bar charts (SVG) |
| `chart_line` | Line charts (SVG polyline) |
| `chart_pie` | Pie charts (CSS conic-gradient) |
| `table` | HTML tables |
| `card` | Info cards |
| `form` | Interactive forms |

### Rendering Flow

```
Domain Agent → returns {render_via: "buddy", component: "chart_pie", data: {...}}
    → Butler sees render_via flag → routes to Buddy
    → Buddy renders via ComponentRegistry → returns HTML
    → User sees chart in browser
```

### Routing Flow (Complete)

```
User: "Show my budget pie chart"
    ↓
Butler receives message
    ↓
No @buddy, no @finance → default to Buddy
    ↓
Buddy understands "budget" → calls FinanceAgent
    ↓
FinanceAgent returns:
{
  "text": "Here's your May spending breakdown:",
  "render_via": "buddy",
  "component": "chart_pie",
  "data": {"labels": [...], "values": [...]}
}
    ↓
Butler sees render_via="buddy" → forwards to Buddy
    ↓
Buddy renders pie chart HTML
    ↓
User sees chart in browser
```

---

## Hierarchical Domain Agent System

### Architecture

```
Domain Agent (PersonalAgent)
    ↓ analyzes query
    ↓ determines specializations needed
    ↓ spawns ONLY relevant children
    ↓ runs them in parallel
    ↓ synthesizes results
    → returns response
```

### 8 Domain Agents

| Domain | Sub-Agent Specializations |
|--------|--------------------------|
| **Personal** | habits, scheduling, relationships, life_coordination, health, fitness |
| **Finance** | market_analysis, portfolio, risk, news |
| **Work** | (template-ready) |
| **Coding** | (template-ready) |
| **Social** | (template-ready) |
| **Learning** | (template-ready) |
| **Research** | (template-ready) |
| **Business** | (template-ready) |

### Template-Driven Sub-Agents

```yaml
# templates/finance/market_analyst.yaml
sub_agent: MarketAnalyst
domain: finance
version: "1.0"

system_prompt: |
  You are a market analyst. Analyze {ticker} using...

tools:
  - search_market_data
  - fetch_price_history
  - calculate_indicators

model:
  provider: anthropic
  model: claude-sonnet-4
  temperature: 0.3

resource_limits:
  max_runtime_seconds: 120
  memory_mb: 512
  max_llm_calls: 10

optional: true
default_enabled: true

expertise_tags:
  - price_analysis
  - trend_identification

output_schema:
  format: json
  schema:
    price: float
    trend: string
    indicators: object

dependencies:
  - portfolio_manager
```

### Factory Pattern

```python
# Create agent from template
agent = factory.create(
    template_name="market_analyst",
    domain="finance",
    instance_id="market_analyst_001"
)

# AgentBuilder for complex construction
agent = AgentBuilder(factory) \
    .with_template('doctor', 'personal') \
    .with_override('model.temperature', 0.2) \
    .with_tool('check_symptoms') \
    .build()
```

### Dynamic Routing

```python
class PersonalAgent:
    CHILD_AGENT_SPECIALIZATIONS = {
        'health': {
            'template': 'doctor',
            'keywords': ['symptoms', 'pain', 'headache', 'fever', 'sick', 'medical'],
            'tools': ['check_symptoms', 'log_health_metric', 'get_health_history', 'suggest_wellness_actions']
        },
        'fitness': {
            'template': 'fitness',
            'keywords': ['workout', 'exercise', 'gym', 'running', 'weight', 'muscle'],
            'tools': ['get_workout_history', 'suggest_exercises', 'log_workout', 'calculate_progression']
        },
        # ...
    }
    
    def _determine_specializations(self, query: str) -> list[str]:
        """Analyze query → return only relevant specializations"""
        query_lower = query.lower()
        matched = []
        for spec, config in self.CHILD_AGENT_SPECIALIZATIONS.items():
            if any(kw in query_lower for kw in config['keywords']):
                matched.append(spec)
        return matched
    
    async def analyze(self, query: str) -> str:
        specs = self._determine_specializations(query)
        # "headache" → only spawns ['health'], not all 6
        results = await asyncio.gather(*[
            self._spawn_child_agent(spec, query) for spec in specs
        ])
        return self._synthesize(query, results)
```

### Self-Extending Meta-System

```
1. ExpertiseGapDetector
   - Monitors all user queries
   - Extracts required skills/keywords
   - Compares against existing agent capabilities
   - Identifies gaps (e.g., 12 PostgreSQL queries, no DB agent)

2. AgentDesigner
   - Clusters related gaps
   - Generates template proposals
   - Presents to user for approval

3. Approval UI
   📊 New Agent Proposal: Database Engineer
   Detected: You've asked 12 database-related questions this week.
   [1] Install this agent
   [2] Customize first
   [3] Not now
   [4] Never suggest database agents

4. TemplateInstaller
   - Saves approved template
   - Registers with AgentRegistry
   - Notifies domain agents
```

---

## QA Critic Addon

### Quality Dimensions (10)
Correctness, Security, Completeness, Consistency, Safety, Performance, Usability, Maintainability, Compliance, Robustness

### Scoring
Severity-weighted: Critical=4×, High=3×, Medium=2×, Low=1×, Info=0.5×

Quality Levels: excellent (≥0.9), good (≥0.7), fair (≥0.5), poor (≥0.3), failing (<0.3)

### Enforcement Actions
ALLOW, ALLOW_WITH_WARNING, REQUIRE_APPROVAL, BLOCK, RETRY, ESCALATE, QUARANTINE

### Built-in Rules

| Rule | Severity | Dimension | Purpose |
|------|----------|-----------|---------|
| output_not_empty | HIGH | Completeness | Ensures agent produces output |
| no_sensitive_leak | CRITICAL | Security | Detects API keys, passwords, tokens |
| execution_within_limits | MEDIUM | Performance | Validates time/memory bounds |
| error_free_execution | HIGH | Robustness | Checks for unhandled exceptions |
| instruction_fidelity | HIGH | Correctness | LLM-based check output matches instructions |
| format_compliance | MEDIUM | Consistency | Validates output format matches schema |

### Sensitive Patterns Detected

```python
SENSITIVE_PATTERNS = [
    r'(api[_\s]?key|apikey)\s*[=:]\s*["\'][\w\-]+["\']',
    r'(password|passwd|pwd)\s*[=:]\s*["\'][^"\']+["\']',
    r'(secret[_\s]?key|secretkey)\s*[=:]\s*["\'][\w\-]+["\']',
    r'(token|access[_\s]?token)\s*[=:]\s*["\'][\w\-]+["\']',
    r'ssh-rsa\s+AAAA[^\s]+',
    r'-----BEGIN\s+(RSA|DSA|EC|OPENSSH)\s+PRIVATE\s+KEY-----',
]
```

### CLI Commands

```bash
theagency qa review --agent <id> --policy strict
theagency qa rules list --dimension security --severity critical
theagency qa scenarios list
theagency qa scenarios run --suite jailbreak,prompt_injection
theagency qa canary run
theagency qa canary watch --interval 30
theagency qa enforce --agent <id> --run-id <run_id>
theagency qa audit show <run_id>
theagency qa gaps heal --agent <id>
theagency qa policy export > policy.yaml
```

---

## Sandbox Addon

### Two Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **Clean Room** | Empty Python environment | Algorithm testing, math |
| **Agency Mirror** | Full codebase clone | Testing system changes |

### Backends

| Backend | Isolation | Startup | Use Case |
|---------|-----------|---------|----------|
| Docker (Primary) | Strong | ~50-200ms | Production, untrusted |
| Process (Fallback) | Medium | ~5-20ms | Trusted, fast iteration |
| RestrictedPython (Optional) | Weak | ~1ms | Development only |

### Configuration

```python
@dataclass
class AthenaMirrorConfig:
    include: bool = False
    source: Literal["git", "local", "image"] = "local"
    repo_path: Optional[Path] = None
    branch: Optional[str] = None
    commit: Optional[str] = None
    install_editable: bool = True
    extra_dependencies: list[str] = field(default_factory=list)
    mount_lattice: bool = False
    lattice_backend: str = "sqlite"
    lattice_db_path: str = "/workspace/lattice-test.db"
    auto_test: bool = False
    test_command: str = "pytest tests/ -x --tb=short"
    test_timeout: int = 600
    persistent_after_test: bool = False
    ttl_hours: int = 24
```

### Security Guarantees
- ✗ Host filesystem access
- ✗ Network access (default none)
- ✗ Resource exhaustion
- ✗ Privilege escalation
- ✗ Host process visibility
- ✗ Device access

---

## Bridges — External AI Frameworks

### Purpose
Allow Agency agents to use external AI frameworks and coordinate with them at native speed with full feature access.

### Architecture

```
athena/bridges/
├── base.py                    # Bridge ABC
├── registry.py                # Bridge discovery
├── claude_code_bridge.py      # Claude Code wrapper
├── codex_bridge.py            # OpenAI Codex wrapper
├── gemini_cli_bridge.py       # Google Gemini CLI wrapper
├── openclaw_bridge.py         # OpenClaw wrapper
├── hermes_bridge.py           # Hermes wrapper
├── agent_zero_bridge.py       # Agent Zero wrapper
├── deerflow_bridge.py         # DeerFlow 2.0 wrapper
└── CONFIG/
    └── bridges.yaml           # Bridge configuration
```

### Bridge Interface

```python
class Bridge(ABC):
    """Base class for external AI framework bridges."""
    
    @abstractmethod
    async def execute(
        self,
        task: str,
        context: dict,
        timeout: int = 300,
        budget: float = 5.00,
        allow_tools: list[str] = None,
    ) -> BridgeResult:
        """Execute task using external framework."""
        ...
    
    @abstractmethod
    async def stream(
        self,
        task: str,
        context: dict,
    ) -> AsyncGenerator[str, None]:
        """Stream output from external framework."""
        ...
    
    @abstractmethod
    def capabilities(self) -> dict:
        """Return framework capabilities."""
        ...
```

### Bridge Result

```python
@dataclass
class BridgeResult:
    backend: str
    status: Literal["completed", "failed", "timeout", "cancelled"]
    output: str
    artifacts: list[str]      # Created/modified files
    usage: dict               # {prompt_tokens, completion_tokens, cost}
    duration_seconds: float
    error: Optional[str] = None
```

### Bridges to Build

| Bridge | Type | Invocation | Notes |
|--------|------|------------|-------|
| **Claude Code** | Subprocess | `claude` CLI binary | Stream I/O, permission prompts |
| **Codex** | API | OpenAI API | Stateless, context prep |
| **Gemini CLI** | Subprocess | `gemini` CLI | Google AI integration |
| **OpenClaw** | HTTP API | `POST /api/agent` | RESTful, needs auth |
| **Hermes** | ACP | `hermes agent run` | ACP client library |
| **Agent Zero** | Subprocess | Containerized | Docker-based |
| **DeerFlow 2.0** | HTTP API | `POST /v1/flow` | JSON task spec |

### Bridge Configuration

```yaml
# athena/CONFIG/bridges.yaml
bridges:
  claude_code:
    enabled: true
    binary: claude
    default_model: claude-sonnet-4
    max_timeout: 300
    max_budget: 5.00
    sandbox: true
    allowed_tools:
      - bash
      - file_read
      - file_write
  
  codex:
    enabled: true
    provider: openai
    model: o3-mini
    api_key_env: OPENAI_API_KEY
    max_timeout: 120
  
  gemini_cli:
    enabled: true
    binary: gemini
    default_model: gemini-2.5-flash
  
  openclaw:
    enabled: false
    base_url: http://localhost:8080
    api_key_env: OPENCLAW_API_KEY
  
  hermes:
    enabled: false
    binary: hermes
  
  agent_zero:
    enabled: false
    docker_image: agent0ai/agent-zero:latest
  
  deerflow:
    enabled: false
    base_url: http://localhost:8000
    api_key_env: DEERFLOW_API_KEY
```

### Integration with Domain Agents

```python
class WorkAgent(DomainAgent):
    """Work domain can use any bridge."""
    
    def __init__(self, bridge_registry):
        self.bridges = bridge_registry
    
    async def handle_task(self, task: str) -> str:
        if "code" in task.lower():
            # Use Claude Code for coding tasks
            result = await self.bridges.execute(
                backend="claude_code",
                task=task,
                context=self.get_context(),
                timeout=300,
            )
        elif "research" in task.lower():
            # Use DeerFlow for research
            result = await self.bridges.execute(
                backend="deerflow",
                task=task,
            )
        return result.output
```

---

## QA Skill Suite

### Scanners

| Scanner | Purpose |
|---------|---------|
| `code_style_scanner.py` | PEP8, formatting, complexity |
| `import_scanner.py` | Import validity, circular deps |
| `test_coverage_scanner.py` | Coverage analysis |
| `architecture_scanner.py` | Layer violations |
| `security_scanner.py` | Security audit, secrets |
| `doc_scanner.py` | Documentation quality |

### CLI

```bash
python scripts/athena-qa           # Full QA scan
make qa                            # All checks
make lint                          # Code style only
make format                        # Auto-format
make coverage                      # Test coverage
```

---

## Coordination Model

### Lattice — Shared Graph Database

```
Nodes: Agent, Task, Deliverable, Proposal, Vote, Domain, SubDomain, Skill, Secrets
Relationships:
  - SPAWNED_BY
  - MEMBER_OF
  - EXPERT_IN
  - ASSIGNED_TO
  - PRODUCED_BY
  - SCORED_BY
  - HAS_SKILL
```

### Governance

| Tier | Description | Approval |
|------|-------------|----------|
| Tier 1 | Infrastructure replacement | Auto-approve |
| Tier 2 | Multi-agent vote | Majority |
| Tier 3 | Critical changes | Supermajority |

### Peer Scoring
- Every deliverable scored by peers
- Failed agents auto-replaced
- Reputation tracked in Lattice

---

## First Boot Sequence

```
Minute 1: Discovery
  GatewayAgent starts → Discovers all agents → Each announces capabilities

Minute 2: Registration
  Agents write profiles to Lattice
  Tool permissions set
  Rate limits configured
  Peer scoring baseline (everyone starts 50/100)

Minute 3: Ready
  "All agents healthy, monitoring active"
```

---

## Work Tracking System (Jack's Golden Rule)

> **If it's not in the wing, it doesn't exist.**

### After Every Git Commit
- Update tasks/<id>.json → status = "completed"
- Set completed_at = now (ISO8601)
- Add commit hash to task.result.commit
- Move to completed/<year>/<month>/
- Log to metrics/throughput.jsonl

### Directory Structure
```
~/.theagency/wings/wing_<agent>/
├── tasks/<task_id>.json
├── work/<work_id>.json
├── testing/<test_run_id>.json
├── completed/<year>/<month>/
├── metrics/throughput.jsonl
└── state.json
```

---

## Research Foundations

| Source | Patterns Adopted |
|--------|------------------|
| **Sparfuchs QA** | Multi-layer adapter, preflight gating, coverage babysitting |
| **Cisco Skill Scanner** | Pack-based composition, policy knobs, SARIF reporting |
| **Giskard OSS** | Scenario-based testing, LLM checks, hierarchical scoring |

---

## Competitive Analysis

### Competitors Researched

| Project | Stars | Best For |
|---------|-------|----------|
| agent-zero | - | Secure containerized agent |
| goose | 43.6k | Multi-provider MCP agent |
| cline | 20.5k | IDE-integrated coding |
| OpenHands | 28.3k | Sandboxed code execution |
| OpenCode | - | Terminal LSP-native coding |
| DeerFlow | - | Production LangGraph agent |
| pi-mono | - | Clean modular monorepo |
| Hermes | - | Multi-agent protocol |
| OpenClaw | - | Multi-channel + device automation |
| PAI | - | Persistent learning AI |

### Where The Agency Wins
1. Truly decentralized (no central gateway bottleneck)
2. Domain agents baked in (8 domains)
3. Local-first by design (all data on machine)
4. Orthogonal Security layer
5. Lightweight (runs on 2-core, 4GB)
6. Python-native (ML/data science)
7. Structured memory (drawers: wing → room → item)
8. No lock-in (not tied to Claude, MCP, LangGraph, or VS Code)

### Where The Agency Falls Short (Gaps)

| Priority | Gap | Solution |
|----------|-----|----------|
| P0 | Provider diversity | Add OpenAI/Google/Ollama to LLM harness |
| P0 | Sandbox isolation | Docker per subagent |
| P1 | Extension marketplace | Community skill registry |
| P1 | IDE integration | VS Code + JetBrains plugins |
| P1 | Multi-tenant SaaS | RBAC, billing, scaling |
| P2 | Web/Desktop UI | React dashboard + Electron |
| P2 | LSP code intelligence | tree-sitter + language servers |
| P2 | Tool breadth | Browser, vision, audio |
| P3 | Human-in-the-loop | Clarification/approval flows |
| P3 | Observability | Tracing, metrics, audit logs |
| P4 | Cloud Scalability | Kubernetes, distributed Lattice |

---

## Pulse Analysis (Reference Architecture)

### What It Is
Pulse is a production-grade AI coding assistant with advanced features worth studying.

### Key Innovations to Adopt

| Feature | Description | Value |
|---------|-------------|-------|
| AutoDream | Background memory consolidation | Fix Lattice bloat automatically |
| Coordinator Mode | Multi-agent with task notifications | Upgrade subagent protocol |
| MCP Client | Full Model Context Protocol | Access 70+ MCP servers |
| IDE Bridge | VS Code/JetBrains integration | Blueprint for plugins |
| BashTool Security | Command allowlist, truncation | Harden sandbox |
| GrepTool | Parallel search, binary detection | Faster file search |
| Task Framework | Background tasks with progress | Better UX |
| Permission + Scratchpad | Per-tool permissions | Wing permissions |

### Integration Strategy

**Phase 1 (1 month):**
- AutoDream consolidation daemon → athena/services/consolidation/
- Task notification protocol → structured subagent responses
- Scratchpad directories → ~/.theagency/wings/{wing}/scratchpad/

**Phase 2 (2-3 months):**
- MCP client → athena/mcp/
- Dynamic tool registration
- Tool quality upgrades (BashTool, GrepTool, LSPTool)

**Phase 3 (3-4 months):**
- IDE Bridge → separate theagency-vscode/ repo
- WebSocket + React webview
- Code actions: Refactor, Explain, Add Tests

---

## Git Adoption

All code must be committed. Forge department auto-commits every 15 minutes.

```bash
# Auto-commit helper
import subprocess
from datetime import datetime
def git_commit():
    subprocess.run(['git', 'add', '-A', 'theagency/your-section/'])
    subprocess.run(['git', 'commit', '-m', f'Agent YOUR_NAME: update — {datetime.utcnow()}', '--no-verify'])
```

---

## Glossary

| Term | Definition |
|------|------------|
| **Lattice** | Shared graph database (Neo4j + Qdrant) |
| **Butler** | Gateway agent — dumb message relay |
| **Buddy** | UI rendering agent |
| **Clean Room** | Sandbox Mode 1 — empty Python environment |
| **Agency Mirror** | Sandbox Mode 2 — full codebase clone |
| **QA Critic** | Quality enforcement system |
| **Canary** | Lightweight smoke test |
| **Bridge** | External AI framework wrapper |
| **Gap Healing** | Auto-retry for prior failures |
| **Preflight** | Pre-execution cost/time estimation |
| **Pack** | Group of related rules |
| **Scenario** | Multi-step adversarial test |
| **AgentBuilder** | Fluent utility for child agent construction |
| **Self-Extending** | System creates new agents based on usage |
| **Jack's Golden Rule** | "If it's not in the wing, it doesn't exist" |
| **AutoDream** | Background memory consolidation |

---

*Last updated: 2026-09-16 by Hermes Agent*
*All references to previous project names have been updated to "The Agency"*
