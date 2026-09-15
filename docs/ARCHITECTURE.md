# Nexus — Architecture (Updated)

## System Overview

Nexus is a **decentralized multi-agent orchestration system** where specialized AI agents collaborate autonomously through a shared graph database (the "Lattice"). Agents vote, score each other, and govern themselves.

---

## Core Components

| Component | Purpose |
|-----------|---------|
| **Lattice** | Shared graph DB (Neo4j + Qdrant) for coordination & memory |
| **Gateway/Butler** | Message relay from interfaces → agents (3 modes) |
| **Domain Agents** | 8 specialized managers (Finance, Personal, Health, etc.) |
| **Sub-Agents** | Dynamically spawned specialists via template factory |
| **Buddy** | UI rendering agent (forked Space-Agent) |
| **Governance** | Voting, proposals, reputation, access control |
| **SMS** | Semantic Memory Store (retrieval, secrets, spawn, dream, librarian) |
| **QA Critic** | Quality enforcement with parallel rule execution |
| **Sandbox** | Isolated code execution (Clean Room + Nexus Mirror) |

---

## Gateway System (Butler)

### Three Modes
| Mode | Butler | Buddy | Use Case |
|------|--------|-------|----------|
| **Butler Only** | ✓ routes directly | ✗ | Headless, API-only |
| **Separate** (RECOMMENDED) | ✓ routes to Buddy | ✓ separate process | Production |
| **Merged** | ✓ in-process | ✓ same process | Dev/demo |

### How the Butler Routes

When you text: "Show my budget pie chart"

1. **Parse**: Extract keywords, intent, entities
2. **Consult Agent Registry**: Query Lattice for agent capabilities
3. **Match & Score**: Keyword overlap + semantic similarity + peer score + load + history
4. **Pick Winner**: Highest score wins
5. **Delegate**: Route to domain agent

### Routing Decision Tree (Separate Mode)
```python
if message.target == "buddy" or message.command.startswith("/buddy"):
    → Route to Buddy conversation
elif message.target and message.target.startswith("@"):
    → Route to mentioned domain agent
else:
    → Route to Buddy (default, Buddy decides)
```

### Butler Does NOT Do
- ❌ No intelligence ("dumb relay")
- ❌ No decisions — agents decide
- ❌ No memory storage — Lattice stores
- ❌ No local state (except config)

### Resilience Features (Downside Fixes)
1. **Circuit Breaker** — Auto-opens after failures, half-open for recovery
2. **Health Monitoring** — Per-agent health scores (0-1)
3. **Confidence Classifier** — Keyword-based domain classification with context
4. **Response Validator** — Auto-detects `render_via="buddy"` flag
5. **Buddy Fallback** — Falls back to Mission Control when Buddy down
6. **Session Context** — Tracks last 10 queries per user
7. **Retry Logic** — 2 retries with exponential backoff (1s, 2s)
8. **Clarification** — Asks user when confidence < threshold

---

## Buddy — UI Agent

### What It Is
Buddy is a fork of [agent0ai/space-agent](https://github.com/agent0ai/space-agent), adapted as a Nexus node. It renders visualizations in the browser.

### Two Pages
1. **Mission Control** (default) — Domain agent summaries
2. **Buddy Page** (floating icon) — Complex visualizations

### Rendering Flow
```
Domain Agent → returns {render_via: "buddy", component: "chart_pie", data: {...}}
    → Butler sees render_via flag → routes to Buddy
    → Buddy renders via ComponentRegistry → returns HTML
    → User sees chart in browser
```

### Built-in Components
- chart_bar (vertical/horizontal SVG bars)
- chart_line (SVG polyline)
- chart_pie (CSS conic-gradient)
- table (sortable grids)
- card (key-value displays)
- form (interactive inputs)

### Buddy Personality
Defined in `buddy/personality.md` — injected as system prompt. Proactive, concise, uses emojis sparingly.

---

## Hierarchical Domain Agent System

### Architecture (Not Flat Parallel)
```
Domain Agent (PersonalAgent)
    ↓ analyzes query
    ↓ determines specializations needed
    ↓ spawns ONLY relevant children
    ↓ runs them in parallel
    ↓ synthesizes results
    → returns response
```

### Key Difference: Dynamic, Not Fixed
**Old (Flat)**: Always spawn all 6 sub-agents
**New (Hierarchical)**: Analyze query → spawn only 1-3 relevant specialists

Example: "headache" → only spawns `personal_doctor` (not all 6)

### Template-Driven
- Specializations are YAML templates, not code
- Non-developers can create new agent types
- Hot-swappable without redeployment

### AgentBuilder Utility
Fluent API for constructing child agents:
```python
AgentBuilder(factory)
    .with_template('doctor', 'personal')
    .with_override('model.temperature', 0.2)
    .build()
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

### Factory Pattern
```python
agent = factory.create(
    template_name="market_analyst",
    domain="finance",
    instance_id="market_analyst_001"
)
```

### Self-Extending Meta-System (Future)
The system watches for expertise gaps and designs new agents:

1. **ExpertiseGapDetector** — Monitors queries, extracts skills, finds gaps
2. **AgentDesigner** — Generates template proposals from gap clusters
3. **Approval UI** — "You seem to need a Database Engineer. Create?"
4. **TemplateInstaller** — Saves approved template, registers with system

Example growth:
- Week 1: Personal domain only
- Week 2: User asks database questions → System suggests Database Engineer
- Week 3: User asks crypto DB questions → System suggests Crypto DB Specialist

---

## Sandbox Addon

### Two Modes
**Mode 1: Clean Room** — Empty Python environment
**Mode 2: Nexus Mirror (Gemini)** — Full Nexus codebase clone for testing changes

### Backends
| Backend | Isolation | Use Case |
|---------|-----------|----------|
| Docker (Primary) | Strong (namespaces + cgroups) | Production, untrusted code |
| Process (Fallback) | Medium (RLIMIT) | Trusted code, fast iteration |
| RestrictedPython (Optional) | Weak | Development only |

### Why Docker
- Filesystem, network, process isolation
- Resource limits (CPU, memory, PIDs)
- seccomp/AppArmor security
- Snapshot/restore via docker commit

### Concurrency
- Per-subject asyncio.Semaphore (default 5)
- FIFO wait queue when limit reached
- Background cleanup of aged sandboxes

### Security Guarantees
- ✗ Host filesystem access
- ✗ Network access (default none)
- ✗ Resource exhaustion
- ✗ Privilege escalation
- ✗ Host process visibility

---

## QA Critic Addon

### Purpose
Quality enforcement system (NOT red teaming). Simultaneously reviews every agent's work.

### Quality Dimensions (10)
Correctness, Security, Completeness, Consistency, Safety, Performance, Usability, Maintainability, Compliance, Robustness

### Scoring
Severity-weighted: Critical=4×, High=3×, Medium=2×, Low=1×, Info=0.5×

Quality Levels: excellent (≥0.9), good (≥0.7), fair (≥0.5), poor (≥0.3), failing (<0.3)

### Enforcement Actions
ALLOW, ALLOW_WITH_WARNING, REQUIRE_APPROVAL, BLOCK, RETRY, ESCALATE, QUARANTINE

### Built-in Rules
| Rule | Severity | Dimension |
|------|----------|-----------|
| output_not_empty | HIGH | Completeness |
| no_sensitive_leak | CRITICAL | Security |
| execution_within_limits | MEDIUM | Performance |
| error_free_execution | HIGH | Robustness |

### Policy Packs
- **strict**: fail_on [CRITICAL, HIGH], min_score 0.9
- **balanced**: fail_on [CRITICAL], min_score 0.7
- **permissive**: fail_on [CRITICAL], allow_warnings, min_score 0.3

### Scenario Testing
Jailbreak, PromptInjection, DataExfiltration, ToolMisuse, Boundary scenarios

### Canary Suite
Lightweight smoke tests: output_not_empty, uses_at_least_one_tool, finishes_within_timeout, no_critical_violations

---

## Integration Architecture

```
User (Telegram/Discord/CLI/VS Code)
        ↓
   [Gateway/Butler]
        ↓
   ┌─────────────────────────────────────────┐
   │              Lattice (Graph DB)          │
   │  ┌───────────────────────────────────┐  │
   │  │  Domain Agents (8)                │  │
   │  │  (dynamically spawn children)     │  │
   │  └───────────────────────────────────┘  │
   │  ┌───────────────────────────────────┐  │
   │  │  Buddy (UI Renderer)              │  │
   │  │  (charts, tables, cards, forms)   │  │
   │  └───────────────────────────────────┘  │
   └─────────────────────────────────────────┘
        ↓
   [Addons]
   ├── Sandbox (isolated execution)
   ├── QA Critic (quality review)
   └── Simulation (social simulation)
```

### Cross-Component Flow
```
User → Gateway → Lattice → Domain Agent
                  ↓
                Buddy (rendering)
                  ↓
        ┌─────────┴─────────┐
        ↓                   ↓
   Sandbox              QA Critic
   (execution)          (quality review)
        ↓                   ↓
   Metrics ──────────→ Enforcement Decision
   (exit code,         (ALLOW/BLOCK/
    memory, time)       RETRY/ESCALATE)
```

---

## Work Tracking System (Jack's Golden Rule)

### The Golden Rule
> **If it's not in the wing, it doesn't exist.**

### After Every Git Commit
- [ ] Does commit message reference a task ID? (TASK-123)
- [ ] Update tasks/<id>.json → status = "completed"
- [ ] Set completed_at = now (ISO8601)
- [ ] Add commit hash to task.result.commit
- [ ] If work item done → move to completed/<year>/<month>/
- [ ] Log to metrics/throughput.jsonl
- [ ] Sync to MemPalace

### When Starting New Work
- [ ] Create tasks/<uuid>.json with status = "in_progress"
- [ ] Set actual_effort.started_at = now
- [ ] Add task_id to work/<work_id>.json.tasks[]
- [ ] Update state.json current_activity

### Directory Structure
```
~/.athena/wings/wing_<agent>/
├── tasks/<task_id>.json
├── work/<work_id>.json
├── testing/<test_run_id>.json
├── completed/<year>/<month>/
├── metrics/throughput.jsonl
└── state.json
```

---

## Design Decisions

### Why Decentralized Coordination
No single boss agent. Agents vote, score each other, govern themselves.

### Why Hierarchical Agents (Not Flat)
1. **Efficiency** — Only spawn needed agents
2. **Cost** — Fewer LLM calls
3. **Latency** — Parallel execution of relevant agents only
4. **Maintainability** — Children isolated
5. **Extensibility** — New capabilities = new YAML file

### Why Template-Driven
- Specializations are data, not code
- Non-developers can create new agent types
- Hot-swappable without redeployment

### Why Template Inheritance
- Children inherit parent context (tools, memory)
- Automatic context continuity
- Parent's tools/memory available to child

### Why Factory Pattern
- All agent creation through factory ensuring consistency
- AgentBuilder for complex constructions
- Simple factory.create() for simple cases

### Why Fork-and-Adapt
Buddy forked from Space-Agent. Merge upstream updates. Continue independently if upstream stops.

### Why Canary Rollout
Changes validated in Nexus Mirror sandboxes before staged rollout (1 → 10% → 100%).

---

## Glossary

| Term | Definition |
|------|------------|
| **Lattice** | Shared graph database (Neo4j + Qdrant) |
| **Butler** | Gateway agent — dumb message relay |
| **Buddy** | UI rendering agent — forked Space-Agent |
| **Clean Room** | Sandbox Mode 1 — empty Python environment |
| **Nexus Mirror** | Sandbox Mode 2 — full Nexus codebase clone |
| **QA Critic** | Quality enforcement system |
| **Canary** | Lightweight smoke test for monitoring |
| **Gap Healing** | Auto-retry for prior failures |
| **Preflight** | Pre-execution cost/time estimation |
| **Pack** | Group of related rules (core, security, etc.) |
| **Scenario** | Multi-step adversarial test |
| **AgentBuilder** | Fluent utility for child agent construction |
| **Self-Extending** | System creates new agents based on usage |
| **Jack's Golden Rule** | "If it's not in the wing, it doesn't exist" |
