from __future__ import annotations

import threading
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ThreatSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ThreatKind(str, Enum):
    RECONNAISSANCE = "reconnaissance"
    INJECTION = "injection"
    EXFILTRATION = "exfiltration"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    DENIAL_OF_SERVICE = "denial_of_service"
    SUPPLY_CHAIN = "supply_chain"


class DefenseType(str, Enum):
    DETECTION = "detection"
    LOGGING = "logging"
    PREVENTION = "prevention"
    ISOLATION = "isolation"
    RECOVERY = "recovery"


class DefenseStatus(str, Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    ACTIVE = "active"
    DISABLED = "disabled"
    FAILED = "failed"


class Threat(BaseModel):
    id: str
    name: str
    description: str = ""
    kind: ThreatKind = ThreatKind.INJECTION
    severity: ThreatSeverity = ThreatSeverity.MEDIUM
    vector: str = ""
    detected_at: datetime = Field(default_factory=_utcnow)


class Defense(BaseModel):
    id: str
    threat_id: str
    type: DefenseType
    config: dict[str, Any] = Field(default_factory=dict)
    status: DefenseStatus = DefenseStatus.PROPOSED
    description: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    validated_at: datetime | None = None


class ThreatAnalysis(BaseModel):
    threat_id: str
    detected: bool
    detection_confidence: float = Field(ge=0.0, le=1.0)
    logging_channels: list[str]
    prevention_active: list[str]
    isolation_available: bool
    recovery_plan: str
    recommendations: list[str]
    analyzed_at: datetime = Field(default_factory=_utcnow)


class BlueTeamError(Exception):
    pass


_SEVERITY_CONFIDENCE: dict[ThreatSeverity, float] = {
    ThreatSeverity.LOW: 0.55,
    ThreatSeverity.MEDIUM: 0.7,
    ThreatSeverity.HIGH: 0.85,
    ThreatSeverity.CRITICAL: 0.95,
}

_REQUIRED_CONFIG: dict[DefenseType, tuple[str, ...]] = {
    DefenseType.DETECTION: ("sensor", "thresholds"),
    DefenseType.LOGGING: ("channel", "retention_days"),
    DefenseType.PREVENTION: ("enforce", "policy"),
    DefenseType.ISOLATION: ("backend", "network_policy"),
    DefenseType.RECOVERY: ("snapshot", "restore"),
}

CONTROL_TEMPLATES: dict[DefenseType, list[dict[str, Any]]] = {
    DefenseType.DETECTION: [
        {
            "name": "sandbox-nids",
            "config": {"sensor": "egress-cap", "thresholds": {"mode": "alert"}},
        },
        {
            "name": "file-integrity-watch",
            "config": {"sensor": "inotify", "thresholds": {"mode": "alert"}},
        },
    ],
    DefenseType.LOGGING: [
        {"name": "sandbox-audit", "config": {"channel": "audit", "retention_days": 90}},
        {"name": "stdout-capture", "config": {"channel": "stdout", "retention_days": 30}},
        {"name": "network-egress-log", "config": {"channel": "egress", "retention_days": 90}},
    ],
    DefenseType.PREVENTION: [
        {"name": "caps-drop", "config": {"enforce": True, "policy": "cap-drop-all"}},
        {"name": "readonly-fs", "config": {"enforce": True, "policy": "read-only-rootfs"}},
        {"name": "import-filter", "config": {"enforce": True, "policy": "allowlist-imports"}},
    ],
    DefenseType.ISOLATION: [
        {"name": "docker-namespaces", "config": {"backend": "docker", "network_policy": "none"}},
        {"name": "resource-limits", "config": {"backend": "cgroups", "network_policy": "none"}},
    ],
    DefenseType.RECOVERY: [
        {"name": "snapshot-restore", "config": {"snapshot": "docker-commit", "restore": "detached-restart"}},
    ],
}

_KIND_RECOMMENDATIONS: dict[ThreatKind, list[str]] = {
    ThreatKind.RECONNAISSANCE: [
        "rate-limit sandbox probe timing",
        "disable network probing surface",
        "monitor repeated sandbox create/destroy cycles",
    ],
    ThreatKind.INJECTION: [
        "enforce import filter allowlist",
        "keep root filesystem read-only",
        "scrub untrusted input before evaluation",
    ],
    ThreatKind.EXFILTRATION: [
        "force network-policy NONE for untrusted agents",
        "add egress DLP on the Lattice bridge",
        "enable network egress logging",
    ],
    ThreatKind.PRIVILEGE_ESCALATION: [
        "audit capability drops on every container",
        "drop CAP_ALL and enforce no-new-privileges",
        "run sandbox user as non-root uid 1000",
    ],
    ThreatKind.DENIAL_OF_SERVICE: [
        "enforce per-agent sandbox semaphore",
        "apply cpu/memory/pids limits",
        "set fail-closed timeout on long-running commands",
    ],
    ThreatKind.SUPPLY_CHAIN: [
        "pin base image digests",
        "schedule periodic image rebuild validation",
        "approve dependency changes before rollout",
    ],
}


class BlueTeam:
    def __init__(
        self,
        controls: Mapping[DefenseType, Sequence[dict[str, Any]]] | None = None,
    ) -> None:
        self._controls: dict[DefenseType, list[dict[str, Any]]] = {
            dtype: [dict(item) for item in templates]
            for dtype, templates in (controls if controls is not None else CONTROL_TEMPLATES).items()
        }
        self._threats: dict[str, Threat] = {}
        self._analyses: dict[str, ThreatAnalysis] = {}
        self._defenses: dict[str, Defense] = {}
        self._lock = threading.RLock()

    def _resolve_threat(self, threat: str | Threat) -> Threat:
        if isinstance(threat, Threat):
            if threat.id not in self._threats:
                with self._lock:
                    self._threats[threat.id] = threat
            return threat
        with self._lock:
            resolved = self._threats.get(threat)
        if resolved is None:
            raise BlueTeamError(f"unknown threat {threat}")
        return resolved

    def analyze_threat(self, threat: str | Threat) -> ThreatAnalysis:
        resolved = self._resolve_threat(threat)
        with self._lock:
            cached = self._analyses.get(resolved.id)
        if cached is not None:
            return cached

        logging_channels = [
            item["name"] for item in self._controls.get(DefenseType.LOGGING, [])
        ]
        prevention_active = [
            item["name"] for item in self._controls.get(DefenseType.PREVENTION, [])
        ]
        isolation_list = [
            item["name"] for item in self._controls.get(DefenseType.ISOLATION, [])
        ]
        recovery_list = [
            item["name"] for item in self._controls.get(DefenseType.RECOVERY, [])
        ]

        recommendations = list(_KIND_RECOMMENDATIONS[resolved.kind])
        if resolved.kind is not ThreatKind.EXFILTRATION and resolved.kind is not ThreatKind.RECONNAISSANCE:
            recommendations.append("run a purple validation pass on the finding")

        analysis = ThreatAnalysis(
            threat_id=resolved.id,
            detected=bool(self._controls.get(DefenseType.DETECTION)),
            detection_confidence=_SEVERITY_CONFIDENCE[resolved.severity],
            logging_channels=logging_channels,
            prevention_active=prevention_active,
            isolation_available=bool(isolation_list),
            recovery_plan=", ".join(recovery_list) if recovery_list else "unspecified",
            recommendations=recommendations,
        )
        with self._lock:
            self._analyses[resolved.id] = analysis
        logger.info(
            "blue_threat_analyzed",
            threat_id=resolved.id,
            kind=resolved.kind.value,
            severity=resolved.severity.value,
            detected=analysis.detected,
        )
        return analysis

    def propose_defenses(self, threat: str | Threat) -> list[Defense]:
        resolved = self._resolve_threat(threat)
        defenses: list[Defense] = []
        with self._lock:
            for dtype, templates in self._controls.items():
                for template in templates:
                    defense = Defense(
                        id=f"def-{uuid.uuid4().hex[:12]}",
                        threat_id=resolved.id,
                        type=dtype,
                        config=dict(template["config"]),
                        description=str(template["name"]),
                    )
                    self._defenses[defense.id] = defense
                    defenses.append(defense)
        logger.info(
            "blue_defenses_proposed",
            threat_id=resolved.id,
            count=len(defenses),
        )
        return defenses

    def validate_defense(self, defense_id: str) -> Defense:
        with self._lock:
            defense = self._defenses.get(defense_id)
        if defense is None:
            raise BlueTeamError(f"unknown defense {defense_id}")
        required = set(_REQUIRED_CONFIG[defense.type])
        missing = [key for key in required if key not in defense.config]
        if missing:
            status = DefenseStatus.FAILED
            validated_at = None
        else:
            status = DefenseStatus.VALIDATED
            validated_at = _utcnow()
        validated = defense.model_copy(
            update={"status": status, "validated_at": validated_at}
        )
        with self._lock:
            self._defenses[defense_id] = validated
        logger.info(
            "blue_defense_validated",
            defense_id=defense_id,
            threat_id=validated.threat_id,
            type=validated.type.value,
            status=status.value,
        )
        return validated

    def list_defenses(
        self,
        threat_id: str | None = None,
        status: DefenseStatus | None = None,
    ) -> list[Defense]:
        with self._lock:
            defenses = [
                defense
                for defense in self._defenses.values()
                if (threat_id is None or defense.threat_id == threat_id)
                and (status is None or defense.status is status)
            ]
        return sorted(defenses, key=lambda defense: defense.created_at, reverse=True)

    def get_defense(self, defense_id: str) -> Defense:
        with self._lock:
            defense = self._defenses.get(defense_id)
        if defense is None:
            raise BlueTeamError(f"unknown defense {defense_id}")
        return defense

    def get_analysis(self, threat_id: str) -> ThreatAnalysis:
        with self._lock:
            analysis = self._analyses.get(threat_id)
        if analysis is None:
            raise BlueTeamError(f"no analysis for threat {threat_id}")
        return analysis