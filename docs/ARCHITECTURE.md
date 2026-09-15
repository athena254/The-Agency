# Nexus — Architecture

## 1. System Overview

Nexus is a **decentralized multi-agent orchestration system** where specialized AI agents collaborate autonomously through a shared graph database (the "Lattice"). Instead of one central boss, agents vote on decisions, score each other's work, and govern themselves.

**Core Philosophy**: A self-organizing team of expert AI agents that improve themselves over time.

---

## 2. Core Components

| Component | Purpose |
|-----------|---------|
| **Lattice** | Shared graph DB (Neo4j + Qdrant) for coordination & memory |
| **Gateway/Butler** | Message relay from interfaces → agents (3 modes) |
| **Domain Agents** | Specialized AI agents (finance, coding, research, etc.) |
| **Buddy** | UI rendering service (forked Space-Agent) |
| **Governance** | Voting, proposals, reputation, access control |
| **SMS** | Semantic Memory Store (retrieval, secrets, spawn, dream, librarian) |
| **External Coordinator** | Router for external agents |
| **Bridges** | Adapters for external coding agents (12 bridges) |
| **Skills** | Modular capabilities (LLM harness, prompt architecture, etc.) |
| **Addons** | Optional modules (sandbox, simulation, adversarial) |

---

## 3. Gateway System (The Butler)

### 3.1 Three Gateway Modes

| Mode | Butler | Buddy | Use Case |
|------|--------|-------|----------|
| **Butler Only** | ✓ routes directly | ✗ | Headless, API-only, lightweight |
| **Separate** (RECOMMENDED) | ✓ routes to Buddy | ✓ separate process | Production |
| **Merged** | ✓ in-process | ✓ same process | Dev/demo/single-container |

### 3.2 Gateway Responsibilities
- Receives messages from interfaces (Telegram, Discord, CLI, VS Code)
- Routes to correct agent(s) in the Lattice
- Handles governance — votes, escalations, system healing
- Monitors health (CPU, memory, disk, agent count, lattice status)

### 3.3 What the Butler Does NOT Do
- ❌ No intelligence of its own ("dumb relay")
- ❌ No local state (except config)
- ❌ Doesn't make decisions — agents in the Lattice do that
- ❌ Doesn't store memory — the Lattice does

### 3.4 Gateway Advantages
1. **Separation of Concerns** — Agents: domain expertise; Butler: interface plumbing + governance
2. **Unified Multi-Interface** — One gateway supports all interfaces simultaneously
3. **Centralized Governance & Safety** — Gatekeeper handles rate limiting, auth, risk scoring once
4. **Observability** — All traffic passes through one point → complete audit trail
5. **Simplified Agent Code** — Agents focus on domain tasks, not interfaces
6. **Security Boundary** — Butler is the only external attack surface
7. **Graceful Degradation** — If one interface fails, others keep working

### 3.5 Butler vs Buddy Trade-off

**Merged Mode**: Butler IS Buddy (single process)
- Simpler deployment, lower latency
- Couples interface to coordination layer

**Separate Mode**: Butler + Buddy as two processes
- Butler stays pure (auth, rate-limit, route only)
- Buddy is a full Nexus node with UI capabilities
- Clean separation: swap UI without touching gateway
- Can scale Buddy independently

---

## 4. Buddy — UI Agent

### 4.1 What It Is
Buddy is a fork of [agent0ai/space-agent](https://github.com/agent0ai/space-agent), adapted as a Nexus node. When upstream updates → Buddy merges/rebase. If upstream stops → Nexus continues independently.

### 4.2 Architecture
- `SpaceAgentNode(BaseAgent)` — inherits from Nexus BaseAgent
- `BuddyAdapter(NodeInterface)` — registers Buddy with capabilities: ui_rendering, user_interface, visualization, agent_control
- `MessageRouter` — routes user queries to domain agents via coordinator
- `WebSocket server` — connects browser UI to Buddy's inbox/outbox
- `personality.md` — Buddy's system prompt/personality

### 4.3 Two-Page Mission Control UI
1. **Mission Control** (default page) — domain agent summaries, charts, etc.
2. **Buddy Page** — accessed via floating icon; Buddy renders complex visualizations here

### 4.4 Domain Agent → Buddy Rendering
Domain agents can request Buddy rendering by setting `render_via: "buddy"` in responses:
```python
{
    "text": "Spending breakdown",
    "render_via": "buddy",
    "component": "chart_bar",
    "data": {"labels": [...], "values": [...]}
}
```

### 4.5 Buddy Fork Sync Strategy
Use **Git subtree** — own the repo, pull upstream regularly:
```bash
git remote add buddy-upstream https://github.com/agent0ai/space-agent.git
git subtree pull --prefix=gateways/buddy buddy-upstream main --squash
```

---

## 5. Sandbox Addon

### 5.1 Purpose
Isolated execution environments where agents can safely run potentially breaking code. Multiple agents can use sandboxes concurrently.

### 5.2 Two Modes

**Mode 1: Clean Room** (EPHEMERAL)
- Empty Python environment + minimal OS
- For pure algorithm testing, math, data processing
- No Nexus system access

**Mode 2: Nexus Mirror** (SNAPSHOT — "Sandbox Gemini")
- Full Nexus codebase inside sandbox
- For testing system changes before mainstream rollout
- Used by self-rectification nodes to validate upgrades
- Isolated Lattice DB instance
- Auto-test execution (pytest)

### 5.3 Backend Options

| Backend | Isolation | Startup | Use Case |
|---------|-----------|---------|----------|
| **Docker** (Primary) | Strong (namespaces + cgroups) | ~50-200ms | Production, untrusted code |
| **Process** (Fallback) | Medium (RLIMIT) | ~5-20ms | Trusted code, fast iteration |
| **RestrictedPython** (Optional) | Weak | ~1ms | Development only |

### 5.4 Why Docker as Primary
1. Strong isolation: Linux namespaces (PID, network, mount, IPC, UTS) + cgroups
2. Filesystem isolation: Read-only root, writable tmpfs, volume mounts
3. Network isolation: Can disable network entirely (--network none)
4. Security: seccomp filters, AppArmor, --cap-drop ALL, no-new-privileges
5. Snapshotting: Docker commit for instant state restore

### 5.5 Why NOT RestrictedPython
- Dependency missing (not in pyproject.toml)
- In-process: Buggy code can segfault interpreter
- Known escape techniques exist
- No filesystem/resource/network isolation
- Unmaintained (last release 2018)

### 5.6 Security Model
Docker backend guarantees:
- ✗ Host filesystem access (mount namespace, read-only root)
- ✗ Network access (network namespace = none)
- ✗ Resource exhaustion (cgroups: CPU, memory, disk, PIDs)
- ✗ Privilege escalation (drop capabilities, no-new-privileges)
- ✗ Host process visibility (PID namespace)
- ✗ Device access (no /dev mounts)

### 5.7 Concurrency
- Per-subject `asyncio.Semaphore` (configurable limit per agent, default 5)
- Global registry: `dict[sandbox_id, (backend, config)]`
- FIFO wait queue when limit reached
- Background cleanup: removes aged sandboxes (>1h default)

### 5.8 Lifecycle
```
CREATING → READY → RUNNING → (COMPLETED | ERROR) → DESTROYED
                     ↓
                (PAUSED / SNAPSHOT)
```

### 5.9 Rollout Stages (Canary Pattern)
After sandbox validates a change:
1. **Phase 1**: Canary (1 node)
2. **Phase 2**: Staging (10% of nodes)
3. **Phase 3**: Gradual (33% → 66% → 100%)
4. **Phase 4**: Mainstream (merge to main)

---

## 6. Adversarial/QA Critic Addon

### 6.1 Purpose
A **quality enforcement system** (NOT red teaming) that simultaneously reviews every agent's work and finds loopholes. Enforces strict QA rules to ensure quality even in autonomy.

### 6.2 Architecture Components

| Component | Purpose |
|-----------|---------|
| **QACritic** | Core review engine with parallel rule execution |
| **QARuleRegistry** | Global rule manager with YAML template loading |
| **QAEnforcer** | Decision engine (ALLOW/BLOCK/REQUIRE_APPROVAL/etc.) |
| **Scenarios** | Adversarial test suites (jailbreak, prompt injection, etc.) |
| **Canary** | Lightweight smoke tests that run on every commit |

### 6.3 Quality Dimensions (10)
Correctness, Security, Completeness, Consistency, Safety, Performance, Usability, Maintainability, Compliance, Robustness

### 6.4 Enforcement Actions
ALLOW, ALLOW_WITH_WARNING, REQUIRE_APPROVAL, BLOCK, RETRY, ESCALATE, QUARANTINE

### 6.5 Built-in Rules
| Rule | Severity | Purpose |
|------|----------|---------|
| `output_not_empty` | HIGH | Ensures agent produces output |
| `no_sensitive_leak` | CRITICAL | Detects API keys, passwords, tokens |
| `execution_within_limits` | MEDIUM | Validates time/memory bounds |
| `error_free_execution` | HIGH | Checks for unhandled exceptions |

### 6.6 Scoring System
Severity-weighted: Critical=4×, High=3×, Medium=2×, Low=1×, Info=0.5×

Quality Levels: excellent (≥0.9), good (≥0.7), fair (≥0.5), poor (≥0.3), failing (<0.3)

### 6.7 Policy Packs
- **strict**: fail_on [CRITICAL, HIGH], min_score 0.9
- **balanced** (default): fail_on [CRITICAL], min_score 0.7
- **permissive**: fail_on [CRITICAL], allow_warnings, min_score 0.3

### 6.8 Research Patterns Integrated
1. **Sparfuchs QA** — Multi-layer adapter pattern, preflight gating, coverage babysitting, gap healing, canary suite, cross-provider audit
2. **Cisco Skill Scanner** — Pack-based composition pipeline, policy knobs, signature/YARA rules, SARIF reporting
3. **Giskard OSS** — Scenario-based testing, LLM-based checks with Jinja2 templates, hierarchical scoring

---

## 7. Integration Architecture

```
User (Telegram/Discord/CLI/VS Code)
        ↓
   [Gateway/Butler]
        ↓
   ┌─────────────────────────────────────────┐
   │              Lattice (Graph DB)          │
   │  ┌───────────────────────────────────┐  │
   │  │  Domain Agents                    │  │
   │  │  (finance, coding, research...)   │  │
   │  └───────────────────────────────────┘  │
   │  ┌───────────────────────────────────┐  │
   │  │  Buddy (UI Agent)                 │  │
   │  │  (charts, tables, cards, forms)   │  │
   │  └───────────────────────────────────┘  │
   └─────────────────────────────────────────┘
        ↓
   [Addons]
   ├── Sandbox (isolated execution)
   ├── Adversarial (QA Critic)
   └── Simulation (social simulation)
```

---

## 8. Directory Structure

```
nexus/
├── docs/
│   ├── ARCHITECTURE.md          (this file)
│   └── SPECIFICATION.md
├── spec/
│   └── (detailed specs per component)
├── athena/
│   ├── gateways/
│   │   ├── launcher.py
│   │   ├── butler_only.py
│   │   ├── butler_separate.py
│   │   ├── butler_merged.py
│   │   ├── gateway_agent.py
│   │   ├── adapters/
│   │   │   ├── telegram_adapter.py
│   │   │   ├── cli_adapter.py
│   │   │   ├── vscode_adapter.py
│   │   │   └── discord_adapter.py
│   │   ├── buddy/
│   │   │   ├── node.py
│   │   │   ├── personality.md
│   │   │   ├── components/
│   │   │   │   ├── registry.py
│   │   │   │   └── renderers.py
│   │   │   └── backend/
│   │   │       └── websocket_server.py
│   │   └── CONFIG/
│   │       └── gateway_modes.yaml
│   ├── CORE/
│   │   ├── helpers/
│   │   │   └── buddy_client.py
│   │   ├── orchestrator/
│   │   ├── gatekeeper/
│   │   ├── safety/
│   │   └── sms/
│   ├── bridges/
│   ├── skills/
│   └── EXTERNAL_COORDINATOR/
├── addons/
│   ├── adversarial/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── critic.py
│   │   ├── enforcer.py
│   │   ├── registry.py
│   │   ├── scenarios.py
│   │   ├── canary.py
│   │   ├── cli.py
│   │   ├── README.md
│   │   ├── SKILL.md
│   │   ├── templates/
│   │   ├── examples/
│   │   └── tests/
│   ├── sandbox/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── sandbox_manager.py
│   │   ├── sandbox_api.py
│   │   ├── sandbox_addon.py
│   │   ├── sandbox_service.py
│   │   ├── sandbox_client.py
│   │   ├── Dockerfile.base
│   │   ├── backends/
│   │   │   ├── base.py
│   │   │   ├── docker_backend.py
│   │   │   ├── process_backend.py
│   │   │   └── restricted_backend.py
│   │   ├── scripts/
│   │   ├── examples/
│   │   └── tests/
│   └── simulation/
├── tests/
├── config/
├── vendor/
└── README.md
```

---

## 9. Key Design Decisions

### 9.1 Decentralized Coordination
No single boss agent. Agents vote, score each other, and govern themselves through proposals.

### 9.2 Graph Database (Lattice)
Neo4j + Qdrant for coordination & memory. Provides shared state without centralization.

### 9.3 Pluggable Backends
All critical components (sandbox, gateway) support multiple backends with clean abstraction layers.

### 9.4 Async-First
All components use `async/await` for scalability to hundreds of concurrent agents.

### 9.5 Fork-and-Adapt
Buddy is forked from Space-Agent. When upstream updates, merge/rebase. If upstream stops, continue independently.

### 9.6 Canary Rollout
Changes to Nexus core are validated in Mirror-mode sandboxes before staged rollout (1 → 10% → 100% of nodes).
