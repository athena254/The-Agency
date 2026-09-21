from __future__ import annotations

import threading
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PlanStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RedTeamPlan(BaseModel):
    id: str
    target: str
    hypothesis: str
    scope: list[str] = Field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    created_by: str
    approved_by: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class RedTeamPlannerError(Exception):
    pass


class RedTeamPlanner:
    def __init__(self) -> None:
        self._plans: dict[str, RedTeamPlan] = {}
        self._lock = threading.RLock()

    def create_plan(
        self,
        target: str,
        hypothesis: str,
        scope: Sequence[str] = (),
        created_by: str = "system",
    ) -> RedTeamPlan:
        plan = RedTeamPlan(
            id=f"plan-{uuid.uuid4().hex[:12]}",
            target=target,
            hypothesis=hypothesis,
            scope=list(scope),
            status=PlanStatus.PENDING_APPROVAL,
            created_by=created_by,
        )
        with self._lock:
            self._plans[plan.id] = plan
        logger.info(
            "red_plan_created",
            plan_id=plan.id,
            target=target,
            created_by=created_by,
        )
        return plan

    def approve_plan(
        self, plan_id: str, approved_by: str = "security-admin"
    ) -> RedTeamPlan:
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise RedTeamPlannerError(f"unknown plan {plan_id}")
            if plan.status in (
                PlanStatus.APPROVED,
                PlanStatus.REJECTED,
                PlanStatus.COMPLETED,
                PlanStatus.CANCELLED,
            ):
                raise RedTeamPlannerError(
                    f"plan {plan_id} cannot be approved from status {plan.status.value}"
                )
            updated = plan.model_copy(
                update={
                    "status": PlanStatus.APPROVED,
                    "approved_by": approved_by,
                    "updated_at": _utcnow(),
                }
            )
            self._plans[plan_id] = updated
        logger.info(
            "red_plan_approved",
            plan_id=plan_id,
            approved_by=approved_by,
        )
        return updated

    def list_plans(self, status: PlanStatus | None = None) -> list[RedTeamPlan]:
        with self._lock:
            plans = [
                plan
                for plan in self._plans.values()
                if status is None or plan.status is status
            ]
        return sorted(plans, key=lambda plan: plan.created_at, reverse=True)

    def get_plan(self, plan_id: str) -> RedTeamPlan:
        with self._lock:
            plan = self._plans.get(plan_id)
        if plan is None:
            raise RedTeamPlannerError(f"unknown plan {plan_id}")
        return plan