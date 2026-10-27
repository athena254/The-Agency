"""Tests for the versioned prompt registry (deterministic, no LLM/exec)."""

from __future__ import annotations

import pytest

from agency.factory.prompts import (
    MAX_PROMPT_BODY_BYTES,
    PromptKind,
    PromptRegistry,
    PromptSpec,
    PromptStatus,
    reap_packs_missing_safety,
)


def _spec(**over) -> PromptSpec:
    args = {
        "prompt_id": "demo-segment",
        "version": "1.0.0",
        "name": "Demo Segment",
        "description": "A demo prompt segment.",
        "domain": "demo",
        "kind": PromptKind.SEGMENT,
        "body": "Do the demo thing.",
        "provenance": "team:demo",
    }
    args.update(over)
    return PromptSpec(**args)


def _registry(tmp_path) -> PromptRegistry:
    return PromptRegistry(str(tmp_path / "prompts.db"))


def _publish(reg: PromptRegistry, prompt_id: str = "demo-segment") -> None:
    reg.transition(prompt_id, "1.0.0", PromptStatus.TESTING)
    reg.transition(prompt_id, "1.0.0", PromptStatus.APPROVED, "tests pass")
    reg.publish(prompt_id, "1.0.0", "review ok")


def test_register_get_and_duplicate_version(tmp_path) -> None:
    reg = _registry(tmp_path)
    try:
        stored = reg.register(_spec())
        assert stored.status is PromptStatus.DRAFT
        assert reg.get("demo-segment", "1.0.0").body == "Do the demo thing."
        with pytest.raises(ValueError, match="duplicate prompt version"):
            reg.register(_spec())
        reg.register(_spec(version="1.0.1"))
        assert [s.version for s in reg.list_versions("demo-segment")] == ["1.0.0", "1.0.1"]
    finally:
        reg.close()


@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "latest", "", "1..0", "1.0.0.0"])
def test_malformed_version_rejected(tmp_path, bad: str) -> None:
    reg = _registry(tmp_path)
    try:
        with pytest.raises(ValueError):
            reg.register(_spec(version=bad))
    finally:
        reg.close()


@pytest.mark.parametrize("bad", ["", "  ", "../evil", "has space", "-leading"])
def test_malformed_ids_rejected(tmp_path, bad: str) -> None:
    reg = _registry(tmp_path)
    try:
        with pytest.raises(ValueError):
            reg.register(_spec(prompt_id=bad))
        with pytest.raises(ValueError):
            reg.register(_spec(domain=bad))
    finally:
        reg.close()


def test_body_bounds_and_blank_body_rejected(tmp_path) -> None:
    reg = _registry(tmp_path)
    try:
        with pytest.raises(ValueError, match="body exceeds"):
            reg.register(_spec(body="x" * (MAX_PROMPT_BODY_BYTES + 1)))
        with pytest.raises(ValueError, match="body must be a nonblank"):
            reg.register(_spec(body="   "))
    finally:
        reg.close()


def test_new_versions_must_start_in_draft(tmp_path) -> None:
    reg = _registry(tmp_path)
    try:
        with pytest.raises(ValueError, match="DRAFT"):
            reg.register(_spec(status=PromptStatus.PUBLISHED))
    finally:
        reg.close()


def test_lifecycle_transitions_and_events(tmp_path) -> None:
    reg = _registry(tmp_path)
    try:
        reg.register(_spec())
        reg.transition("demo-segment", "1.0.0", PromptStatus.TESTING)
        with pytest.raises(ValueError, match="nonempty evidence"):
            reg.transition("demo-segment", "1.0.0", PromptStatus.APPROVED)
        with pytest.raises(ValueError, match="illegal transition"):
            reg.transition("demo-segment", "1.0.0", PromptStatus.PUBLISHED)
        reg.transition("demo-segment", "1.0.0", PromptStatus.APPROVED, "tests pass")
        with pytest.raises(ValueError, match="nonempty evidence"):
            reg.publish("demo-segment", "1.0.0", "  ")
        published = reg.publish("demo-segment", "1.0.0", "review ok")
        assert published.status is PromptStatus.PUBLISHED
        assert reg.is_published("demo-segment", "1.0.0") is True
        assert reg.is_published("demo-segment", "9.9.9") is False
        events = reg.list_events("demo-segment", "1.0.0")
        assert [e["to_status"] for e in events] == [
            "DRAFT",
            "TESTING",
            "APPROVED",
            "PUBLISHED",
        ]
    finally:
        reg.close()


def test_restart_persistence(tmp_path) -> None:
    db = str(tmp_path / "prompts.db")
    reg = PromptRegistry(db)
    reg.register(_spec())
    _publish(reg)
    reg.close()
    reg2 = PromptRegistry(db)
    try:
        reopened = reg2.get("demo-segment", "1.0.0")
        assert reopened.status is PromptStatus.PUBLISHED
        assert [e["to_status"] for e in reg2.list_events("demo-segment", "1.0.0")][-1] == (
            "PUBLISHED"
        )
    finally:
        reg2.close()


def test_seed_builtin_requires_builtin_provenance_and_published(tmp_path) -> None:
    reg = _registry(tmp_path)
    try:
        with pytest.raises(ValueError, match="builtin"):
            reg.seed_builtin(_spec(status=PromptStatus.PUBLISHED), evidence="seed")
        with pytest.raises(ValueError, match="PUBLISHED"):
            reg.seed_builtin(_spec(provenance="builtin:demo"), evidence="seed")
    finally:
        reg.close()


def test_seed_builtin_is_idempotent_and_never_overwrites(tmp_path) -> None:
    reg = _registry(tmp_path)
    try:
        spec = _spec(provenance="builtin:demo", status=PromptStatus.PUBLISHED)
        assert reg.seed_builtin(spec, evidence="seed") is True
        assert reg.seed_builtin(spec, evidence="seed") is False
        changed = _spec(provenance="builtin:demo", status=PromptStatus.PUBLISHED, body="changed")
        assert reg.seed_builtin(changed, evidence="seed") is False
        assert reg.get("demo-segment", "1.0.0").body == "Do the demo thing."
        assert [e["to_status"] for e in reg.list_events("demo-segment", "1.0.0")] == ["PUBLISHED"]
    finally:
        reg.close()


def test_unknown_prompt_raises_keyerror(tmp_path) -> None:
    reg = _registry(tmp_path)
    try:
        with pytest.raises(KeyError):
            reg.get("missing", "1.0.0")
        with pytest.raises(KeyError):
            reg.list_events("missing", "1.0.0")
    finally:
        reg.close()


def test_reap_safety_pairing_helper() -> None:
    assert reap_packs_missing_safety({"reap-17-malware-analysis"}) == {"reap-17-malware-analysis"}
    assert reap_packs_missing_safety({"reap-17-malware-analysis", "reap-safety-scope"}) == set()
    assert reap_packs_missing_safety({"reap-wildcard", "reap-prompt-writing-tips"}) == set()
    assert reap_packs_missing_safety({"agency-base-identity"}) == set()
    assert reap_packs_missing_safety(set()) == set()
