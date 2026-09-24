"""Tests for the purple team: attack-defense pairing and verdicts."""

import pytest

from agency.security.blue.defender import BlueTeamError, Threat, ThreatKind, ThreatSeverity
from agency.security.purple.validator import PurpleTeam, PurpleTeamError, Verdict


def _run_attack(test_red_planner, test_red_executor, test_sandbox, approved: bool = True):
    plan = test_red_planner.create_plan(target="staging/api", hypothesis="sqli")
    if approved:
        test_red_planner.approve_plan(plan.id)
    return test_red_executor.execute_test(plan.id, test_sandbox.id)


def _defense_for(test_blue_team, threat_id: str = "th-1"):
    threat = Threat(id=threat_id, name="sqli", kind=ThreatKind.INJECTION,
                    severity=ThreatSeverity.HIGH)
    test_blue_team.analyze_threat(threat)
    defense = test_blue_team.propose_defenses(threat)[0]
    return test_blue_team.validate_defense(defense.id)


def test_passed_attack_confirmed(test_purple_team: PurpleTeam, test_red_planner,
                                 test_red_executor, test_blue_team, test_sandbox):
    attack = _run_attack(test_red_planner, test_red_executor, test_sandbox)
    defense = _defense_for(test_blue_team)
    result = test_purple_team.run_attack_defense_test(attack.id, defense.id)
    assert result.verdict is Verdict.CONFIRMED
    assert result.finding_id == attack.id
    assert result.defense_result is not None
    assert len(result.evidence) == len(attack.evidence)


def test_blocked_attack_not_vulnerable(test_purple_team: PurpleTeam, test_red_planner,
                                       test_red_executor, test_blue_team, test_sandbox):
    attack = _run_attack(test_red_planner, test_red_executor, test_sandbox, approved=False)
    defense = _defense_for(test_blue_team)
    result = test_purple_team.run_attack_defense_test(attack.id, defense.id)
    assert result.verdict is Verdict.NOT_VULNERABLE


def test_validate_finding_without_defense(test_purple_team: PurpleTeam, test_red_planner,
                                          test_red_executor, test_sandbox):
    attack = _run_attack(test_red_planner, test_red_executor, test_sandbox)
    result = test_purple_team.validate_finding(attack.id)
    assert result.verdict is Verdict.CONFIRMED
    assert result.defense_result is None


def test_validate_finding_unknown_raises(test_purple_team: PurpleTeam):
    with pytest.raises(PurpleTeamError):
        test_purple_team.validate_finding("test-missing")


def test_run_attack_defense_unknown_ids(test_purple_team: PurpleTeam):
    with pytest.raises((BlueTeamError, PurpleTeamError)):
        test_purple_team.run_attack_defense_test("test-missing", "def-missing")


def test_generate_report_counts(test_purple_team: PurpleTeam, test_red_planner,
                                test_red_executor, test_blue_team, test_sandbox):
    confirmed = _run_attack(test_red_planner, test_red_executor, test_sandbox)
    blocked = _run_attack(test_red_planner, test_red_executor, test_sandbox, approved=False)
    defense = _defense_for(test_blue_team)
    test_purple_team.run_attack_defense_test(confirmed.id, defense.id)
    test_purple_team.run_attack_defense_test(blocked.id, defense.id)
    report = test_purple_team.generate_report()
    assert report.total_findings == 2
    assert report.confirmed == 1
    assert report.not_vulnerable == 1
    assert report.confirmed + report.not_vulnerable + report.inconclusive == report.total_findings
    assert len(report.findings) == 2


def test_generate_report_empty(test_purple_team: PurpleTeam):
    report = test_purple_team.generate_report()
    assert report.total_findings == 0
    assert report.findings == []
