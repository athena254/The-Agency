"""Tests for ECC wave 2 runtime specs and durable apprentice promotion."""

from __future__ import annotations

import importlib
import socket
import urllib.request
from pathlib import Path

import pytest
from pydantic import ValidationError

from agency.forge.promotion_ledger import (
    MAX_PROMOTIONS,
    PromotionError,
    PromotionLedger,
)
from agency.forge.runtime_specs import (
    NODE_SPEC,
    PYTHON_SPEC,
    RuntimeSpec,
    spec_for,
)

# --- runtime specs ---------------------------------------------------------


def test_node_spec_has_real_command_and_loopback_endpoints() -> None:
    spec = spec_for("node")
    assert isinstance(spec, RuntimeSpec)
    assert spec == NODE_SPEC
    assert spec.verify_command == ("npm", "run", "start")
    assert spec.endpoints
    assert all(url.startswith("http://127.0.0.1:") for url in spec.endpoints)


def test_python_spec_has_real_command_and_loopback_endpoints() -> None:
    spec = spec_for("python")
    assert spec == PYTHON_SPEC
    assert spec.verify_command[0] == "python"
    assert spec.endpoints
    assert all(url.startswith("http://127.0.0.1:") for url in spec.endpoints)


def test_product_type_is_normalized() -> None:
    assert spec_for("  NODE  ") == NODE_SPEC
    assert spec_for("Python") == PYTHON_SPEC


def test_unknown_product_type_fails_closed() -> None:
    spec = spec_for("cobol")
    assert spec.verify_command == ()
    assert spec.endpoints == ()
    assert "unsupported_product_type" in spec.caveats


def test_import_does_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("agency.forge.runtime_specs")

    def boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("network call at import time")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    importlib.reload(module)
    assert module.spec_for("node").verify_command == NODE_SPEC.verify_command


def test_runtime_spec_is_immutable() -> None:
    with pytest.raises(ValidationError):
        NODE_SPEC.verify_command = ("changed",)


# --- promotion ledger ------------------------------------------------------


def _ledger(path: Path, *, max_records: int = MAX_PROMOTIONS) -> PromotionLedger:
    return PromotionLedger(path, max_records=max_records)


def test_record_then_is_promoted(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger.jsonl")
    record = ledger.record_promotion("Apprentice-1", approver="alice")
    assert record.apprentice == "apprentice-1"
    assert record.approver == "alice"
    assert record.at
    assert ledger.is_promoted("apprentice-1") is True
    assert ledger.is_promoted("APPRENTICE-1") is True


def test_unknown_apprentice_is_not_promoted(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger.jsonl")
    assert ledger.is_promoted("nobody") is False


@pytest.mark.parametrize(
    "approver",
    [
        "agent",
        "forge",
        "auto",
        "automatic",
        "auto-promotion",
        "system",
        "cli",
        "bot",
        "none",
        "self",
        "   ",
    ],
)
def test_generic_approver_refused(tmp_path: Path, approver: str) -> None:
    ledger = _ledger(tmp_path / "ledger.jsonl")
    with pytest.raises(PromotionError):
        ledger.record_promotion("apprentice", approver=approver)
    assert ledger.is_promoted("apprentice") is False


def test_self_approval_refused(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger.jsonl")
    with pytest.raises(PromotionError):
        ledger.record_promotion("apprentice", approver="apprentice")


def test_blank_apprentice_refused(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger.jsonl")
    with pytest.raises(PromotionError):
        ledger.record_promotion("   ", approver="alice")


def test_duplicate_promotion_refused(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = _ledger(path)
    ledger.record_promotion("apprentice", approver="alice")
    before = path.read_text(encoding="utf-8")
    with pytest.raises(PromotionError):
        ledger.record_promotion("apprentice", approver="bob")
    assert path.read_text(encoding="utf-8") == before


def test_ledger_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "ledger.jsonl"
    _ledger(path).record_promotion("apprentice", approver="alice")
    assert _ledger(path).is_promoted("apprentice") is True


def test_ledger_is_bounded_and_append_only(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = _ledger(path, max_records=2)
    ledger.record_promotion("one", approver="alice")
    ledger.record_promotion("two", approver="alice")
    before = path.read_text(encoding="utf-8")
    with pytest.raises(PromotionError):
        ledger.record_promotion("three", approver="alice")
    assert len(before.strip().splitlines()) == 2
    assert path.read_text(encoding="utf-8") == before
    assert ledger.is_promoted("three") is False


def test_note_is_bounded(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger.jsonl")
    record = ledger.record_promotion("apprentice", approver="alice", note="x" * 5000)
    assert len(record.note) <= 1000


def test_corrupt_line_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    assert _ledger(path).is_promoted("apprentice") is False
