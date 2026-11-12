"""Permission policy shared by both TUIs. YOLO = bypass human approval only."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class Mode(str, Enum):
    AUTO = "auto"
    PLAN = "plan"
    YOLO = "yolo"


@dataclass
class PermissionPolicy:
    mode: Mode = Mode.AUTO
    approved_patterns: set[str] = field(default_factory=set)
    audit: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> PermissionPolicy:
        yolo = os.environ.get("AGENCY_YOLO", "").strip().lower() in ("1", "true", "yes")
        return cls(mode=Mode.YOLO if yolo else Mode.AUTO)

    @property
    def yolo(self) -> bool:
        return self.mode is Mode.YOLO

    def needs_approval(self, action: str, detail: str = "", *, protected: bool = False) -> bool:
        if self.yolo:
            self._record(action, detail, "yolo_auto_approved", protected)
            return False
        if protected:
            self._record(action, detail, "approval_required_protected", protected)
            return True
        if action in self.approved_patterns or detail in self.approved_patterns:
            self._record(action, detail, "pre_approved", protected)
            return False
        self._record(action, detail, "approval_required", protected)
        return True

    def approve(self, action: str, detail: str = "", *, remember: bool = False) -> None:
        if remember:
            self.approved_patterns.add(action)
            if detail:
                self.approved_patterns.add(detail)
        self._record(action, detail, "approved", False)

    def deny(self, action: str, detail: str = "") -> None:
        self._record(action, detail, "denied", False)

    def _record(self, action: str, detail: str, decision: str, protected: bool) -> None:
        self.audit.append(
            {
                "ts": datetime.now(UTC).isoformat(),
                "action": action,
                "detail": detail[:200],
                "decision": decision,
                "mode": self.mode.value,
                "protected": str(protected),
            }
        )
