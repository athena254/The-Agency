"""Portability tests for the typed cross-platform file-lock shim (Q-18 slice 1b)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agency.workflows.recovery import (
    _LOCK_EXCLUSIVE_NB,
    _LOCK_UNLOCK,
    RecoverableLock,
    _apply_os_lock,
)


def test_lock_mode_constants_are_typed_ints() -> None:
    assert isinstance(_LOCK_EXCLUSIVE_NB, int)
    assert isinstance(_LOCK_UNLOCK, int)
    assert _LOCK_EXCLUSIVE_NB > 0
    assert _LOCK_UNLOCK >= 0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX lock constants")
def test_posix_constants_match_fcntl() -> None:
    import fcntl

    assert _LOCK_EXCLUSIVE_NB == (fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert _LOCK_UNLOCK == fcntl.LOCK_UN


def test_apply_os_lock_is_nonblocking_and_releasable(tmp_path: Path) -> None:
    path = tmp_path / "shim.lock"
    with path.open("a+b") as first, path.open("a+b") as second:
        _apply_os_lock(first, True)
        with pytest.raises(OSError):
            _apply_os_lock(second, True)
        _apply_os_lock(first, False)
        _apply_os_lock(second, True)
        _apply_os_lock(second, False)


def test_recoverable_lock_file_created_and_released(tmp_path: Path) -> None:
    db_path = str(tmp_path / "rec.db")
    lock = RecoverableLock(db_path)
    assert lock.path == db_path + ".recover.lock"
    assert not Path(lock.path).exists()

    assert lock.acquire() is True
    assert Path(lock.path).exists()
    assert lock.acquire() is True

    contender = RecoverableLock(db_path)
    assert contender.acquire() is False

    lock.release()
    assert contender.acquire() is True
    contender.release()


def test_memory_backend_rejected() -> None:
    with pytest.raises(ValueError, match=":memory:"):
        RecoverableLock(":memory:")
