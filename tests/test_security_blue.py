"""Tests for the blue team: threat analysis, defenses, validation."""

import pytest

from agency.security.blue.defender import (
    BlueTeam,
    BlueTeamError,
    DefenseStatus,
    DefenseType,
    Threat,
    ThreatKind,
    ThreatSeverity,
)


def _threat(**kwargs) -> Threat:
    base = {"id": "th-1", "name": "sql-injection", "kind": ThreatKind.INJECTION,
            "severity": ThreatSeverity.HIGH, "vector": "input"}
    base.update(kwargs)
    return Threat(**base)


def test_analyze_threat_confidence_by_severity(test_blue_team: BlueTeam):
    low = test_blue_team.analyze_threat(_threat(id="t-low", severity=ThreatSeverity.LOW))
    crit = test_blue_team.analyze_threat(_threat(id="t-crit", severity=ThreatSeverity.CRITICAL))
    assert crit.detection_confidence > low.detection_confidence
    assert low.detected is True
    assert low.logging_channels
    assert low.prevention_active
    assert low.isolation_available is True
    assert low.recommendations


def test_analyze_threat_cached(test_blue_team: BlueTeam):
    first = test_blue_team.analyze_threat(_threat())
    second = test_blue_team.analyze_threat("th-1")
    assert first is second
    assert test_blue_team.get_analysis("th-1").threat_id == "th-1"
    with pytest.raises(BlueTeamError):
        test_blue_team.get_analysis("missing")


def test_analyze_unknown_threat_id_raises(test_blue_team: BlueTeam):
    with pytest.raises(BlueTeamError):
        test_blue_team.analyze_threat("nope")


def test_kind_specific_recommendations(test_blue_team: BlueTeam):
    exfil = test_blue_team.analyze_threat(
        _threat(id="t-ex", kind=ThreatKind.EXFILTRATION))
    assert any("egress" in r for r in exfil.recommendations)
    recon = test_blue_team.analyze_threat(
        _threat(id="t-re", kind=ThreatKind.RECONNAISSANCE))
    assert recon.recommendations


def test_propose_defenses_all_types(test_blue_team: BlueTeam):
    threat = _threat()
    test_blue_team.analyze_threat(threat)
    defenses = test_blue_team.propose_defenses(threat)
    assert defenses
    types = {d.type for d in defenses}
    assert DefenseType.DETECTION in types
    assert DefenseType.LOGGING in types
    assert DefenseType.PREVENTION in types
    assert all(d.threat_id == "th-1" for d in defenses)
    assert all(d.status is DefenseStatus.PROPOSED for d in defenses)


def test_propose_defenses_by_id(test_blue_team: BlueTeam):
    test_blue_team.analyze_threat(_threat())
    defenses = test_blue_team.propose_defenses("th-1")
    assert defenses


def test_validate_defense_success(test_blue_team: BlueTeam):
    test_blue_team.analyze_threat(_threat())
    defense = test_blue_team.propose_defenses("th-1")[0]
    validated = test_blue_team.validate_defense(defense.id)
    assert validated.status is DefenseStatus.VALIDATED
    assert validated.validated_at is not None


def test_validate_defense_missing_config_fails(test_blue_team: BlueTeam):

    threat = _threat()
    test_blue_team.analyze_threat(threat)
    defense = test_blue_team.propose_defenses(threat)[0]
    # corrupt the stored defense config directly
    stored = test_blue_team.get_defense(defense.id)
    object.__setattr__(stored, "config", {})
    result = test_blue_team.validate_defense(defense.id)
    assert result.status is DefenseStatus.FAILED
    assert result.validated_at is None


def test_validate_unknown_defense_raises(test_blue_team: BlueTeam):
    with pytest.raises(BlueTeamError):
        test_blue_team.validate_defense("def-missing")
    with pytest.raises(BlueTeamError):
        test_blue_team.get_defense("def-missing")


def test_list_defenses_filters(test_blue_team: BlueTeam):
    test_blue_team.analyze_threat(_threat())
    defenses = test_blue_team.propose_defenses("th-1")
    test_blue_team.validate_defense(defenses[0].id)
    assert len(test_blue_team.list_defenses()) == len(defenses)
    assert len(test_blue_team.list_defenses(threat_id="th-1")) == len(defenses)
    assert len(test_blue_team.list_defenses(status=DefenseStatus.VALIDATED)) == 1
    assert test_blue_team.list_defenses(threat_id="other") == []


def test_custom_controls(test_blue_team: BlueTeam):
    custom = BlueTeam(controls={DefenseType.DETECTION: [{"name": "x", "config": {}}]})
    analysis = custom.analyze_threat(_threat(id="c1"))
    assert analysis.detected is True
    assert analysis.prevention_active == []
