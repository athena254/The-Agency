"""Hermetic tests for the durable assignment journal foundation (A5 slice 3a)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agency.autonomy.assignment_journal import (
    AssignmentJournal,
    AssignmentJournalError,
    Transition,
)

ASSIGNMENT = "asg_" + "1" * 32
SECOND_ASSIGNMENT = "asg_" + "2" * 32
TASK = "task_" + "1" * 32
WORKER = "worker_" + "1" * 32
GRANT = "a" * 64
RESULT = "b" * 64


def assign(
    journal: AssignmentJournal,
    assignment_id: str = ASSIGNMENT,
    *,
    task_id: str = TASK,
    worker_id: str = WORKER,
    generation: int = 0,
    at: int = 100,
    grant_digest: str | None = None,
) -> Transition:
    return journal.assign(
        assignment_id=assignment_id,
        task_id=task_id,
        worker_id=worker_id,
        generation=generation,
        at=at,
        grant_digest=grant_digest,
    )


def test_duplicate_assign_replays_stored_result_without_redispatch() -> None:
    with AssignmentJournal() as journal:
        first = assign(journal)
        replay = assign(journal)
        assert first.dispatched and first.outbox is not None
        assert replay.replayed and not replay.dispatched and replay.outbox is None
        assert replay.record == first.record
        assert len(journal.outbox()) == 1
        with pytest.raises(ValueError):
            assign(journal, worker_id="worker_" + "9" * 32)


def test_transition_replay_is_a_noop() -> None:
    with AssignmentJournal() as journal:
        assign(journal)
        first = journal.transition(assignment_id=ASSIGNMENT, generation=0, state="RUNNING", at=110)
        replay = journal.transition(assignment_id=ASSIGNMENT, generation=0, state="RUNNING", at=110)
        assert first.dispatched and first.outbox is not None
        assert replay.replayed and replay.outbox is None
        assert replay.record == first.record
        assert len(journal.outbox()) == 2


def test_state_and_outbox_commit_together(tmp_path: Path) -> None:
    db_path = tmp_path / "atomic.db"
    with AssignmentJournal(db_path) as journal:
        assign(journal)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TRIGGER block_outbox BEFORE INSERT ON outbox "
            "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
        )
        conn.commit()
    with pytest.raises(ValueError), AssignmentJournal(db_path) as journal:
        journal.transition(assignment_id=ASSIGNMENT, generation=0, state="RUNNING", at=110)
    with sqlite3.connect(db_path) as conn:
        state = conn.execute(
            "SELECT state FROM assignments WHERE assignment_id=?", (ASSIGNMENT,)
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()
    assert state is not None and state[0] == "ASSIGNED"
    assert count is not None and count[0] == 1


def test_rejected_transition_leaves_no_outbox_row() -> None:
    with AssignmentJournal() as journal:
        assign(journal)
        with pytest.raises(ValueError):
            journal.transition(assignment_id=ASSIGNMENT, generation=0, state="COMPLETED", at=110)
        assert journal.get(ASSIGNMENT).state == "ASSIGNED"
        assert len(journal.outbox()) == 1


def test_stale_generation_cannot_touch_newer_assignment() -> None:
    with AssignmentJournal() as journal:
        assign(journal, ASSIGNMENT, generation=0, at=100)
        journal.transition(assignment_id=ASSIGNMENT, generation=0, state="RUNNING", at=110)
        assign(journal, SECOND_ASSIGNMENT, generation=1, at=120)
        with pytest.raises(ValueError):
            journal.transition(
                assignment_id=ASSIGNMENT,
                generation=0,
                state="COMPLETED",
                at=130,
                result_digest=RESULT,
            )
        with pytest.raises(ValueError):
            journal.transition(
                assignment_id=SECOND_ASSIGNMENT, generation=0, state="RUNNING", at=130
            )
        with pytest.raises(ValueError):
            assign(journal, "asg_" + "3" * 32, generation=0, at=140)
        assert journal.get(ASSIGNMENT).state == "RUNNING"
        assert journal.get(SECOND_ASSIGNMENT).state == "ASSIGNED"
        fresh = journal.transition(
            assignment_id=SECOND_ASSIGNMENT, generation=1, state="RUNNING", at=150
        )
        assert fresh.record.state == "RUNNING"


def test_reopen_marks_running_uncertain_and_never_resumes(tmp_path: Path) -> None:
    db_path = tmp_path / "recovery.db"
    with AssignmentJournal(db_path) as journal:
        assign(journal, ASSIGNMENT, generation=0, at=100)
        journal.transition(assignment_id=ASSIGNMENT, generation=0, state="RUNNING", at=110)
        assign(journal, SECOND_ASSIGNMENT, generation=1, at=120)
        journal.transition(assignment_id=SECOND_ASSIGNMENT, generation=1, state="RUNNING", at=130)
    with AssignmentJournal(db_path) as reopened:
        assert reopened.get(ASSIGNMENT).state == "UNCERTAIN"
        assert reopened.get(SECOND_ASSIGNMENT).state == "UNCERTAIN"
        assert [entry.assignment_id for entry in reopened.outbox()][-2:] == [
            ASSIGNMENT,
            SECOND_ASSIGNMENT,
        ]
        assert [entry.state for entry in reopened.outbox()][-2:] == [
            "UNCERTAIN",
            "UNCERTAIN",
        ]
        assert len(reopened.outbox()) == 6
        with pytest.raises(ValueError):
            reopened.transition(assignment_id=ASSIGNMENT, generation=0, state="RUNNING", at=200)
    with AssignmentJournal(db_path) as again:
        assert len(again.outbox()) == 6


def test_outbox_is_ordered_and_replayable_after_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    with AssignmentJournal(db_path) as journal:
        assign(journal, at=100)
        journal.transition(assignment_id=ASSIGNMENT, generation=0, state="RUNNING", at=110)
        journal.transition(
            assignment_id=ASSIGNMENT,
            generation=0,
            state="COMPLETED",
            at=120,
            result_digest=RESULT,
        )
    with AssignmentJournal(db_path) as reopened:
        entries = reopened.outbox()
        assert [entry.seq for entry in entries] == [1, 2, 3]
        assert [entry.state for entry in entries] == ["ASSIGNED", "RUNNING", "COMPLETED"]
        assert [entry.body["at"] for entry in entries] == [100, 110, 120]
        assert reopened.outbox(after=1) == tuple(entries[1:])
        assert reopened.outbox(after=3) == ()
        assert reopened.get(ASSIGNMENT).result_digest == RESULT


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("assignments", "state", "COMPLETED"),
        ("assignments", "integrity_digest", "0" * 64),
        ("assignments", "generation", 9),
        ("outbox", "body", "{}"),
        ("outbox", "chain_digest", "0" * 64),
        ("outbox", "integrity_digest", "0" * 64),
    ],
)
def test_tampered_records_fail_closed_on_open(
    tmp_path: Path, table: str, column: str, value: object
) -> None:
    db_path = tmp_path / "tamper.db"
    with AssignmentJournal(db_path) as journal:
        assign(journal)
        journal.transition(assignment_id=ASSIGNMENT, generation=0, state="RUNNING", at=110)
    with sqlite3.connect(db_path) as conn:
        if table == "assignments":
            conn.execute(
                f"UPDATE assignments SET {column}=? WHERE assignment_id=?", (value, ASSIGNMENT)
            )
        else:
            conn.execute(f"UPDATE outbox SET {column}=? WHERE seq=1", (value,))
        conn.commit()
    with pytest.raises(AssignmentJournalError):
        AssignmentJournal(db_path)


def test_missing_outbox_row_fails_closed_on_open(tmp_path: Path) -> None:
    db_path = tmp_path / "gap.db"
    with AssignmentJournal(db_path) as journal:
        assign(journal)
        journal.transition(assignment_id=ASSIGNMENT, generation=0, state="RUNNING", at=110)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM outbox WHERE seq=1")
        conn.commit()
    with pytest.raises(AssignmentJournalError):
        AssignmentJournal(db_path)


def test_verify_detects_post_open_tamper(tmp_path: Path) -> None:
    db_path = tmp_path / "verify.db"
    with AssignmentJournal(db_path) as journal:
        assign(journal)
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE outbox SET chain_digest=? WHERE seq=1", ("0" * 64,))
            conn.commit()
        with pytest.raises(AssignmentJournalError):
            journal.verify()


def test_optional_grant_digest_is_bound_for_every_transition() -> None:
    with AssignmentJournal() as journal:
        created = assign(journal, grant_digest=GRANT)
        assert created.record.grant_digest == GRANT
        assert created.outbox is not None
        assert created.outbox.body["grant_digest"] == GRANT
        with pytest.raises(ValueError):
            assign(journal, grant_digest="c" * 64)
        with pytest.raises(ValueError):
            journal.transition(assignment_id=ASSIGNMENT, generation=0, state="RUNNING", at=110)
        accepted = journal.transition(
            assignment_id=ASSIGNMENT,
            generation=0,
            state="RUNNING",
            at=110,
            grant_digest=GRANT,
        )
        assert accepted.record.grant_digest == GRANT
        unbound = assign(journal, SECOND_ASSIGNMENT, generation=1, at=120)
        assert unbound.record.grant_digest is None
        assert (
            journal.transition(
                assignment_id=SECOND_ASSIGNMENT, generation=1, state="RUNNING", at=130
            ).record.state
            == "RUNNING"
        )


def test_records_carry_no_private_material() -> None:
    secret = "sk-private-key-do-not-store"
    with AssignmentJournal() as journal:
        for field in ("assignment_id", "task_id", "worker_id"):
            values: dict[str, Any] = {
                "assignment_id": ASSIGNMENT,
                "task_id": TASK,
                "worker_id": WORKER,
                "generation": 0,
                "at": 100,
            }
            values[field] = f"prompt-{secret}"
            with pytest.raises(ValueError):
                journal.assign(**values)
        with pytest.raises(ValueError):
            assign(journal, grant_digest=secret)
        assign(journal, grant_digest="d" * 64)
        journal.transition(
            assignment_id=ASSIGNMENT, generation=0, state="RUNNING", at=110, grant_digest="d" * 64
        )
        journal.transition(
            assignment_id=ASSIGNMENT,
            generation=0,
            state="COMPLETED",
            at=120,
            grant_digest="d" * 64,
            result_digest=RESULT,
        )
        stored = json.dumps(
            [journal.get(ASSIGNMENT).integrity_body()] + [entry.body for entry in journal.outbox()],
            sort_keys=True,
        )
        assert secret not in stored
        assert "prompt" not in stored


async def test_async_context_manager_closes_store() -> None:
    journal = AssignmentJournal()
    async with journal:
        assign(journal)
        assert journal.get(ASSIGNMENT).state == "ASSIGNED"
    with pytest.raises(ValueError):
        journal.get(ASSIGNMENT)
