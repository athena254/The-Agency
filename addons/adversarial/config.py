"""config.py - Core dataclasses and enums for the QA Critic system."""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4


class QualityDimension(enum.Enum):
    """Dimensions along which agent output can be evaluated."""
    CORRECTNESS = "correctness"
    COMPLETENESS = "completeness"
    SAFETY = "safety"
    RELIABILITY = "reliability"
    EFFICIENCY = "efficiency"
    USABILITY = "usability"
    MAINTAINABILITY = "maintainability"
    COMPLIANCE = "compliance"


class Severity(enum.Enum):
    """Severity level of a violation."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class QualityLevel(enum.Enum):
    """Overall quality level assigned by the critic."""
    EXCELLENT = "excellent"
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    POOR = "poor"
    UNACCEPTABLE = "unacceptable"


class EnforcementAction(enum.Enum):
    """Action the enforcer takes on a violation."""
    BLOCK = "block"
    RETRY = "retry"
    WARN = "warn"
    LOG = "log"
    ESCALATE = "escalate"
    NOOP = "noop"


class QARuleType(enum.Enum):
    """Classification of QA rule checks."""
    OUTPUT = "output"
    EXECUTION = "execution"
    SECURITY = "security"
    COMPLIANCE = "compliance"
    PERFORMANCE = "performance"
    CUSTOM = "custom"


class PolicyPack(enum.Enum):
    """Predefined enforcement policy packs."""
    STRICT = "strict"
    BALANCED = "balanced"
    PERMISSIVE = "permissive"


@dataclass(frozen=True)
class QARule:
    """A single quality rule that the critic can evaluate."""
    name: str
    description: str
    rule_type: QARuleType
    dimension: QualityDimension
    severity: Severity = Severity.MEDIUM
    weight: float = 1.0
    enabled: bool = True
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.weight <= 10.0:
            raise ValueError(f"Weight must be in [0, 10], got {self.weight}")


@dataclass
class Violation:
    """Represents a single quality rule violation."""
    rule: QARule
    message: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    context: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule_name": self.rule.name,
            "rule_type": self.rule.rule_type.value,
            "dimension": self.rule.dimension.value,
            "severity": self.rule.severity.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "context": self.context,
        }


@dataclass
class QAReview:
    """Result of a single review pass."""
    target_id: str
    target_name: str
    violations: list[Violation] = field(default_factory=list)
    score: float = 1.0
    quality_level: QualityLevel = QualityLevel.EXCELLENT
    duration_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)
    healed_gaps: list[str] = field(default_factory=list)

    @property
    def has_critical(self) -> bool:
        return any(
            v.rule.severity == Severity.CRITICAL for v in self.violations
        )

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "target_name": self.target_name,
            "violations": [v.to_dict() for v in self.violations],
            "score": self.score,
            "quality_level": self.quality_level.value,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "healed_gaps": self.healed_gaps,
        }


@dataclass
class QualityReport:
    """Aggregated report from multiple reviews."""
    reviews: list[QAReview] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.utcnow)
    summary: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.summary:
            self._compute_summary()

    def _compute_summary(self) -> None:
        total = len(self.reviews)
        if total == 0:
            self.summary = {
                "total_reviews": 0,
                "total_violations": 0,
                "average_score": 0.0,
                "quality_distribution": {},
                "dimension_scores": {},
            }
            return

        total_violations = sum(len(r.violations) for r in self.reviews)
        average_score = sum(r.score for r in self.reviews) / total

        quality_dist: dict[str, int] = {}
        dim_scores: dict[str, list[float]] = {}
        for review in self.reviews:
            ql = review.quality_level.value
            quality_dist[ql] = quality_dist.get(ql, 0) + 1
            for v in review.violations:
                dim = v.rule.dimension.value
                if dim not in dim_scores:
                    dim_scores[dim] = []
                dim_scores[dim].append(1.0 if v.rule.severity == Severity.CRITICAL else 0.5)

        self.summary = {
            "total_reviews": total,
            "total_violations": total_violations,
            "average_score": round(average_score, 3),
            "quality_distribution": quality_dist,
            "dimension_scores": {
                d: round(sum(s) / len(s), 3) for d, s in dim_scores.items()
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "reviews": [r.to_dict() for r in self.reviews],
            "summary": self.summary,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


@dataclass
class QAConfig:
    """Configuration for the QA Critic system."""
    policy_pack: PolicyPack = PolicyPack.BALANCED
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    execution_timeout_ms: int = 30000
    parallel_rules: bool = True
    max_parallel_rules: int = 10
    enable_gap_healing: bool = True
    audit_log_path: Optional[str] = None
    rules_directory: Optional[str] = None
    auto_remediate: bool = False
    violation_threshold: float = 0.3
    quality_threshold: QualityLevel = QualityLevel.ACCEPTABLE
    escalation_enabled: bool = True
    escalation_threshold: int = 5
    custom_rules: list[QARule] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QAConfig":
        config = cls()
        if "policy_pack" in data:
            config.policy_pack = PolicyPack(data["policy_pack"])
        for key, value in data.items():
            if key == "policy_pack":
                continue
            if key == "quality_threshold" and isinstance(value, str):
                config.quality_threshold = QualityLevel(value)
            elif hasattr(config, key):
                setattr(config, key, value)
        return config

    @classmethod
    def from_json(cls, json_str: str) -> "QAConfig":
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def from_file(cls, path: str) -> "QAConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_pack": self.policy_pack.value,
            "max_retries": self.max_retries,
            "retry_delay_seconds": self.retry_delay_seconds,
            "execution_timeout_ms": self.execution_timeout_ms,
            "parallel_rules": self.parallel_rules,
            "max_parallel_rules": self.max_parallel_rules,
            "enable_gap_healing": self.enable_gap_healing,
            "audit_log_path": self.audit_log_path,
            "rules_directory": self.rules_directory,
            "auto_remediate": self.auto_remediate,
            "violation_threshold": self.violation_threshold,
            "quality_threshold": self.quality_threshold.value,
            "escalation_enabled": self.escalation_enabled,
            "escalation_threshold": self.escalation_threshold,
        }


# Default severity-to-action mapping for policy packs
POLICY_ACTIONS: dict[PolicyPack, dict[Severity, EnforcementAction]] = {
    PolicyPack.STRICT: {
        Severity.INFO: EnforcementAction.WARN,
        Severity.LOW: EnforcementAction.RETRY,
        Severity.MEDIUM: EnforcementAction.BLOCK,
        Severity.HIGH: EnforcementAction.BLOCK,
        Severity.CRITICAL: EnforcementAction.ESCALATE,
    },
    PolicyPack.BALANCED: {
        Severity.INFO: EnforcementAction.LOG,
        Severity.LOW: EnforcementAction.LOG,
        Severity.MEDIUM: EnforcementAction.WARN,
        Severity.HIGH: EnforcementAction.RETRY,
        Severity.CRITICAL: EnforcementAction.BLOCK,
    },
    PolicyPack.PERMISSIVE: {
        Severity.INFO: EnforcementAction.NOOP,
        Severity.LOW: EnforcementAction.LOG,
        Severity.MEDIUM: EnforcementAction.LOG,
        Severity.HIGH: EnforcementAction.WARN,
        Severity.CRITICAL: EnforcementAction.RETRY,
    },
}


def get_action_for_severity(
    policy: PolicyPack, severity: Severity
) -> EnforcementAction:
    """Look up the enforcement action for a given policy and severity."""
    return POLICY_ACTIONS.get(policy, POLICY_ACTIONS[PolicyPack.BALANCED]).get(
        severity, EnforcementAction.WARN
    )
