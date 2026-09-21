"""Transforms technical findings into risk scores and tracks scorer accuracy.

:class:`RiskScorer` sits between the evidence system and the risk engine:

* ``score_finding``  — wraps :class:`~agency.risk.engine.engine.RiskEngine` and
  calibrates the result against the reporting agent's historical accuracy.
* ``score_agent``   — aggregates every finding an agent has ever scored.
* ``score_system``  — aggregates every finding raised against a system.

Historical accuracy is persisted and drives a *calibration* multiplier: agents
with a high false-positive rate are dampened, agents with a clean record are
left untouched. False positives/negatives are recorded explicitly via
:meth:`RiskScorer.record_outcome`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Self

import aiosqlite
from pydantic import BaseModel, Field
from structlog import get_logger

from ..evidence import EvidenceStore
from ..evidence.store.models import EvidenceEntry, Finding, utcnow
from .engine import RiskEngine
from .engine.models import RiskCategory, RiskModel

logger = get_logger(__name__)

_SCORER_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_accuracy (
    agent_id        TEXT PRIMARY KEY,
    findings_scored INTEGER NOT NULL DEFAULT 0,
    true_positives  INTEGER NOT NULL DEFAULT 0,
    false_positives INTEGER NOT NULL DEFAULT 0,
    false_negatives INTEGER NOT NULL DEFAULT 0,
    true_negatives  INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scored_findings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id  TEXT NOT NULL,
    agent_id    TEXT,
    system_id   TEXT,
    target      TEXT NOT NULL,
    category    TEXT NOT NULL,
    risk        TEXT NOT NULL,
    scored_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scored_system ON scored_findings (system_id);
CREATE INDEX IF NOT EXISTS idx_scored_agent ON scored_findings (agent_id);
CREATE INDEX IF NOT EXISTS idx_scored_finding ON scored_findings (finding_id);
"""


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _iso(value: datetime) -> str:
    return value.isoformat()


class Outcome(str, Enum):
    """Ground-truth label applied to a previously scored finding."""

    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"
    TRUE_NEGATIVE = "true_negative"


class AgentAccuracy(BaseModel):
    """Historical accuracy of a reporting agent's risk assessments."""

    agent_id: str
    findings_scored: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def precision(self) -> float | None:
        """Share of flagged findings that were genuinely true positives."""
        if self.true_positives + self.false_positives == 0:
            return None
        return self.true_positives / (self.true_positives + self.false_positives)

    @property
    def recall(self) -> float | None:
        if self.true_positives + self.false_negatives == 0:
            return None
        return self.true_positives / (self.true_positives + self.false_negatives)

    @property
    def f1(self) -> float | None:
        precision = self.precision
        recall = self.recall
        if precision is None or recall is None or precision + recall == 0:
            return None
        return 2 * (precision * recall) / (precision + recall)

    @property
    def calibration(self) -> float:
        """Confidence multiplier in ``[0.5, 1.0]`` derived from precision.

        A perfect record (``precision >= 0.9``) leaves estimates untouched; a
        chronic false-positive finder is dampened towards half-confidence.
        """
        precision = self.precision
        if precision is None or precision >= 0.9:
            return 1.0
        return _clamp(0.5 + precision * 0.6)

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> AgentAccuracy:
        """Reconstruct accuracy from an ``agent_accuracy`` table row."""
        return cls(
            agent_id=row[0],
            findings_scored=row[1],
            true_positives=row[2],
            false_positives=row[3],
            false_negatives=row[4],
            true_negatives=row[5],
            updated_at=datetime.fromisoformat(row[6]),
        )


class AgentRiskProfile(BaseModel):
    """Aggregate risk posture of everything one agent has scored."""

    agent_id: str
    risk: RiskModel
    category: RiskCategory
    finding_count: int
    accuracy: AgentAccuracy
    top_findings: list[RiskModel] = Field(default_factory=list)


class SystemRiskProfile(BaseModel):
    """Aggregate risk posture of everything raised against one system."""

    system_id: str
    risk: RiskModel
    category: RiskCategory
    finding_count: int
    agents: dict[str, int] = Field(default_factory=dict)
    top_findings: list[RiskModel] = Field(default_factory=list)


class RiskScorer:
    """Front door from technical findings to calibrated risk profiles."""

    def __init__(
        self,
        store: EvidenceStore,
        engine: RiskEngine | None = None,
        database: str | Path = ":memory:",
    ) -> None:
        self.store = store
        self.engine = engine or RiskEngine()
        self.database = str(database)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> Self:
        """Open the accuracy/scoring database. Idempotent."""
        if self._conn is not None:
            return self
        self._conn = await aiosqlite.connect(self.database)
        if self.database != ":memory:":
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.executescript(_SCORER_SCHEMA)
        await self._conn.commit()
        logger.info("risk_scorer_initialized", database=self.database)
        return self

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> Self:
        return await self.initialize()

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    # -- scoring -------------------------------------------------------------

    async def score_finding(
        self,
        finding: Finding,
        *,
        agent_id: str | None = None,
        evidence: list[EvidenceEntry] | None = None,
        record: bool = True,
    ) -> RiskModel:
        """Score a single finding, calibrated by the reporting agent's history.

        The optional ``evidence`` list strengthens the exploitability and
        detectability estimates; when omitted it is fetched lazily from the
        evidence store.
        """
        if evidence is None:
            evidence = await self.store.evidence_for_finding(finding.id)

        risk = self.engine.calculate_risk(finding, evidence)
        if agent_id is None:
            agent_id = finding.provenance.agent_id
        risk = await self._apply_agent_prior(risk, agent_id)

        if record:
            await self._record_score(finding, risk, agent_id)
        return risk

    async def score_agent(self, agent_id: str, *, top_k: int = 10) -> AgentRiskProfile:
        """Aggregate every finding the given agent has scored."""
        rows = await self._scored_rows("agent_id = ?", agent_id)
        models = [RiskModel.model_validate_json(risk_json) for _, _, _, _, risk_json, _ in rows]
        profiles = self._aggregate(models, top_k)
        accuracy = await self.accuracy(agent_id)
        return AgentRiskProfile(
            agent_id=agent_id,
            risk=profiles["risk"],
            category=profiles["category"],
            finding_count=len(models),
            accuracy=accuracy,
            top_findings=profiles["top"],
        )

    async def score_system(self, system_id: str, *, top_k: int = 10) -> SystemRiskProfile:
        """Aggregate every finding raised against a system target."""
        rows = await self._scored_rows(
            "system_id = ? OR target LIKE ?", system_id, f"%{system_id}%"
        )
        agent_counts: dict[str, int] = {}
        models: list[RiskModel] = []
        for _, agent, _, _, risk_json, _ in rows:
            if agent:
                agent_counts[agent] = agent_counts.get(agent, 0) + 1
            models.append(RiskModel.model_validate_json(risk_json))
        profiles = self._aggregate(models, top_k)
        return SystemRiskProfile(
            system_id=system_id,
            risk=profiles["risk"],
            category=profiles["category"],
            finding_count=len(models),
            agents=dict(sorted(agent_counts.items(), key=lambda item: item[1], reverse=True)),
            top_findings=profiles["top"],
        )

    # -- accuracy ------------------------------------------------------------

    async def record_outcome(
        self, finding_id: str, outcome: Outcome, *, agent_id: str | None = None
    ) -> AgentAccuracy:
        """Apply a ground-truth outcome to a previously scored finding."""
        if agent_id is None:
            agent_id = await self._owning_agent(finding_id)
        agent_id = agent_id or "unknown"

        column = {
            Outcome.TRUE_POSITIVE: "true_positives",
            Outcome.FALSE_POSITIVE: "false_positives",
            Outcome.FALSE_NEGATIVE: "false_negatives",
            Outcome.TRUE_NEGATIVE: "true_negatives",
        }[outcome]

        conn = self._require_conn()
        async with self._lock:
            await conn.execute(
                """
                INSERT INTO agent_accuracy (agent_id, findings_scored, true_positives,
                    false_positives, false_negatives, true_negatives, updated_at)
                VALUES (?, 0, 0, 0, 0, 0, ?)
                ON CONFLICT(agent_id) DO NOTHING
                """,
                (agent_id, _iso(utcnow())),
            )
            await conn.execute(
                f"UPDATE agent_accuracy SET {column} = {column} + 1, updated_at = ? "
                "WHERE agent_id = ?",
                (_iso(utcnow()), agent_id),
            )
            await conn.commit()
        logger.info("outcome_recorded", finding_id=finding_id, agent_id=agent_id, outcome=outcome.value)
        return await self.accuracy(agent_id)

    async def accuracy(self, agent_id: str | None = None) -> AgentAccuracy | list[AgentAccuracy]:
        """Return accuracy for one agent, or for every tracked agent."""
        conn = self._require_conn()
        if agent_id is not None:
            cursor = await conn.execute(
                "SELECT agent_id, findings_scored, true_positives, false_positives, "
                "false_negatives, true_negatives, updated_at FROM agent_accuracy "
                "WHERE agent_id = ?",
                (agent_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return AgentAccuracy(agent_id=agent_id)
            return AgentAccuracy.from_row(row)
        cursor = await conn.execute(
            "SELECT agent_id, findings_scored, true_positives, false_positives, "
            "false_negatives, true_negatives, updated_at FROM agent_accuracy "
            "ORDER BY findings_scored DESC"
        )
        return [AgentAccuracy.from_row(row) for row in await cursor.fetchall()]

    # -- internals -----------------------------------------------------------

    async def _apply_agent_prior(self, risk: RiskModel, agent_id: str | None) -> RiskModel:
        if not agent_id:
            return risk
        accuracy = await self.accuracy(agent_id)
        adjustment = accuracy.calibration
        if adjustment >= 1.0:
            return risk
        data = risk.model_dump()
        data["confidence"] = _clamp(risk.confidence * adjustment)
        data["likelihood"] = _clamp(risk.likelihood * (0.4 + 0.6 * adjustment))
        return RiskModel.model_validate(data)

    async def _record_score(
        self, finding: Finding, risk: RiskModel, agent_id: str | None
    ) -> None:
        conn = self._require_conn()
        system_id = finding.provenance.system
        category = self.engine.derive_category(risk).value
        async with self._lock:
            await conn.execute(
                """
                INSERT INTO scored_findings
                    (finding_id, agent_id, system_id, target, category, risk, scored_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    finding.id,
                    agent_id,
                    system_id,
                    finding.target,
                    category,
                    risk.model_dump_json(),
                    _iso(utcnow()),
                ),
            )
            await conn.execute(
                """
                INSERT INTO agent_accuracy (agent_id, findings_scored, true_positives,
                    false_positives, false_negatives, true_negatives, updated_at)
                VALUES (?, 1, 0, 0, 0, 0, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    findings_scored = findings_scored + 1, updated_at = excluded.updated_at
                """,
                (agent_id or "unknown", _iso(utcnow())),
            )
            await conn.commit()

    async def _scored_rows(self, where: str, *params: Any) -> list[tuple[Any, ...]]:
        conn = self._require_conn()
        cursor = await conn.execute(
            f"SELECT finding_id, agent_id, system_id, target, risk, category "
            f"FROM scored_findings WHERE {where} ORDER BY id DESC",
            tuple(params),
        )
        return list(await cursor.fetchall())

    async def _owning_agent(self, finding_id: str) -> str | None:
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT agent_id FROM scored_findings WHERE finding_id = ? ORDER BY id DESC LIMIT 1",
            (finding_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    def _aggregate(
        self, models: list[RiskModel], top_k: int
    ) -> dict[str, RiskModel | RiskCategory | list[RiskModel]]:
        if not models:
            empty = RiskModel()
            return {"risk": empty, "category": self.engine.derive_category(empty), "top": []}

        means: dict[str, Any] = {}
        for field in (
            "impact",
            "likelihood",
            "confidence",
            "exposure",
            "exploitability",
            "detectability",
            "reversibility",
            "blast_radius",
        ):
            means[field] = _clamp(sum(getattr(model, field) for model in models) / len(models))
        assets: list[str] = []
        for model in models:
            for asset in model.affected_assets:
                if asset not in assets:
                    assets.append(asset)
        mean_risk = RiskModel(
            **means,
            affected_assets=assets,
            evidence={"aggregate": True, "count": len(models)},
        )
        ranked = sorted(models, key=self._priority, reverse=True)[:top_k]
        return {
            "risk": mean_risk,
            "category": self.engine.derive_category(mean_risk),
            "top": ranked,
        }

    @staticmethod
    def _priority(risk: RiskModel) -> float:
        return (
            risk.impact * 0.35
            + risk.likelihood * 0.25
            + risk.exposure * 0.20
            + risk.blast_radius * 0.20
        )

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("RiskScorer.initialize() must be called before use")
        return self._conn


__all__ = [
    "AgentAccuracy",
    "AgentRiskProfile",
    "Outcome",
    "RiskScorer",
    "SystemRiskProfile",
]