"""Mandatory human gates are named from paths and shown in approval briefs."""

from __future__ import annotations

from types import SimpleNamespace

from agency.forge.approvals import _brief
from agency.forge.human_gates import required_human_gates
from agency.forge.orchestration import JobState


def test_path_based_gates() -> None:
    assert "auth" in required_human_gates(["src/auth/login.py"])
    assert "payments" in required_human_gates(["billing/checkout.py"])
    assert "factory_guardrails" in required_human_gates([".github/workflows/ci.yml"])
    assert required_human_gates(["src/landing.ts"]) == ()


def test_approval_brief_shows_required_gates() -> None:
    job = SimpleNamespace(
        job_id="j-1",
        title="Harden auth",
        state=JobState.AWAITING_HUMAN,
        gate="PASS",
        report_id="r-1",
        changed_paths=("src/auth/login.py", "billing/checkout.py"),
        repo="C:/repo",
        updated_at="2026-01-01T00:00:00Z",
    )
    brief = _brief(job)  # type: ignore[arg-type]
    assert "auth" in brief.required_human_gates
    assert "payments" in brief.required_human_gates
