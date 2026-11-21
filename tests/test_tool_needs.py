"""Tests for the domain tool-needs catalog loader (agency.tools.needs).

The catalog is declaration data: profile name -> required/optional tool
names. Missing file is honest-empty; malformed YAML fails closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agency.tools.needs import (
    DEFAULT_NEEDS_PATH,
    ToolNeeds,
    default_requirements,
    load_tool_needs,
)

FORGE_OPTIONAL = (
    "rea",
    "ghidra",
    "jadx",
    "apktool",
    "frida",
    "ilspy",
    "radare2",
    "angr",
    "mitmproxy",
    "rizin",
)


def test_shipped_catalog_parses() -> None:
    profiles = load_tool_needs()
    assert set(profiles) == {"default", "research", "forge"}
    assert profiles["default"].required == ("memory_query", "memory_write", "utc_time")
    assert profiles["default"].optional == ("web_search", "web_fetch", "calculator")
    assert profiles["research"].required == ("web_search", "web_fetch")
    assert profiles["research"].optional == ("memory_query", "memory_write")
    assert profiles["forge"].required == ()
    assert profiles["forge"].optional == FORGE_OPTIONAL
    assert DEFAULT_NEEDS_PATH.name == "tool_needs.yaml"


def test_default_requirements_merged_sorted_deduped() -> None:
    assert default_requirements() == (
        "calculator",
        "memory_query",
        "memory_write",
        "utc_time",
        "web_fetch",
        "web_search",
    )


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_tool_needs(tmp_path / "absent.yaml") == {}


def test_custom_catalog_parses(tmp_path: Path) -> None:
    path = tmp_path / "needs.yaml"
    path.write_text(
        "profiles:\n  ops:\n    required: [memory_query]\n    optional: [calculator, calculator]\n",
        encoding="utf-8",
    )
    profiles = load_tool_needs(path)
    assert profiles["ops"] == ToolNeeds(
        name="ops", required=("memory_query",), optional=("calculator", "calculator")
    )


def test_malformed_yaml_syntax_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("profiles: [default, broken\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_tool_needs(path)


def test_non_mapping_profiles_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("profiles: [one, two]\n", encoding="utf-8")
    with pytest.raises(TypeError, match="profiles"):
        load_tool_needs(path)


def test_profile_entry_must_be_mapping(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("profiles:\n  default: just-a-string\n", encoding="utf-8")
    with pytest.raises(TypeError):
        load_tool_needs(path)


def test_non_string_tool_entries_raise(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("profiles:\n  default:\n    required: [1, 2]\n", encoding="utf-8")
    with pytest.raises(TypeError):
        load_tool_needs(path)


def test_non_list_tool_field_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("profiles:\n  default:\n    required: memory_query\n", encoding="utf-8")
    with pytest.raises(TypeError):
        load_tool_needs(path)


def test_blank_tool_entries_raise(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text('profiles:\n  default:\n    required: [""]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="nonblank"):
        load_tool_needs(path)


def test_missing_default_profile_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "nodefault.yaml"
    path.write_text("profiles:\n  other:\n    required: []\n", encoding="utf-8")
    monkeypatch.setattr("agency.tools.needs.DEFAULT_NEEDS_PATH", path)
    with pytest.raises(KeyError):
        default_requirements()


def test_entries_are_frozen() -> None:
    entry = ToolNeeds(name="default", required=("a",), optional=())
    with pytest.raises(ValidationError):
        entry.name = "other"  # type: ignore[misc]
