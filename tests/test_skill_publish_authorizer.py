"""Publication gating for SkillRegistry when a publish authorizer is configured."""

from __future__ import annotations

from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus


def _draft(registry: SkillRegistry) -> None:
    registry.register(
        SkillSpec(
            skill_id="s",
            version="1.0.0",
            name="S",
            description="d",
            implementation="op",
            provenance="test",
            status=SkillStatus.DRAFT,
        )
    )
    registry.transition("s", "1.0.0", SkillStatus.TESTING)
    registry.transition("s", "1.0.0", SkillStatus.APPROVED, "ev")


def test_publish_gated_without_approver(tmp_path) -> None:
    reg = SkillRegistry(str(tmp_path / "s.db"), publish_authorizer=lambda a, p: True)
    _draft(reg)
    try:
        reg.publish("s", "1.0.0", "ev")
    except ValueError as exc:
        assert "approver" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_publish_gated_authorizer_denies(tmp_path) -> None:
    reg = SkillRegistry(str(tmp_path / "s.db"), publish_authorizer=lambda a, p: False)
    _draft(reg)
    try:
        reg.publish("s", "1.0.0", "ev", approver="alice")
    except ValueError as exc:
        assert "denied" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_publish_allowed_with_authorizer(tmp_path) -> None:
    reg = SkillRegistry(
        str(tmp_path / "s.db"),
        publish_authorizer=lambda a, p: a == "alice" and p == "skill.publish",
    )
    _draft(reg)
    spec = reg.publish("s", "1.0.0", "ev", approver="alice")
    assert spec.status is SkillStatus.PUBLISHED
    events = reg.list_events("s", "1.0.0")
    assert any(e.get("approver") == "alice" for e in events)
