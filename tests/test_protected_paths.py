"""Tests for Forge protected-paths enforcement."""

from __future__ import annotations

from pathlib import Path

from agency.forge.protected_paths import PROTECTED_PATHS, is_protected


def test_protected_paths_list():
    assert PROTECTED_PATHS == [".env", "secrets", "infra", "migrations"]


def test_env_file_protected():
    assert is_protected(".env")
    assert is_protected(".env.local")
    assert is_protected(".env.production")
    assert is_protected("config/.env")


def test_protected_directories():
    assert is_protected("secrets")
    assert is_protected("secrets/api.key")
    assert is_protected("infra/deploy.yml")
    assert is_protected("migrations/0001_initial.py")
    assert is_protected("/infra/x.yml")


def test_nested_protected_component():
    assert is_protected("a/b/secrets/c.key")
    assert is_protected("x/y/infra/z.yml")


def test_safe_paths():
    assert not is_protected("src/main.py")
    assert not is_protected("tests/test_foo.py")
    assert not is_protected("src/secrets_helper.py")
    assert not is_protected("README.md")


def test_path_object():
    assert is_protected(Path("infra") / "x.yml")
    assert not is_protected(Path("src") / "main.py")


def test_backslashes():
    assert is_protected(r"infra\deploy.yml")
    assert is_protected(r"secrets\api.key")
