# Nexus Adversarial/QA Critic Addon — Detailed Specification

## 1. Overview

The **QA Critic** addon is a **quality enforcement system** (NOT red teaming) that simultaneously reviews every agent's work, finds loopholes, and enforces strict quality standards. It ensures quality even in full autonomy.

**Based on research from:**
- Sparfuchs QA — Multi-layer adapter, preflight gating, coverage babysitting
- Cisco Skill Scanner — Pack-based composition, policy knobs
- Giskard OSS — Scenario-based testing, LLM-based checks

---

## 2. Quality Dimensions (10)

```python
class QualityDimension(Enum):
    CORRECTNESS = "correctness"          # Logic errors, wrong answers
    SECURITY = "security"                # API keys, passwords, injection
    COMPLETENESS = "completeness"        # Missing steps, incomplete analysis
    CONSISTENCY = "consistency"          # Contradictions in reasoning
    SAFETY = "safety"                    # Harmful outputs, dangerous code
    PERFORMANCE = "performance"          # Inefficient algorithms, timeouts
    USABILITY = "usability"              # Hard-to-read output, poor formatting
    MAINTAINABILITY = "maintainability"  # Spaghetti code, no comments
    COMPLIANCE = "compliance"            # Policy violations, license issues
    ROBUSTNESS = "robustness"            # Edge cases, error handling
```

---

## 3. Scoring System

### 3.1 Severity Weights
```python
SEVERITY_WEIGHTS = {
    Severity.CRITICAL: 4.0,
    Severity.HIGH: 3.0,
    Severity.MEDIUM: 2.0,
    Severity.LOW: 1.0,
    Severity.INFO: 0.5,
}
```

### 3.2 Quality Thresholds
```python
QUALITY_THRESHOLDS = {
    QualityLevel.EXCELLENT: 0.9,
    QualityLevel.GOOD: 0.7,
    QualityLevel.FAIR: 0.5,
    QualityLevel.POOR: 0.3,
    QualityLevel.FAILING: 0.0,
}
```

### 3.3 Score Computation
```python
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

## 4. Enforcement Actions

```python
class EnforcementAction(Enum):
    ALLOW = "allow"                          # Continue normally
    ALLOW_WITH_WARNING = "allow_with_warning" # Continue but note issue
    REQUIRE_APPROVAL = "require_approval"     # Pause for human review
    BLOCK = "block"                          # Stop and discard output
    RETRY = "retry"                          # Re-run with instructions to fix
    ESCALATE = "escalate"                    # Escalate to higher authority
    QUARANTINE = "quarantine"                # Isolate agent, something wrong
```

### 4.1 Policy Packs
```yaml
# config/qa_policies.yaml
policies:
  strict:
    fail_on: [CRITICAL, HIGH]
    require_approval_on: [MEDIUM]
    retry_on_timeout: true
    max_retries: 2
    min_coverage: 0.8
    
  balanced:  # default
    fail_on: [CRITICAL]
    require_approval_on: []
    retry_on_timeout: true
    max_retries: 2
    min_coverage: 0.5
    
  permissive:
    fail_on: [CRITICAL]
    allow_warnings: true
    retry_on_timeout: true
    max_retries: 1
    min_coverage: 0.3
```

---

## 5. Built-in Rules

### 5.1 output_not_empty (HIGH, Completeness)
```python
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
```

### 5.2 no_sensitive_leak (CRITICAL, Security)
```python
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
```

### 5.3 execution_within_limits (MEDIUM, Performance)
```python
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
```

### 5.4 error_free_execution (HIGH, Robustness)
```python
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

## 6. QACritic — Core Engine

### 6.1 Singleton Pattern
```python
class QACritic:
    _instance: Optional[QACritic] = None
    
    @classmethod
    def get_critic(cls) -> QACritic:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
```

### 6.2 Review Methods
```python
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
```

### 6.3 Preflight (Before Expensive Work)
```python
def preflight(self, context: dict) -> dict:
    """Compute expected coverage/quality before execution."""
    return {
        "estimated_cost": self._estimate_cost(context),
        "estimated_time": self._estimate_time(context),
        "coverage": self._estimate_coverage(context),
    }
```

### 6.4 Coverage Tracking
```python
def track_coverage(self, agent_id: str, capabilities_used: set):
    """Track which agent capabilities were actually exercised."""
    self._coverage_tracker[agent_id].update(capabilities_used)
```

---

## 7. QAEnforcer — Decision Engine

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
    
    async def enforce_from_context(
        self, run_id: str, agent_id: str, context: dict,
    ) -> EnforcementDecision:
        """Convenience: review + enforce in one call."""
        review = await self._critic.review(run_id, agent_id, context)
        return await self.enforce(review)
```

---

## 8. Scenario Testing

```python
class Scenario(ABC):
    @abstractmethod
    async def run(self, context: dict) -> ScenarioResult: ...

class JailbreakScenario(Scenario):
    """Tests if agent ignores instructions under prompt injection."""
    async def run(self, context: dict) -> ScenarioResult:
        # Step 1: Normal request
        # Step 2: Injection attempt
        # Step 3: Check compliance
        pass

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

---

## 9. Canary Suite

```python
class CanarySuite:
    """Lightweight smoke tests for continuous monitoring."""
    
    BUILT_IN_CANARIES = [
        "output_not_empty",
        "uses_at_least_one_tool",
        "finishes_within_timeout",
        "no_critical_violations",
    ]
    
    async def run(
        self,
        context: dict,
        canaries: Optional[list[str]] = None,
    ) -> CanaryResult: ...
    
    async def watch(
        self,
        interval_seconds: float = 60.0,
        callback: Optional[Callable] = None,
    ) -> None: ...  # Continuous monitoring
```

---

## 10. Registry — Rule Management

```python
class QARuleRegistry:
    _instance: Optional[QARuleRegistry] = None
    
    @classmethod
    def get_registry(cls) -> QARuleRegistry: ...
    
    def register(self, rule_id: str, rule: QARule) -> None: ...
    def get(self, rule_id: str) -> Optional[QARule]: ...
    def list_rules(
        self,
        dimension: Optional[QualityDimension] = None,
        severity: Optional[Severity] = None,
    ) -> list[QARule]: ...
    
    def load_from_yaml(self, path: Path) -> None: ...
    def load_pack(self, pack_name: str) -> None: ...
    
    def create_rule(
        self,
        rule_id: str,
        name: str,
        description: str,
        severity: Severity,
        dimension: QualityDimension,
        check_fn: Callable,
    ) -> QARule: ...
```

---

## 11. Pack System

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

## 12. CLI Commands

```bash
# Review agent output
nexus qa review --agent <id> --policy strict

# List all rules
nexus qa rules list --dimension security --severity critical

# Run scenarios
nexus qa scenarios list
nexus qa scenarios run --suite jailbreak,prompt_injection

# Run canary suite
nexus qa canary run
nexus qa canary watch --interval 30

# Enforce decisions
nexus qa enforce --agent <id> --run-id <run_id>

# View audit trail
nexus qa audit show <run_id>

# Gap healing
nexus qa gaps heal --agent <id>

# Policy management
nexus qa policy export > policy.yaml
nexus qa policy import policy.yaml
```

---

## 13. Data Models

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

## 14. Error Handling

### 14.1 Three-Tier Model
1. **Check-level errors** — Rule itself failed → ERROR status, doesn't fail review unless CRITICAL
2. **Execution errors** — Agent crashed → Auto-BLOCK + ESCALATE
3. **System errors** — QA system down → Fail-OPEN or fail-CLOSED based on `error_policy`

### 14.2 Recovery Strategies
- Retry with exponential backoff for transient failures
- Fallback provider if LLM check fails on primary model
- Graceful degradation: if meta-analyzer unavailable, return rule results without cross-validation

---

## 15. Extension Points

### 15.1 Custom Rule Registration
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

### 15.2 Custom Scenarios
```python
class MyCustomScenario(Scenario):
    async def run(self, context) -> ScenarioResult: ...
```

### 15.3 Custom Policy Packs
```yaml
# my_policy.yaml
name: finance_strict
fail_on: [CRITICAL, HIGH]
min_score: 0.95
require_approval_on: [MEDIUM]
```

---

## 16. Research Patterns Integrated

| Source | Pattern | Adoption |
|--------|---------|----------|
| Sparfuchs QA | Multi-layer adapter | `AgentAdapter` interface for agent-agnostic review |
| Sparfuchs QA | Preflight gating | `preflight()` method estimates cost/time before execution |
| Sparfuchs QA | Coverage babysitting | Tracks which capabilities were exercised |
| Sparfuchs QA | Gap healing | Auto-schedules re-checks for prior failures |
| Sparfuchs QA | Canary suite | Lightweight smoke tests for continuous monitoring |
| Sparfuchs QA | Cross-provider audit | Second-opinion LLM check for hallucination detection |
| Cisco Skill Scanner | Pack system | `templates/packs/` with YAML manifests |
| Cisco Skill Scanner | Policy knobs | Per-rule enable/disable, threshold overrides |
| Cisco Skill Scanner | Analyzer types | Static, LLM, Behavioral, Meta — separate classes |
| Giskard OSS | Scenario runner | `Scenario` base class with `ScenarioRunner` batch execution |
| Giskard OSS | LLM-based checks | Jinja2 templates for prompt-based validation |
| Giskard OSS | Hierarchical scoring | Per-rule → Per-scenario → Overall quality score |
