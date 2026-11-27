"""Tests for bounded task-intake admission."""

from __future__ import annotations

import pytest

from agency.api.admission import AdmissionRejected, check_admission, max_pending_tasks


def test_admission_allows_room_and_refuses_full() -> None:
    check_admission(0, 2)
    check_admission(1, 2)
    with pytest.raises(AdmissionRejected):
        check_admission(2, 2)


def test_admission_rejects_bad_arguments() -> None:
    with pytest.raises(ValueError):
        check_admission(-1, 2)
    with pytest.raises(ValueError):
        check_admission(0, 0)


def test_cap_is_env_driven_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENCY_API_MAX_PENDING_TASKS", "12")
    assert max_pending_tasks() == 12
    monkeypatch.setenv("AGENCY_API_MAX_PENDING_TASKS", "bad")
    assert max_pending_tasks() == 1000
    monkeypatch.setenv("AGENCY_API_MAX_PENDING_TASKS", "999999")
    assert max_pending_tasks() == 100_000
