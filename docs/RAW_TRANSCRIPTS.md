# Raw Transcripts Archive

This file contains verbatim extracts from forwarded texts about the Athena/The Agency system.
Referenced by: docs/ARCHITECTURE.md, docs/SPECIFICATION.md, docs/SPEC_*.md

---

## 1. Project Description (from forwarded text)

Athena is a decentralized multi-agent system where specialized AI agents collaborate autonomously through a shared graph database (the "Lattice"). Instead of one central boss, agents vote on decisions, score each other's work, and govern themselves. It builds on top of existing agent runtimes (OpenHands, pi-agent-core) but focuses on the coordination layer — governance, memory, and durable execution. Currently building a Finance domain agent as the first proof-of-concept. Think: a self-organizing team of expert AI agents that improve themselves over time.

---

## 2. The Butler (GatewayAgent)

The Butler is the front door. It's a dumb message relay that connects you (via Telegram, Discord, CLI, VS Code, etc.) to the Athena system.

What it does:
1. Receives your messages from whatever interface you're using
2. Routes them to the right agent(s) inside the Lattice
3. Sends back responses through the same interface
4. Handles governance — votes, escalations, and system healing when things break
5. Monitors health — checks CPU, memory, disk, agent count, lattice status

What it does NOT do:
- ❌ No intelligence of its own ("dumb relay")
- ❌ No local state (except config)
- ❌ Doesn't make decisions — agents in the Lattice do that
- ❌ Doesn't store memory — the Lattice does

Analogy: Think of a hotel concierge/butler. You tell them what you want ("I need a finance report"), they don't create it themselves — they go find the right person (agent) in the building, get the answer, and bring it back to you.

Key quote from the code: "Governance-only intelligence (voting, escalation, healing)" — the Butler only coordinates, never thinks independently.

---

## 3. Advantages of the Butler Pattern

1. Separation of Concerns
   - Agents: Pure domain expertise (finance, coding, research)
   - Butler: Interface plumbing + governance
   - Each can evolve independently without coupling

2. Unified Multi-Interface Support
   - One gateway supports Telegram, Discord, CLI, VS Code, Slack simultaneously
   - Add a new interface → just build a new gateway adapter, zero changes to agents
   - All interfaces get the same agent intelligence automatically

3. Centralized Governance & Safety
   - Gatekeeper (security layer) sits in front of everything
   - Rate limiting, auth, risk scoring happen once, not per-agent
   - System-wide healing (restart misbehaving agents, vote to replace them)

4. Observability & Debugging
   - All traffic passes through one point → complete audit trail
   - Can log, monitor, replay, or throttle interactions at the edge
   - Easier to spot bugs, abuse patterns, performance issues

5. Simplified Agent Code
   - Agents don't need to worry about: interface, connection, message formatting, rate limits, auth
   - Agents just focus on their domain task

6. Security Boundary
   - The Butler is the only attack surface exposed to external interfaces
   - Internal agents run in a trusted zone, protected by Gatekeeper
   - Compromise of one interface doesn't automatically compromise agents

7. Graceful Degradation
   - If one interface fails (Telegram outage), others keep working
   - Butler can route around failures, queue messages, or fallback modes
   - System stays available even when parts are down

Trade-off: Single point of failure (mitigated by running Butler redundantly), Extra hop adds ~milliseconds latency (negligible for agent work)

---

## 4. Three Gateway Modes

Mode 1: Butler Only (without Buddy)
- Butler routes messages straight to domain agents (personal, finance, tech, etc.)
- No Buddy UI agent at all
- Simple text-based responses through the same interface
- Use case: headless servers, API-only, lightweight deployments

Mode 2: Butler + Buddy Separate (Recommended)
- Butler handles auth, rate-limiting, interface routing
- Buddy runs as separate Athena node in its own process
- Full graphical UI with tool execution, agent routing, visualization
- Clean separation — each can scale independently

Mode 3: Butler + Buddy Merged (Not Recommended)
- Single process combines both roles
- Faster (no Lattice hop) but tightly coupled
- Can't upgrade/scale independently
- Only for dev/demo/single-container

How Users Select During Setup:
- Option A: Configuration file (gateway_modes.yaml)
- Option B: Environment variable (THE_AGENCY_GATEWAY_MODE=separate)
- Option C: Direct module invocation (python -m theagency.gateways.butler_separate)

---

## 5. Buddy — UI Agent

Buddy is a fork of https://github.com/agent0ai/space-agent — forked and adapted for Athena.
- When upstream Space-Agent updates → Buddy should merge/rebase those updates
- You can add Athena-specific features on top of the fork
- If upstream stops, you continue development independently

Two-Page Mission Control UI:
1. Mission Control (default page) — domain agent summaries, charts, etc.
2. Buddy Page — accessed via floating icon; Buddy renders here

Flow:
1. User on Mission Control page
2. User clicks Buddy floating icon
3. UI switches to Buddy page
4. User asks Buddy something → Butler routes to Buddy node
5. Buddy renders response in Buddy page

How Domain Agents Opt for Buddy:
- Personal_agent → detects "needs fancy chart" → marks response: render_via: "buddy"
- Butler sees render_via flag → routes to Buddy
- Buddy renders interactive chart → sends to Buddy page
- UI switches to Buddy page (or shows Buddy tab)

Buddy Fork Sync Strategy (recommended: Git subtree):
```bash
git remote add upstream https://github.com/agent0ai/space-agent.git
git subtree pull --prefix=theagency/gateways/buddy upstream main --squash
```

Buddy Does NOT auto-update when space agent developers update their domain agents. Buddy is a static UI rendering agent — it receives messages from Butler via Lattice, formats domain agent responses into UI elements, sends UI updates via WebSocket. When a domain agent updates, only the domain agent changes — not Buddy.

---

## 6. Routing Logic (Butler Separate Mode)

```python
def route_message(message, sender):
    # 1. Check if message explicitly targets Buddy
    if message.target == "buddy" or message.command.startswith("/buddy"):
        send_to_buddy_via_lattice(message)
        return
    
    # 2. Check if responding domain agent requested Buddy rendering
    if message.has_rendering_hint and message.render_via == "buddy":
        send_to_buddy_via_lattice(message)
        return
    
    # 3. Default: route to domain agent directly
    send_to_domain_agent(message)
```

---

## 7. Space Agent vs Buddy Architecture Shift

| Space Agent | Buddy (Athena) |
|-------------|----------------|
| Standalone Node.js server | Athena node in Python |
| Direct REST API → UI | Lattice messaging → WebSocket |
| Single agent with tools | Routes to specialized domain agents |
| Own auth/runtime | Uses Athena's Gatekeeper + Coordinator |
| File-based state | Graph database (Lattice) + distributed |

---

## 8. 12 Files in theagency/gateways/

1. launcher.py — Unified entry point — reads config, launches correct mode
2. butler_only.py — Mode 1: Butler routes directly to domain agents
3. butler_separate.py — Mode 2: Butler routes to Buddy via Lattice
4. butler_merged.py — Mode 3: Combined in-process Butler+Buddy
5. _gatekeeper_patch.py — BasicGatekeeper stub (rate limiting, always-allow)
6. adapters/telegram_adapter.py
7. adapters/cli_adapter.py
8. adapters/vscode_adapter.py
9. adapters/discord_adapter.py
10. CONFIG/gateway_modes.yaml — Central configuration
11. README.md — Full documentation
12. IMPLEMENTATION_SUMMARY.md — Implementation details

Plus core stubs:
- CORE/protocols/message.py
- CORE/base_agent/agent.py
- EXTERNAL_COORDINATOR/coordinator.py

---

## 9. Buddy Integration Components

1. BUDDY_INTEGRATION_SPEC.md — 16-section spec (19 KB)
2. Domain Agent Helper: theagency/CORE/helpers/buddy_client.py
   - BuddyClient class with fluent API:
     - request_render(component, data, text, metadata)
     - render_chart(chart_type, chart_data, text)
     - render_table(columns, rows, text)
     - render_card(title, content, subtitle, metadata)
     - render_form(form_id, fields, submit_label, text)
     - render_custom(component_name, data, text)
3. Butler Routing Enhancement (butler_separate.py):
   - route_to_buddy_conversation()
   - route_to_domain_agent()
   - route_response_to_buddy()

---

## 10. Buddy Component Rendering System

Location: /root/projects/buddy/ (forked Space-Agent)

buddy/components/registry.py — ComponentRegistry with fallback handling
buddy/components/renderers.py — 6 built-in renderers:
- chart_bar (vertical/horizontal)
- chart_line (SVG polyline)
- chart_pie (CSS conic-gradient)
- table (HTML table)
- card (title/content/subtitle)
- form (fields, submit)

Updated buddy/node.py:
- handle_message branch for domain_render_request type
- _handle_domain_render() method — renders component and sends buddy_render response to Butler

---

## 11. QA Critic Addon — Detailed

Quality Dimensions: Correctness, Security, Completeness, Consistency, Safety, Performance, Usability, Maintainability, Compliance, Robustness

Enforcement Actions: ALLOW, ALLOW_WITH_WARNING, REQUIRE_APPROVAL, BLOCK, RETRY, ESCALATE, QUARANTINE

Built-in Rules:
- output_not_empty — Ensures agent produces output (HIGH, Completeness)
- no_sensitive_leak — Detects API keys, passwords, tokens (CRITICAL, Security)
- execution_within_limits — Checks time/memory bounds (MEDIUM, Performance)
- error_free_execution — Verifies no unhandled exceptions (HIGH, Robustness)

Scoring: Severity-weighted (critical=4x, high=3x, medium=2x, low=1x, info=0.5x)

Policy Packs:
- strict: fail_on [CRITICAL, HIGH], min_score 0.9
- balanced: fail_on [CRITICAL], min_score 0.7
- permissive: fail_on [CRITICAL], allow_warnings, min_score 0.3

QA Critic Components:
- config.py (220 lines): Types: QAConfig, QARule, ValidationResult, QualityReport
- critic.py (299 lines): QACritic — core review engine, parallel rule execution
- registry.py (225 lines): QARuleRegistry — global rule manager, YAML template loader
- enforcer.py (266 lines): QAEnforcer — enforcement decisions
- cli.py (260 lines): Command-line interface

---

## 12. Research Patterns Integrated into QA Critic

| Source | Pattern | Adoption |
|--------|---------|----------|
| Sparfuchs QA | Multi-layer adapter | AgentAdapter interface |
| Sparfuchs QA | Preflight gating | preflight() method estimates cost/time |
| Sparfuchs QA | Coverage babysitting | Tracks capabilities exercised |
| Sparfuchs QA | Gap healing | Auto-schedules re-checks for prior failures |
| Sparfuchs QA | Canary suite | Lightweight smoke tests for continuous monitoring |
| Sparfuchs QA | Cross-provider audit | Second-opinion LLM check for hallucination detection |
| Cisco Skill Scanner | Pack system | templates/packs/ with YAML manifests |
| Cisco Skill Scanner | Policy knobs | Per-rule enable/disable, threshold overrides |
| Cisco Skill Scanner | Analyzer types | Static, LLM, Behavioral, Meta — separate classes |
| Giskard OSS | Scenario runner | Scenario base class with ScenarioRunner batch execution |
| Giskard OSS | LLM-based checks | Jinja2 templates for prompt-based validation |
| Giskard OSS | Hierarchical scoring | Per-rule → Per-scenario → Overall quality score |

---

## 13. QA Critic CLI Commands

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

## 14. QA Critic Data Models (verbatim)

```python
@dataclass
class QARule:
    id: str
    name: str
    description: str
    severity: Severity
    dimension: QualityDimension
    rule_type: QARuleType
    enabled: bool = True
    config: dict = field(default_factory=dict)
    check_fn: Optional[Callable] = None

@dataclass
class Violation:
    rule_id: str
    severity: Severity
    message: str
    evidence: dict = field(default_factory=dict)
    dimension: QualityDimension = QualityDimension.CORRECTNESS
    timestamp: datetime = field(default_factory=datetime.utcnow)

@dataclass
class QAReview:
    run_id: str
    agent_id: str
    overall_score: float
    quality_level: QualityLevel
    violations: list[Violation]
    enforcement_action: EnforcementAction
    audit_trail: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    duration_ms: float = 0.0
    rules_applied: int = 0
    rules_passed: int = 0
    rules_failed: int = 0
    rules_skipped: int = 0
```

---

## 15. QA Critic Error Handling (Three-Tier)

1. Check-level errors — Rule itself failed → ERROR status, doesn't fail review unless CRITICAL
2. Execution errors — Agent crashed → Auto-BLOCK + ESCALATE
3. System errors — QA system down → Fail-OPEN or fail-CLOSED based on error_policy

Recovery Strategies:
- Retry with exponential backoff for transient failures
- Fallback provider if LLM check fails on primary model
- Graceful degradation: if meta-analyzer unavailable, return rule results without cross-validation

---

## 16. QA Critic Extension Points

```python
@QARule.register("no_ssh_keys")
class NoSSHKeysRule(QARule):
    async def check(self, context) -> Optional[Violation]:
        if "ssh-rsa" in context.get("output", ""):
            return Violation(
                rule_id="no_ssh_keys",
                severity=Severity.CRITICAL,
                message="SSH private key leaked",
            )
        return None
```

---

## 17. Sandbox Addon — Detailed

### Backend Comparison

| Backend | Isolation | Startup | Overhead | Use Case |
|---------|-----------|---------|----------|----------|
| Docker | Strong (namespaces + cgroups) | ~50-200ms | ~5-10MB RAM | Production, untrusted code |
| Process | Medium (RLIMIT) | ~5-20ms | ~2-5MB RAM | Trusted code, fast iteration |
| RestrictedPython | Weak | ~1ms | ~1MB RAM | Development only |

### Why Docker as Primary
1. Strong isolation: Linux namespaces (PID, network, mount, IPC, UTS) + cgroups
2. Filesystem isolation: Read-only root, writable tmpfs, volume mounts
3. Network isolation: Can disable network entirely (--network none)
4. Security: seccomp filters, AppArmor profiles, --cap-drop ALL, --security-opt no-new-privileges
5. Snapshotting: Docker commit for instant state restore
6. Available: Docker daemon already running on system

### Why NOT RestrictedPython
- Dependency missing: Not in pyproject.toml, not installed
- In-process: Buggy code can segfault Python interpreter
- Escape potential: Known escape techniques exist
- No filesystem/resource/network isolation
- Unmaintained: Last release 2018

### Concurrency Model
```python
# Each subject gets their own semaphore
subject_semaphores = {"agent-1": Semaphore(5), ...}

# When limit reached, requests queue
pending_queue = {"agent-1": deque([Future, ...])}
```

### Lifecycle States
```
CREATING → READY → RUNNING → (COMPLETED | ERROR) → DESTROYED
                     ↓
                (PAUSED / SNAPSHOT)
```

### Agent Wrapper Communication Protocol
```
On success:
  stdout: __AGENT_RESULT__\n<repr(result)>\n

On error:
  stdout: __AGENT_ERROR__\n<error_message>\n<traceback>\n
  exit_code: 1
```

### Result Parsing
Uses ast.literal_eval() to parse __AGENT_RESULT__ line — preserves typing (int/float/bool/dict)

### SandboxConfig Fields
```python
@dataclass
class ResourceLimits:
    cpu_percent: float = 50.0
    memory_mb: int = 512
    disk_mb: int = 1024
    max_pids: int = 64
    max_open_files: int = 1024
    runtime_seconds: int = 300

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

---

## 18. Security Model — Docker Backend Guarantees

Docker backend guarantees:
- ✗ Host filesystem access (mount namespace, read-only root)
- ✗ Network access (network namespace = none)
- ✗ Resource exhaustion (cgroups: CPU, memory, disk, PIDs)
- ✗ Privilege escalation (drop capabilities, no-new-privileges)
- ✗ Host process visibility (PID namespace)
- ✗ Device access (no /dev mounts)

Not mitigated:
- ⚠ Docker daemon compromise (if daemon hacked, all sandboxes fail)
- ⚠ Kernel exploits (0-day in namespace implementation)
- ⚠ Side-channel attacks

Mitigations:
- Docker daemon runs rootless where possible
- Minimal base image (python:3.11-slim → consider Alpine for smaller attack surface)
- Regular security updates
- Read-only root filesystem
- Default seccomp/AppArmor profiles

---

## 19. Security Boundaries Table

| Threat | Docker | Process | RestrictedPython |
|--------|--------|---------|-----------------|
| Filesystem access | ✅ Isolated volume | ⚠️ Shared (workspace only) | ❌ Full access |
| Network access | ✅ Blocked by default | ❌ Unrestricted | ❌ Unrestricted |
| CPU exhaustion | ✅ RLIMIT + cgroups | ✅ RLIMIT only | ❌ None |
| Memory exhaustion | ✅ RLIMIT + cgroups | ✅ RLIMIT only | ❌ None |
| Fork bombs | ✅ RLIMIT_NPROC | ✅ RLIMIT_NPROC | ❌ None |
| Import malicious module | ✅ Hook blocks | ⚠️ Can import anything | ⚠️ Can import anything |
| Escape to host | ✅ Very hard | ⚠️ Possible (vulns) | ❌ Trivial |

---

## 20. Sandbox HTTP Service API

```
POST   /sandbox/create           → {"sandbox_id": "..."}
POST   /sandbox/{id}/run         → SandboxResult
POST   /sandbox/{id}/tests       → SandboxResult
POST   /sandbox/{id}/packages    → SandboxResult
POST   /sandbox/{id}/snapshot    → {"snapshot_id": "..."}
POST   /sandbox/{id}/restore     → {"success": true}
GET    /sandbox/{id}/metrics     → SandboxStats
GET    /sandboxes?subject=...    → [{"sandbox_id": "...", ...}]
DELETE /sandbox/{id}             → {"success": true}
GET    /health                   → {"status": "ok"}
```

---

## 21. QA Critic Integration with Sandbox

The sandbox reports metrics (execution_time, peak_memory, exit_code, stderr) to the QA Critic for enforcement decisions.

---

## 22. Scenario Testing (Giskard Pattern)

```python
class Scenario(ABC):
    @abstractmethod
    async def run(self, context: dict) -> ScenarioResult: ...

class JailbreakScenario(Scenario):
    """Tests if agent ignores instructions under prompt injection."""

class PromptInjectionScenario(Scenario):
    """Tests if agent reveals system prompt."""

class DataExfiltrationScenario(Scenario):
    """Tests if agent leaks sensitive data."""

class ToolMisuseScenario(Scenario):
    """Tests if agent misuses available tools."""

class BoundaryScenario(Scenario):
    """Tests edge cases and boundary conditions."""

class ScenarioRunner:
    async def run_suite(self, scenarios, context, parallel=True): ...
```

---

## 23. Canary Suite

```python
class CanarySuite:
    BUILT_IN_CANARIES = [
        "output_not_empty",
        "uses_at_least_one_tool",
        "finishes_within_timeout",
        "no_critical_violations",
    ]
    
    async def run(self, context, canaries=None) -> CanaryResult: ...
    async def watch(self, interval_seconds=60, callback=None) -> None: ...
```

---

## 24. Pack System (Cisco Skill Scanner Pattern)

```yaml
# templates/packs/core/pack.yaml
name: core
description: Always-on rules for basic quality
rules:
  - output_not_empty
  - no_sensitive_leak
  - execution_within_limits
  - error_free_execution

# templates/packs/security/pack.yaml
name: security
description: Security-focused rules
rules:
  - no_sensitive_leak
  - no_sql_injection
  - no_xss_vulnerability

# templates/packs/adversarial/pack.yaml
name: adversarial
description: Adversarial test scenarios
rules:
  - jailbreak_resistance
  - prompt_injection_resistance
  - data_exfiltration_resistance
```

---

## 25. Audit Trail (Sparfuchs Pattern)

```python
@dataclass
class AuditLog:
    run_id: str
    timestamp: datetime
    agent_id: str
    execution_context: dict
    rules_applied: list[str]
    violations: list[Violation]
    enforcement_action: str
    policy_snapshot: dict
    agent_input: str
    agent_output: str
    sandbox_metrics: dict  # exit_code, stdout, stderr, duration_ms, memory_mb
    quality_audit: dict | None  # second-opinion LLM check
    gap_analysis: dict | None  # comparison to prior runs
```

Logging layers:
1. Structured JSON logs (per-rule, per-execution) → findings.jsonl
2. Aggregated report (deduplicated, delta) → findings-final.json, delta.json
3. Human-readable markdown → qa-report.md
4. CI/IDE integration → SARIF format

---

## 26. QA Critic Preflight

```python
def preflight(self, context: dict) -> dict:
    """Compute expected coverage/quality before execution."""
    return {
        "estimated_cost": self._estimate_cost(context),
        "estimated_time": self._estimate_time(context),
        "coverage": self._estimate_coverage(context),
    }
```

---

## 27. QA Critic API

```python
class QACritic:
    _instance: Optional[QACritic] = None
    
    @classmethod
    def get_critic(cls) -> QACritic: ...
    
    async def review(
        self,
        run_id: str,
        agent_id: str,
        context: dict,
        rules: Optional[list[str]] = None,
        parallel: bool = True,
    ) -> QAReview: ...
    
    def register_rule(self, rule: QARule) -> None: ...
    def get_rule(self, rule_id: str) -> Optional[QARule]: ...
    def list_rules(self, dimension=None, severity=None, rule_type=None) -> list[QARule]: ...
    def preflight(self, context: dict) -> dict: ...
    def track_coverage(self, agent_id: str, capabilities_used: set): ...
```

---

## 28. QAEnforcer API

```python
class QAEnforcer:
    def __init__(self, critic: QACritic, policy_pack: str = "balanced"): ...
    
    async def enforce(self, review: QAReview) -> EnforcementDecision: ...
    
    async def enforce_from_context(
        self, run_id: str, agent_id: str, context: dict,
    ) -> EnforcementDecision: ...
    
    def get_decision_history(self, agent_id=None, limit=100) -> list[EnforcementDecision]: ...

@dataclass
class EnforcementDecision:
    run_id: str
    agent_id: str
    action: EnforcementAction
    reason: str
    violations: list[Violation]
    quality_score: float
    quality_level: QualityLevel
    timestamp: datetime = field(default_factory=datetime.utcnow)
    retry_count: int = 0
    retry_after_seconds: Optional[float] = None
```

---

## 29. QARuleRegistry API

```python
class QARuleRegistry:
    _instance: Optional[QARuleRegistry] = None
    
    @classmethod
    def get_registry(cls) -> QARuleRegistry: ...
    
    def register(self, rule_id: str, rule: QARule) -> None: ...
    def get(self, rule_id: str) -> Optional[QARule]: ...
    def list_rules(self, dimension=None, severity=None) -> list[QARule]: ...
    def load_from_yaml(self, path: Path) -> None: ...
    def load_pack(self, pack_name: str) -> None: ...
    def create_rule(self, rule_id, name, description, severity, dimension, check_fn) -> QARule: ...
```

---

## 30. Git Adoption Announcement (from forwarded text)

📢 THE_AGENCY GIT ADOPTION — ACTION REQUIRED
The Agency is now under Git version control at /root/project-theagency/
All code must be committed to the repository. The Forge department (7 agents) is already auto-committing every 15 minutes.

YOUR ACTION:
1. Read the full announcement: cat /root/project-theagency/GIT_ADOPTION_ANNOUNCEMENT.md
2. Add git commit to your build cycle (after each build):
   import subprocess
   from datetime import datetime
   def git_commit():
       subprocess.run(['git','add','-A','theagency/your-section/'],cwd='/root/project-athena')
       subprocess.run(['git','commit','-m',f'Agent YOUR_NAME: update — {datetime.utcnow()}','--no-verify'],cwd='/root/project-athena')
3. Test: create a file, commit, verify with: cd /root/project-athena && git log --oneline -3

REPLY WITH THIS TEMPLATE:
✅ CONFIRMED — Git Adoption
Agent: <your-name>
⏰ Timeline: Immediate / Within 24h / Within 48h
📁 Workspace: <your-current-build-path>
❓ Blockers: <none or description>

Sent by: Architect (Forge Coordination) — 2026-04-30 13:55 UTC

---

## 31. Project Directory Structure (from 141 projects audit)

Total: 141 projects across 17 top-level directories

By Language/Type:
| Type | Count | Examples |
|------|------:|----------|
| NODE | 64 | opencode, goose/ui, cline, Decepticon |
| PYTHON | 38 | hermes-agent, graphiti, redamon, MemoryOS |
| GENERAL | 25 | Docker-based projects, Makefile projects |
| RUST | 13 | goose (crates), opencode/desktop (Tauri) |
| GO | 1 | Decepticon/clients/launcher |

Top-Level Project Directories:
| Directory | Projects | Primary Tech |
|-----------|---------:|--------------|
| /root/hermes-agent | 10 | Python, Node, TUI |
| /root/redamon | 12 | Python, Docker, Pentesting |
| /root/goose | 15 | Rust (crates), Node, Python |
| /root/opencode | 19 | Node (monorepo), Rust, containers |
| /root/cline | 8 | Node (CLI tool) |
| /root/agent-zero | 5 | Python, plugins |
| /root/OpenHands | 5 | Python, Node (AI agent) |
| /root/MemoryOS | 6 | Python (memory systems) |
| /root/graphiti | 6 | Python (graph RAG) |
| /root/Decepticon | 5 | Python, Node, Go (MCP) |
| /root/PentestGPT | 3 | Python (security testing) |
| /root/mempalace | 2 | Python, Node |
| /root/talon | 3 | Node, Python (SIEM) |
| /root/strix | 2 | Python, Docker |
| /root/GMemory | 1 | Python |
| /root/general-agentic-memory | 2 | Python |
| /root/privacy-filter | 1 | Python |

---

## 32. Notable Highlights from Project Audit

- /root/goose — Large Rust monorepo with 8 crates + Node UI + docs
- /root/opencode — Massive Node.js monorepo with 22 packages (console, desktop, web, SDK)
- /root/hermes-agent — The main Hermes agent system with CLI, web UI, TUI, and skills
- /root/redamon — Security scanning suite (recon, trufflehog, gvm, badDNS, MCP)
- /root/cline — Cline AI code assistant (standalone + evals platform)
- /root/OpenHands — OpenHands AI coding agent (Python backend + Node frontend)

---

## 33. Space-Agent (Agent Zero) Details

Type: Node.js browser-first AI agent
Version: 0.36.0
Core idea: A desktop/web AI assistant that lives in your browser and can reshape its own interface, build tools/widgets/workflows on the fly, and execute tasks (file ops, git, code execution, web fetching). CLI-driven (space command) with hot-reload dev server.

Architecture highlights:
- Node.js HTTP server (server/dev_server.js, server/server.js)
- REST API endpoints for filesystem, git, cloud share, etc.
- Electron-compatible desktop packaging (packaging/desktop/)
- Agent module system in app/L0/ with strict AGENTS.md documentation contracts
- Single-user or multi-user mode with auth
- Hot-reload cluster mode (WORKERS>1)

---

## 34. Buddy Details

Type: Python Athena node + WebSocket gateway
Version: 0.1.0
Core idea: Space Agent's UI/UX reimplemented as an Athena node. The "mission control" interface becomes a first-class citizen in the Lattice — it registers with Athena's ExternalCoordinator, routes user requests to domain agents via the Lattice, and broadcasts responses back to the browser via WebSocket.

Key integration points:
- SpaceAgentNode(BaseAgent) — inherits from Athena's BaseAgent
- BuddyAdapter(NodeInterface) — registers Buddy as an Athena node with capabilities: ui_rendering, user_interface, visualization, agent_control
- MessageRouter — routes user queries to domain agents (personal-agent, finance-agent, tech-agent, etc.) via Athena coordinator
- WebSocket server (buddy/backend/websocket_server.py) — connects browser UI to Buddy's inbox/outbox
- Personality as system prompt (personality.md)
- Depends on: websockets, httpx, pydantic, athena (from /root/project-athena)

---

## 35. Known Issues / Blockers (from QA Report)

5 of 7 integrations are failing or incomplete:

| Integration | Grade | Status | Issues |
|-------------|-------|--------|--------|
| Lattice DB | B+ | ✅ Mostly Complete | Minor TODOs |
| Adaptive RAG | C- | ⚠️ Borderline | Insufficient tests, docs missing |
| Safety | C | ⚠️ Partial | Feature flag default true, integration tests insufficient |
| Governance | F | ❌ Failing | Skill empty, not loaded |
| Aether (Durable Execution) | D | ❌ Failing | Not integrated |
| Continuous Evaluation | F | ❌ Failing | Skill empty |
| Observability | F | ❌ Failing | Not integrated |

---

## 36. Consolidation Status

Completed (Phase 1):
- ✅ Created FORGE/ workspace for department coordination
- ✅ Created CONSOLIDATION_PLAN.md (33 tasks)
- ✅ Migrated CORE/ → core/ (30 files moved)
- ✅ Migrated GATEWAYS/ → gateways/ (Buddy integration)
- ✅ Migrated TESTS/ → tests/
- ✅ Built validation script (scripts/verify_consolidation.py)

Pending (Phase 2+):
- ⏳ Delete legacy uppercase directories (CORE/, GATEWAYS/, TESTS/, CONFIG/) — files already migrated, old dirs need removal
- ⏳ Migrate SMS v2.0 nodes (MIG-001–013) — 13 tasks
- ⏳ Migrate Skills as Addons (MIG-014–026) — 13 tasks
- ⏳ Migrate Testing Infrastructure (MIG-027–029) — 3 tasks

---

## 37. Current Work-in-Progress (from audit)

Just Added (uncommitted):
1. OASIS Simulation Adapter — addons/simulation/oasis_adapter.py
   - Wraps camel-ai/oasis social simulation engine
   - Integrates with Athena SMS (Retrieval/Spawn/Lattice)
   - Git-based workspace isolation per agent
   - Time-budget enforcement
   - Next: Needs testing, SMS hooks implementation, documentation

Recently Completed (committed):
1. ✅ Buddy Gateway Integration — full component rendering system
2. ✅ Three-Mode Gateway — butler-only/separate/merged
3. ✅ OASIS as Git Submodule — vendor/oasis/

---

## 38. Key File Locations (from audit)

| File/Dir | Purpose |
|----------|---------|
| `theagency/gateways/launcher.py` | Entry point for gateway system |
| `theagency/gateways/butler_separate.py` | Main Butler (recommended mode) |
| `theagency/gateways/buddy/node.py` | Buddy UI node |
| `theagency/core/orchestrator/orchestrator.py` | Agent coordination |
| `theagency/core/sms/` | Semantic Memory Store (retrieval, secrets, spawn) |
| `theagency/core/safety/` | Safety & guardrails system |
| `theagency/bridges/registry.py` | Bridge registration |
| `addons/simulation/simulation_runner.py` | Simulation engine |
| `vendor/oasis/` | OASIS submodule (camel-ai/oasis) |
| `FORGE/` | Department coordination (pending cleanup) |

---

## 39. Git Summary (from audit)

Branch: main
HEAD: 4272d2b (OASIS submodule added)
Status: Clean (2 untracked files: OASIS adapter)
Uncommitted: addons/simulation/{oasis_adapter.py, tests/test_oasis_adapter.py}

---

## 40. Butler Downsides (10 Critical Issues Identified)

1. Single Point of Failure — Butler crashes → all interfaces die
2. Buddy Hard Dependency — Visual features fail when Buddy offline
3. Routing Ambiguity — No @mention → who handles it?
4. Agent Capability Overlap — Blurry boundaries cause conflicts
5. Sticky Session Problem — Butler is stateless; users expect memory
6. Buddy Conversational Bottleneck — All non-explicit queries go through Buddy first
7. Render-Via "Secret Handshake" — Agents must know about render_via="buddy"
8. No Circuit Breaker Pattern — Butler keeps routing to failing agents
9. Component Registry Centralized — No per-agent customization
10. No Request Prioritization — All messages treated equally

---

## 41. Resilience Fixes Implemented (for Butler Downsides)

1. Circuit Breaker Pattern (theagency/core/monitoring/circuit_breaker.py)
   - Full state machine: CLOSED → OPEN → HALF_OPEN → CLOSED
   - Tracks consecutive failures, error rate, latency, peer scores
   - Auto-opens after threshold, half-open after recovery timeout

2. Agent Health Monitoring (theagency/core/monitoring/agent_health.py)
   - Global monitor tracks all agents
   - Per-agent circuit breakers registry
   - Health score queries for routing decisions

3. Routing Confidence Classifier (theagency/core/routing/classifier.py)
   - Keyword-based domain classification
   - Context-aware (uses recent user queries)
   - Returns confidence scores per domain

4. Response Validator (theagency/core/routing/response_validator.py)
   - Auto-detects structured vs plain text responses
   - Auto-adds render_via="buddy" if missing (configurable)
   - Includes BuddyResponseBuilder fluent API

5. Butler Integration (theagency/gateways/butler_separate.py)
   - Circuit breaker registry in __init__
   - route_to_domain_agent() wrapped with circuit breaker
   - Fast-fail when circuit OPEN (no hanging)
   - Session context tracking per user (last 10 queries)
   - Auto-render detection via _ensure_render_via()
   - Response validation integration
   - Confidence-based routing with fallback agents
   - Clarification generation for ambiguous queries
   - Graceful Buddy unavailability fallback

---

## 42. Circuit Breaker Configuration

```yaml
butler:
  circuit_breaker:
    failure_threshold: 3          # Consecutive failures to open
    recovery_timeout: 30.0        # Seconds before HALF_OPEN
    error_rate_threshold: 0.5     # Error rate (0-1) to open
    minimum_calls: 5              # Min calls before error rate considered
    per_agent:                    # Per-agent overrides
      personal_agent:
        failure_threshold: 5
  routing:
    confidence_threshold: 0.6     # Min confidence to route directly
  health_check_interval: 30.0    # Agent health refresh (seconds)
```

---

## 43. Work Tracking System (Jack's Golden Rule — verbatim)

> **After every git commit and when starting work on something, update the work tracking folders to show:**
> **Work Done** (completed tasks → completed/)
> **Work in Progress** (status updates: in_progress, blocked, review, testing)
> **New Work Created** (add new entries to tasks/ or work/)
>
> *If it's not in the wing, it doesn't exist.*

Directory Structure:
```
~/.theagency/wings/wing_<agent>/
├── tasks/<task_id>.json
├── work/<work_id>.json
├── testing/<test_run_id>.json
├── completed/<year>/<month>/
├── metrics/throughput.jsonl
└── state.json
```

After every git commit:
- [ ] Does commit message reference a task ID? (TASK-123)
- [ ] Update tasks/<id>.json → status = "completed"
- [ ] Set completed_at = now (ISO8601)
- [ ] Add commit hash to task.result.commit
- [ ] If work item done → move to completed/<year>/<month>/
- [ ] Log to metrics/throughput.jsonl
- [ ] Sync to MemPalace

When starting new work:
- [ ] Create tasks/<uuid>.json with status = "in_progress"
- [ ] Set actual_effort.started_at = now
- [ ] Add task_id to work/<work_id>.json.tasks[]
- [ ] Update state.json current_activity

Git Hook (post-commit):
```bash
#!/bin/bash
COMMIT_HASH=$(git rev-parse HEAD)
COMMIT_MSG=$(git log -1 --pretty=%B)
TASK_IDS=$(echo "$COMMIT_MSG" | grep -oE 'TASK-[A-Za-z0-9]+' | cut -d- -f2 | tr '\n' ' ')
for TASK_ID in $TASK_IDS; do
  TASK_FILE="$HOME/.theagency/wings/wing_work/tasks/${TASK_ID}.json"
  [ -f "$TASK_FILE" ] || continue
  jq ".status = \"completed\" |
      .completed_at = \"$(date -Iseconds)\" |
      .result.commit = \"$COMMIT_HASH\"" \
    "$TASK_FILE" > /tmp/task.tmp && mv /tmp/task.tmp "$TASK_FILE"
done
```

---

## 44. First Boot Sequence (what happens when you install Athena)

Minute 1: Discovery
- GatewayAgent starts → Discovers all available agents → Each agent announces capabilities

Minute 2: Registration
- Agents write profiles to Lattice
- Tool permissions set
- Rate limits configured
- Peer scoring baseline established (everyone starts at 50/100)

Minute 3: Ready
- GatewayAgent: "I'm listening on port 8000"
- Butler: "Ready to route your requests"
- System: "All agents healthy, monitoring active"

---

## 45. Routing Decision Flow (when user texts Butler)

Step 1: Parse Message
- Extract keywords, intent, entities

Step 2: Consult Agent Registry (Lattice)
- Query Lattice for agent capabilities

Step 3: Match & Score
- Keyword overlap, semantic similarity, peer score, load, context history

Step 4: Pick Winner
- Highest score wins

Step 5: Delegate & Escalate
- Butler → Lattice → Domain Agent → (optional child agent) → Response

---

## 46. Domain Agent Registration Example

```python
class PersonalAgent(DomainAgent):
    async def _register_capabilities(self):
        await self.lattice.register_agent_profile(
            agent_id="personal",
            domain="personal",
            keywords=["health", "fitness", "habits", "wellness", "personal"],
            child_agents=["personal_doctor", "personal_fitness", "personal_coach"],
            tools=["habit_tracker", "scheduler", "relationship_manager", "life_coordinator"]
        )
```

---

## 47. Routing Flow (complete)

```
User: "Track my workouts"
        ↓
Butler → PersonalAgent (domain match)
        ↓
PersonalAgent → "I need fitness tracking → spawn FitnessCoach child"
        ↓
FitnessCoach → tracks workout, returns insights
        ↓
PersonalAgent aggregates + returns to Butler → User
```

The Butler never sees child agents — only the 8 domain heads.

---

## 48. Summary Table (Routing)

| Your Message | Butler Routes To | Agent Action | Buddy Renders? | You See |
|--------------|-----------------|--------------|----------------|---------|
| `@buddy hello` | Buddy | Buddy LLM chat | No (text only) | Text bubble |
| `@finance show budget` | FinanceAgent | Returns render_via=buddy | Yes | Pie chart |
| `track my workout` | Buddy (default) | Buddy → PersonalAgent → FitnessCoach | Yes | Progress chart |
| `@health my symptoms` | HealthAgent | Returns plain text | No | Text report |
| `/buddy /status` | Buddy | Buddy checks system | Maybe | Dashboard cards |

---

## 49. QA Critic Built-in Rule Implementations (verbatim)

```python
# Rule: output_not_empty
async def check_output_not_empty(context: dict) -> Optional[Violation]:
    output = context.get("output", "")
    if not output or not output.strip():
        return Violation(
            rule_id="output_not_empty",
            severity=Severity.HIGH,
            message="Agent produced empty output",
            dimension=QualityDimension.COMPLETENESS,
        )
    return None

# Rule: no_sensitive_leak
SENSITIVE_PATTERNS = [
    r'(api[_\s]?key|apikey)\s*[=:]\s*["\'][\w\-]+["\']',
    r'(password|passwd|pwd)\s*[=:]\s*["\'][^"\']+["\']',
    r'(secret[_\s]?key|secretkey)\s*[=:]\s*["\'][\w\-]+["\']',
    r'(token|access[_\s]?token)\s*[=:]\s*["\'][\w\-]+["\']',
    r'ssh-rsa\s+AAAA[^\s]+',
    r'-----BEGIN\s+(RSA|DSA|EC|OPENSSH)\s+PRIVATE\s+KEY-----',
]

async def check_no_sensitive_leak(context: dict) -> Optional[Violation]:
    output = context.get("output", "")
    for pattern in SENSITIVE_PATTERNS:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            return Violation(
                rule_id="no_sensitive_leak",
                severity=Severity.CRITICAL,
                message=f"Sensitive data leaked: {match.group()[:20]}...",
                evidence={"pattern": pattern, "match": match.group()},
                dimension=QualityDimension.SECURITY,
            )
    return None

# Rule: execution_within_limits
async def check_execution_within_limits(context: dict) -> Optional[Violation]:
    metrics = context.get("metrics", {})
    max_time = context.get("max_time_seconds", 300)
    max_memory = context.get("max_memory_mb", 512)
    actual_time = metrics.get("execution_time_seconds", 0)
    actual_memory = metrics.get("peak_memory_mb", 0)
    if actual_time > max_time:
        return Violation(...)
    if actual_memory > max_memory:
        return Violation(...)
    return None

# Rule: error_free_execution
async def check_error_free_execution(context: dict) -> Optional[Violation]:
    metrics = context.get("metrics", {})
    exit_code = metrics.get("exit_code", 0)
    stderr = metrics.get("stderr", "")
    if exit_code != 0:
        return Violation(
            rule_id="error_free_execution",
            severity=Severity.HIGH,
            message=f"Execution failed with exit code {exit_code}",
            dimension=QualityDimension.ROBUSTNESS,
        )
    return None
```

---

## 50. QA Critic Scoring Algorithm (verbatim)

```python
SEVERITY_WEIGHTS = {
    Severity.CRITICAL: 4.0,
    Severity.HIGH: 3.0,
    Severity.MEDIUM: 2.0,
    Severity.LOW: 1.0,
    Severity.INFO: 0.5,
}

QUALITY_THRESHOLDS = {
    QualityLevel.EXCELLENT: 0.9,
    QualityLevel.GOOD: 0.7,
    QualityLevel.FAIR: 0.5,
    QualityLevel.POOR: 0.3,
    QualityLevel.FAILING: 0.0,
}

def compute_quality_score(total_rules: int, violations: list[Violation]) -> tuple[float, QualityLevel]:
    if total_rules == 0:
        return 1.0, QualityLevel.EXCELLENT
    total_weight = sum(SEVERITY_WEIGHTS[v.severity] for v in violations)
    max_possible = total_rules * SEVERITY_WEIGHTS[Severity.CRITICAL]
    score = max(0.0, 1.0 - (total_weight / max_possible))
    for level, threshold in QUALITY_THRESHOLDS.items():
        if score >= threshold:
            return score, level
    return score, QualityLevel.FAILING
```

---

*End of raw transcripts archive. This file is the source of truth for all forwarded texts.*
*Last updated: 2026-09-15 by Hermes Agent*
