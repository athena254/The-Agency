"""Tests for Lattice data models and configuration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agency.lattice.config import load_config, reset_config, validate_config
from agency.lattice.models import (
    ConsensusProposal,
    EdgeType,
    LatticeConfig,
    LatticeEvent,
    NodeType,
    Reputation,
    Vote,
)


def _now() -> datetime:
    return datetime.now(UTC)


# -- enums ------------------------------------------------------------ #


def test_node_type_values() -> None:
    assert NodeType.AGENT == "agent"
    assert NodeType.TASK == "task"
    assert NodeType.DELIVERABLE == "deliverable"
    assert NodeType.EVIDENCE == "evidence"
    assert NodeType.DECISION == "decision"
    assert NodeType.POLICY == "policy"
    assert NodeType.WING == "wing"
    assert NodeType.EVENT == "event"
    assert {t.value for t in NodeType} == {
        "agent",
        "task",
        "deliverable",
        "evidence",
        "decision",
        "policy",
        "wing",
        "event",
    }


def test_edge_type_values() -> None:
    assert EdgeType.DEPENDS_ON == "depends_on"
    assert EdgeType.PRODUCED_BY == "produced_by"
    assert EdgeType.EVIDENCE_FOR == "evidence_for"
    assert EdgeType.VOTED_ON == "voted_on"
    assert EdgeType.GOVERNS == "governs"
    assert EdgeType.MEMBER_OF == "member_of"
    assert EdgeType.TRUSTS == "trusts"
    assert EdgeType.SUPERSEDES == "supersedes"
    assert len(set(EdgeType)) == 8


# -- LatticeEvent ------------------------------------------------------ #


def test_lattice_event_round_trip() -> None:
    event = LatticeEvent(
        event_id="evt-1",
        timestamp=_now(),
        event_type="node_created",
        actor="butler",
        target_id="node-1",
        payload={"node_type": "agent"},
        prior_state=None,
    )
    restored = LatticeEvent.from_dict(event.to_dict())
    assert restored == event
    assert restored.timestamp.tzinfo is not None


def test_lattice_event_is_frozen() -> None:
    event = LatticeEvent(
        event_id="evt-1",
        timestamp=_now(),
        event_type="node_created",
        actor="system",
        target_id="n1",
        payload={},
    )
    with pytest.raises(AttributeError):
        event.actor = "other"  # type: ignore[misc]


def test_lattice_event_rejects_empty_fields() -> None:
    with pytest.raises(ValueError):
        LatticeEvent(
            event_id="",
            timestamp=_now(),
            event_type="node_created",
            actor="system",
            target_id="n1",
            payload={},
        )


# -- governance dataclasses -------------------------------------------- #


def test_vote_round_trip() -> None:
    vote = Vote(
        voter_id="butler",
        proposal_id="prop-1",
        decision="approve",
        evidence=["ev-1"],
        weight=1.0,
        timestamp=_now(),
    )
    assert Vote.from_dict(vote.to_dict()) == vote


def test_vote_rejects_bad_decision() -> None:
    with pytest.raises(ValueError):
        Vote(voter_id="a", proposal_id="p", decision="maybe")


def test_reputation_round_trip() -> None:
    rep = Reputation(
        agent_id="agent-1",
        score=0.8,
        tasks_completed=8,
        tasks_failed=2,
        peer_ratings=[0.9, 0.7],
        last_updated=_now(),
    )
    assert Reputation.from_dict(rep.to_dict()) == rep


def test_reputation_rejects_bad_score() -> None:
    with pytest.raises(ValueError):
        Reputation(agent_id="a", score=1.5)


def test_proposal_round_trip() -> None:
    proposal = ConsensusProposal(
        proposal_id="prop-1",
        proposer_id="butler",
        proposal_type="spawn_agent",
        quorum_required=0.66,
        votes=[
            Vote(voter_id="butler", proposal_id="prop-1", decision="approve"),
        ],
        status="open",
        expires_at=_now() + timedelta(hours=1),
    )
    assert ConsensusProposal.from_dict(proposal.to_dict()) == proposal


def test_proposal_rejects_bad_status_and_quorum() -> None:
    with pytest.raises(ValueError):
        ConsensusProposal(
            proposal_id="p",
            proposer_id="a",
            proposal_type="t",
            quorum_required=0.0,
            expires_at=_now() + timedelta(hours=1),
        )
    with pytest.raises(ValueError):
        ConsensusProposal(
            proposal_id="p",
            proposer_id="a",
            proposal_type="t",
            status="bogus",
            expires_at=_now() + timedelta(hours=1),
        )


# -- config ------------------------------------------------------------ #


def test_lattice_config_defaults_to_sqlite() -> None:
    config = LatticeConfig()
    assert config.backend == "sqlite"
    assert config.sqlite_path == "./data/lattice.db"
    assert config.vector_dimension == 384
    assert config.default_quorum == pytest.approx(0.66)
    assert validate_config(config) == []


def test_lattice_config_round_trip() -> None:
    config = LatticeConfig(backend="sqlite", sqlite_path=":memory:")
    assert LatticeConfig.from_dict(config.to_dict()) == config


def test_load_config_defaults_when_no_file(clean_env: None) -> None:
    reset_config()
    config = load_config()
    assert config.backend == "sqlite"


def test_load_config_prefers_env_over_yaml(
    clean_env: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "lattice.yaml"
    path.write_text("lattice:\n  backend: sqlite\n  sqlite:\n    path: ./data/file.db\n", encoding="utf-8")
    monkeypatch.setenv("LATTICE_SQLITE_PATH", ":memory:")
    config = load_config(path)
    assert config.backend == "sqlite"
    assert config.sqlite_path == ":memory:"


def test_load_config_reads_nested_yaml(
    clean_env: None, tmp_path, sample_config_dict: dict
) -> None:
    import yaml

    path = tmp_path / "lattice.yaml"
    path.write_text(yaml.safe_dump(sample_config_dict), encoding="utf-8")
    config = load_config(path)
    assert config.sqlite_path == ":memory:"
    assert config.event_retention_days == 365
    assert config.default_quorum == pytest.approx(0.66)


def test_validate_config_catches_bad_backend() -> None:
    config = LatticeConfig(backend="bogus")
    assert validate_config(config) != []


def test_load_config_rejects_invalid_backend(
    clean_env: None, tmp_path
) -> None:
    path = tmp_path / "lattice.yaml"
    path.write_text("lattice:\n  backend: bogus\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)
