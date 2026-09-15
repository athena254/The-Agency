# The Agency — Full Specification

## 1. Gateway/Butler Specification

### 1.1 Three Gateway Modes Configuration

```yaml
# config/gateway_modes.yaml
mode: "separate"  # "butler-only" | "separate" | "merged"

modes:
  butler-only:
    description: "Butler routes directly to domain agents, no Buddy UI"
    entry: "python -m theagency.gateways.butler_only"
    
  separate:
    description: "Butler routes to Buddy via Lattice + to domain agents"
    entry: "python -m theagency.gateways.butler_separate"
    buddy_entry: "python -m theagency.gateways.buddy.node"
    
  merged:
    description: "Single process handles both gateway routing and UI"
    entry: "python -m theagency.gateways.butter_merged"
```

### 1.2 Butler Routing Decision Tree

```python
async def route_message(message: Message, sender: str) -> None:
    # 1. Explicit Buddy targeting
    if message.target == "buddy" or message.command.startswith("/buddy"):
        await route_to_buddy_conversation(message)
        return
    
    # 2. Agent mention
    if message.target and message.target.startswith("@"):
        await route_to_domain_agent(message)
        return
    
    # 3. Default: route to Buddy conversation
    await route_to_buddy_conversation(message)
```

### 1.3 Butler Routing Decision Tree (Separate Mode)

```python
async def route_message(message: Message, sender: str) -> None:
    # 1. Explicit Buddy targeting
    if message.target == "buddy" or message.command.startswith("/buddy"):
        await route_to_buddy_conversation(message)
        return
    
    # 2. Agent mention
    if message.target and message.target.startswith("@"):
        await route_to_domain_agent(message)
        return
    
    # 3. Domain agent render hint (response)
    if message.has_rendering_hint and message.render_via == "buddy":
        await route_response_to_buddy(message)
        return
    
    # 4. Default: route to Buddy conversation
    await route_to_buddy_conversation(message)
```

### 1.4 Buddy Integration Protocol

Domain agents request Buddy rendering via response format:
```python
{
    "text": "Human-readable summary",
    "render_via": "buddy",
    "component": "chart_bar|chart_line|chart_pie|table|card|form|custom",
    "data": {...},
    "metadata": {"title": "...", "description": "..."}
}
```

### 1.5 Resilience Features

| Feature | Implementation |
|---------|----------------|
| Circuit Breaker | CircuitBreaker class with CLOSED/OPEN/HALF_OPEN states |
| Health Monitoring | AgentHealthMonitor with per-agent health scores (0-1) |
| Confidence Classifier | RoutingConfidenceClassifier with keyword-based domain detection |
| Response Validator | Auto-detects render_via flag |
| Buddy Fallback | Falls back to Mission Control when Buddy offline |
| Session Context | Tracks last 10 queries per user |
| Retry Logic | 2 retries with exponential backoff (1s, 2s) |
| Clarification | Asks user when confidence < threshold |

### 1.6 Circuit Breaker Configuration

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

### 1.7 Gateway Launcher

```python
class GatewayLauncher:
    """Unified entry point — reads config, launches correct mode."""
    
    def __init__(self, config_path: str = "config/gateway_modes.yaml"):
        self._config = self._load_config(config_path)
        self._mode = self._config.get("mode", "separate")
    
    def launch(self) -> None:
        if self._mode == "butler-only":
            from .butler_only import ButlerOnly
            gateway = ButlerOnly(self._config)
        elif self._mode == "separate":
            from .butler_separate import ButlerSeparate
            gateway = ButlerSeparate(self._config)
        elif self._mode == "merged":
            from .butler_merged import ButlerMerged
            gateway = ButlerMerged(self._config)
        asyncio.run(gateway.start())
```

---

## 2. Sandbox Specification

### 2.1 Configuration Data Models

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

@dataclass
class SandboxConfig:
    subject: str
    backend: SandboxBackend = SandboxBackend.DOCKER
    sandbox_type: SandboxType = SandboxType.CLEAN_ROOM
    network_access: NetworkAccess = NetworkAccess.NONE
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    allowed_imports: list[str] = field(default_factory=lambda: [
        "math", "json", "os", "sys", "datetime", "collections",
        "itertools", "functools", "statistics", "random", "string",
        "re", "hashlib", "copy", "pprint", "textwrap", "dataclasses",
        "typing", "enum", "abc", "io", "pathlib", "uuid", "time"
    ])
    env_vars: dict[str, str] = field(default_factory=dict)
    workspace_path: Optional[Path] = None
    athena_mirror: AthenaMirrorConfig = field(default_factory=AthenaMirrorConfig)
    preserve_workspace: bool = False
    tags: dict[str, str] = field(default_factory=dict)
```

### 2.2 Public API

```python
class SandboxAPI:
    async def start(self) -> None
    async def stop(self) -> None
    
    async def create(
        self,
        subject: str,
        backend: Union[str, SandboxBackend] = "docker",
        memory_mb: int = 512,
        runtime_seconds: int = 300,
        network_access: Union[str, NetworkAccess] = "none",
        allowed_imports: Optional[list[str]] = None,
        env_vars: Optional[dict[str, str]] = None,
        athena_mirror: Optional[AthenaMirrorConfig] = None,
        tags: Optional[dict[str, str]] = None,
    ) -> str  # Returns sandbox_id
    
    async def run_code(self, sandbox_id: str, code: str, timeout: Optional[int] = None) -> SandboxResult
    async def run_tests(self, sandbox_id: str, test_path: str = "tests/", timeout: int = 120) -> SandboxResult
    async def install_packages(self, sandbox_id: str, packages: list[str]) -> SandboxResult
    async def copy_to_sandbox(self, sandbox_id: str, host_path: Union[str, Path], sandbox_path: str) -> None
    async def copy_from_sandbox(self, sandbox_id: str, sandbox_path: str, host_path: Union[str, Path]) -> None
    async def snapshot(self, sandbox_id: str, label: str = "") -> str
    async def restore(self, sandbox_id: str, snapshot_id: str) -> None
    async def destroy(self, sandbox_id: str) -> bool
    async def get_metrics(self, sandbox_id: str) -> SandboxStats
    async def list_sandboxes(self, subject: Optional[str] = None) -> list[dict]
    async def get_stats(self) -> dict
```

### 2.3 Result Models

```python
@dataclass
class SandboxResult:
    success: bool
    result: Any = None
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    duration_ms: float = 0.0
    memory_peak_mb: float = 0.0
    error_message: Optional[str] = None

@dataclass
class SandboxStats:
    cpu_usage_percent: float = 0.0
    memory_rss_mb: float = 0.0
    thread_count: int = 0
    open_files: int = 0
    disk_used_mb: float = 0.0
    uptime_seconds: float = 0.0
```

### 2.4 HTTP Service API

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

### 2.5 Agent Wrapper Template

```python
#!/usr/bin/env python3
"""Sandbox wrapper — DO NOT EDIT"""

import sys
import resource
import builtins
import importlib
import traceback
from pathlib import Path

# 1. Set resource limits
resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, -1))
resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))

# 2. Install import hook
ALLOWED_IMPORTS = ['math', 'json', 'os', 'sys', 'datetime', ...]
_original_import = builtins.__import__
def safe_import(name, *args, **kwargs):
    if name.split('.')[0] not in ALLOWED_IMPORTS:
        raise ImportError(f"Import of '{name}' is not allowed")
    return _original_import(name, *args, **kwargs)
builtins.__import__ = safe_import

# 3. Execute user code
code_path = Path(__file__).parent / "agent_code.py"
agent_globals = {"__builtins__": builtins.__dict__}
try:
    exec(compile(code_path.read_text(), str(code_path), 'exec'), agent_globals)
    result = agent_globals.get("result")
    print(f"__AGENT_RESULT__\n{repr(result)}")
except Exception as e:
    print(f"__AGENT_ERROR__{e}\n{traceback.format_exc()}")
    sys.exit(1)
```

---

## 3. QA Critic Specification

### 3.1 Configuration Data Models

```python
class QualityDimension(Enum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    COMPLETENESS = "completeness"
    CONSISTENCY = "consistency"
    SAFETY = "safety"
    PERFORMANCE = "performance"
    USABILITY = "usability"
    MAINTAINABILITY = "maintainability"
    COMPLIANCE = "compliance"
    ROBUSTNESS = "robustness"

class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

class QualityLevel(Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    FAILING = "failing"

class EnforcementAction(Enum):
    ALLOW = "allow"
    ALLOW_WITH_WARNING = "allow_with_warning"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"
    RETRY = "retry"
    ESCALATE = "escalate"
    QUARANTINE = "quarantine"

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

@dataclass
class Violation:
    rule_id: str
    severity: Severity
    message: str
    evidence: dict = field(default_factory=dict)
    dimension: QualityDimension = QualityDimension.CORRECTNESS

@dataclass
class QAReview:
    run_id: str
    agent_id: str
    overall_score: float  # 0-1
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

### 3.2 QACritic API

```python
class QACritic:
    _instance: Optional[QACritic] = None
    
    @classmethod
    def get_critic(cls) -> QACritic:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def review(
        self,
        run_id: str,
        agent_id: str,
        context: dict,
        rules: Optional[list[str]] = None,
        parallel: bool = True,
    ) -> QAReview:
        """Run all enabled rules in parallel via asyncio.gather."""
        enabled_rules = self._get_enabled_rules(rules)
        
        if parallel:
            results = await asyncio.gather(
                *[rule.check(context) for rule in enabled_rules],
                return_exceptions=True,
            )
        else:
            results = [await rule.check(context) for rule in enabled_rules]
        
        violations = [r for r in results if isinstance(r, Violation)]
        score, level = compute_quality_score(len(enabled_rules), violations)
        
        return QAReview(
            run_id=run_id,
            agent_id=agent_id,
            overall_score=score,
            quality_level=level,
            violations=violations,
            enforcement_action=self._determine_action(violations, score),
        )
    
    def preflight(self, context: dict) -> dict:
        """Compute expected coverage/quality before execution."""
        return {
            "estimated_cost": self._estimate_cost(context),
            "estimated_time": self._estimate_time(context),
            "coverage": self._estimate_coverage(context),
        }
```

### 3.3 QAEnforcer API

```python
class QAEnforcer:
    def __init__(self, critic: QACritic, policy_pack: str = "balanced"):
        self._critic = critic
        self._policy = self._load_policy(policy_pack)
    
    async def enforce(self, review: QAReview) -> EnforcementDecision:
        """Map violations to enforcement action using policy."""
        violations = review.violations
        
        # Check critical violations
        if any(v.severity == Severity.CRITICAL for v in violations):
            return EnforcementDecision(
                action=EnforcementAction.BLOCK,
                reason="Critical violation detected",
            )
        
        # Check high violations (strict policy)
        if self._policy.fail_on_high and any(v.severity == Severity.HIGH for v in violations):
            return EnforcementDecision(action=EnforcementAction.BLOCK, ...)
        
        # Check score threshold
        if review.overall_score < self._policy.min_score:
            return EnforcementDecision(action=EnforcementAction.RETRY, ...)
        
        # Check for warnings
        if violations:
            return EnforcementDecision(action=EnforcementAction.ALLOW_WITH_WARNING, ...)
        
        return EnforcementDecision(action=EnforcementAction.ALLOW, ...)
```

### 3.4 Built-in Rule Implementations

```python
# Rule: output_not_empty (HIGH, Completeness)
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

# Rule: no_sensitive_leak (CRITICAL, Security)
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

# Rule: execution_within_limits (MEDIUM, Performance)
async def check_execution_within_limits(context: dict) -> Optional[Violation]:
    metrics = context.get("metrics", {})
    max_time = context.get("max_time_seconds", 300)
    max_memory = context.get("max_memory_mb", 512)
    actual_time = metrics.get("execution_time_seconds", 0)
    actual_memory = metrics.get("peak_memory_mb", 0)
    
    if actual_time > max_time:
        return Violation(
            rule_id="execution_within_limits",
            severity=Severity.MEDIUM,
            message=f"Execution exceeded time limit: {actual_time:.1f}s > {max_time}s",
            evidence={"actual_time": actual_time, "limit": max_time},
            dimension=QualityDimension.PERFORMANCE,
        )
    if actual_memory > max_memory:
        return Violation(
            rule_id="execution_within_limits",
            severity=Severity.MEDIUM,
            message=f"Execution exceeded memory limit: {actual_memory:.1f}MB > {max_memory}MB",
            evidence={"actual_memory": actual_memory, "limit": max_memory},
            dimension=QualityDimension.PERFORMANCE,
        )
    return None

# Rule: error_free_execution (HIGH, Robustness)
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

### 3.5 Scoring Algorithm

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

### 3.6 Scenario Testing

```python
class Scenario(ABC):
    @abstractmethod
    async def run(self, context: dict) -> ScenarioResult: ...

class JailbreakScenario(Scenario):
    """Tests if agent ignores instructions under prompt injection."""
    async def run(self, context: dict) -> ScenarioResult: ...

class PromptInjectionScenario(Scenario):
    """Tests if agent reveals system prompt."""
    async def run(self, context: dict) -> ScenarioResult: ...

class DataExfiltrationScenario(Scenario):
    """Tests if agent leaks sensitive context data."""
    async def run(self, context: dict) -> ScenarioResult: ...

class ToolMisuseScenario(Scenario):
    """Tests if agent misuses available tools."""
    async def run(self, context: dict) -> ScenarioResult: ...

class BoundaryScenario(Scenario):
    """Tests edge cases and boundary conditions."""
    async def run(self, context: dict) -> ScenarioResult: ...

class ScenarioRunner:
    async def run_suite(
        self,
        scenarios: list[Scenario],
        context: dict,
        parallel: bool = True,
    ) -> list[ScenarioResult]: ...
```

### 3.7 Canary Suite

```python
class CanarySuite:
    """Lightweight smoke tests for continuous monitoring."""
    
    BUILT_IN_CANARIES = [
        "output_not_empty",
        "uses_at_least_one_tool",
        "finishes_within_timeout",
        "no_critical_violations",
    ]
    
    async def run(self, context: dict, canaries: Optional[list[str]] = None) -> CanaryResult: ...
    async def watch(self, interval_seconds: float = 60.0, callback: Optional[Callable] = None) -> None: ...
```

### 3.8 CLI Commands

```bash
# Review agent output
theagency qa review --agent <id> --policy strict

# List all rules
theagency qa rules list --dimension security --severity critical

# Run scenarios
theagency qa scenarios list
theagency qa scenarios run --suite jailbreak,prompt_injection

# Run canary suite
theagency qa canary run
theagency qa canary watch --interval 30

# Enforce decisions
theagency qa enforce --agent <id> --run-id <run_id>

# View audit trail
theagency qa audit show <run_id>

# Gap healing
theagency qa gaps heal --agent <id>

# Policy management
theagency qa policy export > policy.yaml
theagency qa policy import policy.yaml
```

### 3.9 Pack System

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

## 4. Integration Contracts

### 4.1 Sandbox Integration with QA Critic
The sandbox reports metrics (execution_time, peak_memory, exit_code, stderr) to the QA Critic for enforcement decisions.

### 4.2 Gateway Integration with Sandbox
Domain agents request sandboxes via the Butler. The Butler routes sandbox creation requests through the Lattice.

### 4.3 Gateway Integration with QA Critic
Every agent execution is reviewed by the QA Critic before output reaches the user. BLOCK decisions trigger re-routing or regeneration.

---

## 5. Error Handling Strategy

### 5.1 Three-Tier Error Model
1. **Check-level errors** — Rule itself failed → ERROR status, doesn't fail review unless CRITICAL
2. **Execution errors** — Agent crashed → Auto-BLOCK + ESCALATE
3. **System errors** — QA system down → Fail-OPEN or fail-CLOSED based on `error_policy`

### 5.2 Recovery Strategies
- Retry with exponential backoff for transient failures (429, 5xx)
- Fallback provider if LLM check fails on primary model
- Graceful degradation: if meta-analyzer unavailable, return rule results without cross-validation

---

## 6. Monitoring & Observability

### 6.1 Sandbox Metrics
- CPU usage (%), Memory (RSS MB), Uptime (seconds)
- Thread count, Open files, Disk used (MB)
- Exit code, Duration (ms), Error messages

### 6.2 QA Critic Metrics
- Quality scores over time, per agent, per dimension
- Violation counts by severity
- Enforcement action distribution
- Rule pass/fail rates

### 6.3 Audit Trail
Every review writes a complete record:
- Run metadata (agent, policy, config)
- Raw violations (stream)
- Deduplicated findings
- Delta from prior run
- Coverage report
- Quality audit (second opinion)
- Pre-flight expectations vs reality

---

## 7. Quality Gates

### 7.1 Build Phase
- All code must pass sandbox Clean Room tests before commit
- All rules must pass canary suite

### 7.2 Pre-Deployment
- All code must pass The Agency Mirror (Mode 2) integration tests
- QA Critic score ≥ 0.9 for production deployments
- No CRITICAL or HIGH violations

### 7.3 Runtime
- Every agent execution reviewed by QA Critic
- Canary suite runs continuously
- Violations logged to audit trail
- Trend analysis for quality regression detection

---

*End of specification. For raw source material see docs/RAW_TRANSCRIPTS.md*
