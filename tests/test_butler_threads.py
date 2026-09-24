"""Tests for the persistent Butler thread store.

Covers restart persistence, per-thread isolation, owner enforcement
(no existence oracle: unknown and wrong-owner ids share one KeyError),
branch rules (same workspace/owner, no implicit message inheritance),
input validation, and SQL-injection-as-data handling.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agency.butler.threads import ThreadStore


def _file_store(tmp_path: Path) -> ThreadStore:
    return ThreadStore(str(tmp_path / "threads.db"))


# ------------------------------------------------------------------ #
# Persistence
# ------------------------------------------------------------------ #


def test_restart_persists_workspace_thread_messages(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    workspace = store.create_workspace("alice", "Project Alpha")
    thread = store.create_thread("alice", workspace.id, "Architecture")
    store.append_message("alice", thread.id, "user", "hello")
    store.append_message("alice", thread.id, "assistant", "hi there")
    store.close()

    reopened = ThreadStore(str(tmp_path / "threads.db"))
    try:
        workspaces = reopened.list_workspaces("alice")
        assert [w.id for w in workspaces] == [workspace.id]
        assert workspaces[0].title == "Project Alpha"

        fetched = reopened.get_thread("alice", thread.id)
        assert fetched.title == "Architecture"
        assert fetched.workspace_id == workspace.id

        messages = reopened.list_messages("alice", thread.id)
        assert [(m.role, m.content) for m in messages] == [
            ("user", "hello"),
            ("assistant", "hi there"),
        ]
    finally:
        reopened.close()


def test_memory_db_supports_full_roundtrip() -> None:
    store = ThreadStore(":memory:")
    try:
        workspace = store.create_workspace("alice", "Scratch")
        thread = store.create_thread("alice", workspace.id, "Notes")
        store.append_message("alice", thread.id, "system", "context")
        assert store.list_workspaces("alice")[0].id == workspace.id
        assert store.get_thread("alice", thread.id).id == thread.id
        assert len(store.list_messages("alice", thread.id)) == 1
    finally:
        store.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    store.create_workspace("alice", "W")
    store.close()
    store.close()


def test_append_message_touches_thread_updated_at(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    try:
        workspace = store.create_workspace("alice", "W")
        thread = store.create_thread("alice", workspace.id, "T")
        message = store.append_message("alice", thread.id, "user", "ping")
        fetched = store.get_thread("alice", thread.id)
        assert fetched.updated_at == message.created_at
        assert fetched.updated_at >= thread.updated_at
    finally:
        store.close()


# ------------------------------------------------------------------ #
# Isolation between threads
# ------------------------------------------------------------------ #


def test_two_threads_do_not_cross_contaminate(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    try:
        workspace = store.create_workspace("alice", "Project Alpha")
        thread_a = store.create_thread("alice", workspace.id, "Backend")
        thread_b = store.create_thread("alice", workspace.id, "Research")
        store.append_message("alice", thread_a.id, "user", "backend-only")
        store.append_message("alice", thread_b.id, "user", "research-only")

        contents_a = [m.content for m in store.list_messages("alice", thread_a.id)]
        contents_b = [m.content for m in store.list_messages("alice", thread_b.id)]
        assert contents_a == ["backend-only"]
        assert contents_b == ["research-only"]

        listed = store.list_threads("alice", workspace.id)
        assert {t.id for t in listed} == {thread_a.id, thread_b.id}
    finally:
        store.close()


def test_list_workspaces_scoped_to_owner(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    try:
        store.create_workspace("alice", "Alice WS")
        assert store.list_workspaces("alice")[0].title == "Alice WS"
        assert store.list_workspaces("bob") == []
    finally:
        store.close()


# ------------------------------------------------------------------ #
# Owner enforcement (no existence oracle)
# ------------------------------------------------------------------ #


def _seed_two_owner_store(tmp_path: Path) -> tuple[ThreadStore, str, str]:
    store = _file_store(tmp_path)
    workspace = store.create_workspace("alice", "Project Alpha")
    thread = store.create_thread("alice", workspace.id, "Architecture")
    store.append_message("alice", thread.id, "user", "secret plan")
    return store, workspace.id, thread.id


def test_other_owner_cannot_get_thread(tmp_path: Path) -> None:
    store, _, thread_id = _seed_two_owner_store(tmp_path)
    try:
        with pytest.raises(KeyError):
            store.get_thread("bob", thread_id)
        with pytest.raises(KeyError):
            store.get_thread("alice", "does-not-exist")
    finally:
        store.close()


def test_other_owner_cannot_append_or_list_messages(tmp_path: Path) -> None:
    store, _, thread_id = _seed_two_owner_store(tmp_path)
    try:
        with pytest.raises(KeyError):
            store.append_message("bob", thread_id, "user", "hijack")
        with pytest.raises(KeyError):
            store.list_messages("bob", thread_id)
        # Failed attempts leave the original history untouched.
        assert len(store.list_messages("alice", thread_id)) == 1
    finally:
        store.close()


def test_other_owner_cannot_list_threads_or_create_thread(tmp_path: Path) -> None:
    store, workspace_id, _ = _seed_two_owner_store(tmp_path)
    try:
        with pytest.raises(KeyError):
            store.list_threads("bob", workspace_id)
        with pytest.raises(KeyError):
            store.create_thread("bob", workspace_id, "Intruder")
        with pytest.raises(KeyError):
            store.list_threads("alice", "does-not-exist")
    finally:
        store.close()


def test_other_owner_cannot_branch_thread(tmp_path: Path) -> None:
    store, workspace_id, thread_id = _seed_two_owner_store(tmp_path)
    try:
        other_workspace = store.create_workspace("bob", "Bob WS")
        with pytest.raises(KeyError):
            store.create_thread("bob", other_workspace.id, "Fork", parent_thread_id=thread_id)
        # Same-workspace id is also foreign to bob: still KeyError, not a leak.
        with pytest.raises(KeyError):
            store.create_thread("bob", workspace_id, "Fork", parent_thread_id=thread_id)
    finally:
        store.close()


def test_unknown_ids_raise_keyerror_not_valueerror(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    try:
        workspace = store.create_workspace("alice", "W")
        with pytest.raises(KeyError):
            store.get_thread("alice", "missing-thread")
        with pytest.raises(KeyError):
            store.list_messages("alice", "missing-thread")
        with pytest.raises(KeyError):
            store.append_message("alice", "missing-thread", "user", "x")
        with pytest.raises(KeyError):
            store.create_thread("alice", "missing-workspace", "T")
        with pytest.raises(KeyError):
            store.list_threads("alice", "missing-workspace")
        assert workspace.id
    finally:
        store.close()


# ------------------------------------------------------------------ #
# Branching rules
# ------------------------------------------------------------------ #


def test_parent_across_workspace_denied(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    try:
        workspace_a = store.create_workspace("alice", "WA")
        workspace_b = store.create_workspace("alice", "WB")
        parent = store.create_thread("alice", workspace_a.id, "Main")
        with pytest.raises(ValueError):
            store.create_thread("alice", workspace_b.id, "Fork", parent_thread_id=parent.id)
    finally:
        store.close()


def test_no_implicit_parent_message_inheritance(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    try:
        workspace = store.create_workspace("alice", "W")
        parent = store.create_thread("alice", workspace.id, "Main")
        store.append_message("alice", parent.id, "user", "parent context")
        store.append_message("alice", parent.id, "assistant", "parent answer")

        child = store.create_thread("alice", workspace.id, "Branch", parent_thread_id=parent.id)
        assert child.parent_thread_id == parent.id
        assert store.list_messages("alice", child.id) == []
        # Parent history is unchanged.
        assert len(store.list_messages("alice", parent.id)) == 2
    finally:
        store.close()


def test_branch_without_parent_has_no_parent(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    try:
        workspace = store.create_workspace("alice", "W")
        thread = store.create_thread("alice", workspace.id, "Solo")
        assert thread.parent_thread_id is None
        assert thread.state == "active"
    finally:
        store.close()


# ------------------------------------------------------------------ #
# Input validation (fail closed)
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("owner", ["", "   "])
def test_blank_owner_rejected(tmp_path: Path, owner: str) -> None:
    store = _file_store(tmp_path)
    try:
        with pytest.raises(ValueError):
            store.create_workspace(owner, "Title")
        with pytest.raises(ValueError):
            store.list_workspaces(owner)
    finally:
        store.close()


def test_blank_title_and_content_rejected(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    try:
        with pytest.raises(ValueError):
            store.create_workspace("alice", "   ")
        workspace = store.create_workspace("alice", "W")
        with pytest.raises(ValueError):
            store.create_thread("alice", workspace.id, "")
        thread = store.create_thread("alice", workspace.id, "T")
        with pytest.raises(ValueError):
            store.append_message("alice", thread.id, "user", "   ")
        with pytest.raises(ValueError):
            store.create_thread("alice", "   ", "T")
        with pytest.raises(ValueError):
            store.get_thread("alice", "   ")
    finally:
        store.close()


@pytest.mark.parametrize("role", ["admin", "USER", "tool", "", "human"])
def test_invalid_role_rejected(tmp_path: Path, role: str) -> None:
    store = _file_store(tmp_path)
    try:
        workspace = store.create_workspace("alice", "W")
        thread = store.create_thread("alice", workspace.id, "T")
        with pytest.raises(ValueError):
            store.append_message("alice", thread.id, role, "hello")
        assert store.list_messages("alice", thread.id) == []
    finally:
        store.close()


@pytest.mark.parametrize("role", ["user", "assistant", "system"])
def test_valid_roles_accepted(tmp_path: Path, role: str) -> None:
    store = _file_store(tmp_path)
    try:
        workspace = store.create_workspace("alice", "W")
        thread = store.create_thread("alice", workspace.id, "T")
        message = store.append_message("alice", thread.id, role, "hello")
        assert message.role == role
    finally:
        store.close()


# ------------------------------------------------------------------ #
# SQL injection strings are data, not code
# ------------------------------------------------------------------ #


def test_sql_injection_strings_treated_as_data(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    evil_owner = "alice'; DROP TABLE workspaces; --"
    evil_title = '" OR "1"="1"; DELETE FROM threads; --'
    evil_content = "'; DELETE FROM thread_messages; --"
    try:
        workspace = store.create_workspace(evil_owner, evil_title)
        thread = store.create_thread(evil_owner, workspace.id, evil_title)
        message = store.append_message(evil_owner, thread.id, "user", evil_content)

        # Tables are intact and values round-trip exactly.
        assert store.list_workspaces(evil_owner)[0].title == evil_title
        assert store.get_thread(evil_owner, thread.id).title == evil_title
        assert store.list_messages(evil_owner, thread.id)[0].content == evil_content
        assert message.content == evil_content

        # The payload owner is its own isolated scope, not alice.
        assert store.list_workspaces("alice") == []
    finally:
        store.close()
