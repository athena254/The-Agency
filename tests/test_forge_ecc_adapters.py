"""Contract tests for the generic adapters and generate-only schedule bundle."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import agency.forge.adapters as adapters_mod
from agency.forge.adapters import (
    MAX_NOTE_CHARS,
    NodeAdapter,
    NoOpAdapter,
    ProductAdapter,
    PythonAdapter,
    adapter_for,
)
from agency.forge.scheduler import (
    NO_INSTALL_GUARANTEE,
    ScheduleBundle,
    SchedulerConfig,
    generate_schedule,
)


def _project(tmp_path: Path, slug: str) -> tuple[Path, Path]:
    root = tmp_path / f"root-{slug}"
    work = tmp_path / f"work-{slug}"
    root.mkdir()
    work.mkdir()
    return root, work


def _config(**overrides: object) -> SchedulerConfig:
    values: dict[str, object] = {
        "command": "echo hi",
        "schedule": "0 3 * * *",
        "model": "unit-test",
        "service_name": "forge-test",
    }
    values.update(overrides)
    return SchedulerConfig(**values)  # type: ignore[arg-type]


def _noop_run(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("no subprocess call expected")


# --------------------------------------------------------------------------
# adapter_for: one seam, two real adapters
# --------------------------------------------------------------------------


def test_adapter_for_returns_the_two_real_adapters() -> None:
    node = adapter_for("node")
    python = adapter_for("python")
    assert isinstance(node, NodeAdapter)
    assert isinstance(python, PythonAdapter)
    assert (node.language, python.language) == ("node", "python")


def test_adapter_for_normalizes_language() -> None:
    assert isinstance(adapter_for("  NODE  "), NodeAdapter)
    assert isinstance(adapter_for("Python"), PythonAdapter)


def test_adapter_for_unknown_language_is_a_noop() -> None:
    fallback = adapter_for("go")
    assert isinstance(fallback, NoOpAdapter)
    assert fallback.prepare_dependencies(".", Path(".")) == "no adapter needed"


def test_product_adapter_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        ProductAdapter()  # type: ignore[abstract]


# --------------------------------------------------------------------------
# NodeAdapter: reuse existing node_modules, never install
# --------------------------------------------------------------------------


def test_node_adapter_uses_bounded_junction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, work = _project(tmp_path, "node")
    (root / "node_modules").mkdir()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(adapters_mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr("agency.forge.adapters.subprocess.run", fake_run)

    note = NodeAdapter().prepare_dependencies(root, work)

    assert note == "node_modules reused"
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[:4] == ["cmd", "/c", "mklink", "/J"]
    assert cmd[-2:] == [str(work / "node_modules"), str(root / "node_modules")]
    assert kwargs["timeout"] == 30
    assert kwargs.get("shell") is not True
    assert "install" not in " ".join(cmd).lower()


def test_node_adapter_symlinks_on_posix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, work = _project(tmp_path, "node-posix")
    (root / "node_modules").mkdir()
    made: list[tuple[Path, Path]] = []

    def fake_symlink(self: Path, target: Path, **_kwargs: object) -> None:
        made.append((self, Path(target)))

    monkeypatch.setattr(adapters_mod, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(Path, "symlink_to", fake_symlink)

    note = NodeAdapter().prepare_dependencies(root, work)

    assert note == "node_modules reused"
    assert made == [(work / "node_modules", root / "node_modules")]


def test_node_adapter_without_source_does_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, work = _project(tmp_path, "node-empty")
    monkeypatch.setattr("agency.forge.adapters.subprocess.run", _noop_run)
    note = NodeAdapter().prepare_dependencies(root, work)
    assert note == "no node_modules to reuse"
    assert len(note) < MAX_NOTE_CHARS


def test_node_adapter_skips_existing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, work = _project(tmp_path, "node-exists")
    (root / "node_modules").mkdir()
    (work / "node_modules").mkdir()
    monkeypatch.setattr("agency.forge.adapters.subprocess.run", _noop_run)
    assert NodeAdapter().prepare_dependencies(root, work) == "node_modules already present"


def test_node_adapter_bounds_failure_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, work = _project(tmp_path, "node-fail")
    (root / "node_modules").mkdir()
    monkeypatch.setattr(adapters_mod, "os", SimpleNamespace(name="nt"))

    def fake_failure(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stderr="x" * 500)

    monkeypatch.setattr("agency.forge.adapters.subprocess.run", fake_failure)
    note = NodeAdapter().prepare_dependencies(root, work)
    assert note.startswith("node_modules junction failed")
    assert len(note) <= MAX_NOTE_CHARS


def test_node_adapter_rejects_missing_worktree(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "node_modules").mkdir(parents=True)
    note = NodeAdapter().prepare_dependencies(root, tmp_path / "missing")
    assert note == "worktree missing"
    assert len(note) < MAX_NOTE_CHARS


# --------------------------------------------------------------------------
# PythonAdapter
# --------------------------------------------------------------------------


def test_python_adapter_exposes_src(tmp_path: Path) -> None:
    root, work = _project(tmp_path, "py")
    (work / "src").mkdir()
    note = PythonAdapter().prepare_dependencies(root, work)
    assert note == "python path prepared"
    assert len(note) < MAX_NOTE_CHARS


def test_python_adapter_without_src(tmp_path: Path) -> None:
    root, work = _project(tmp_path, "py-empty")
    assert PythonAdapter().prepare_dependencies(root, work) == "python project ready"


# --------------------------------------------------------------------------
# generate_schedule: text only, provenance, NO-INSTALL guarantee
# --------------------------------------------------------------------------


def test_generate_schedule_bundle() -> None:
    bundle = generate_schedule(_config())
    assert isinstance(bundle, ScheduleBundle)
    assert "[Service]" in bundle.service and "ExecStart=echo hi" in bundle.service
    assert "[Timer]" in bundle.timer and "OnCalendar=0 3 * * *" in bundle.timer
    assert bundle.cron_line == "0 3 * * * echo hi"
    assert isinstance(bundle.generated_at, datetime)
    assert bundle.no_install == NO_INSTALL_GUARANTEE
    assert "generate_schedule" in bundle.provenance
    assert "sha256:" in bundle.provenance
    assert bundle.caveats == ("schedule_not_applied", "capture_only", "release_not_authorized")


def test_generate_schedule_accepts_a_dict() -> None:
    bundle = generate_schedule({"command": "echo hi", "schedule": "daily", "model": "unit-test"})
    assert bundle.config.command == "echo hi"
    assert "daily" in bundle.cron_line


def test_generate_schedule_digest_is_deterministic() -> None:
    first = generate_schedule(_config())
    second = generate_schedule(_config())
    other = generate_schedule(_config(command="echo bye"))
    assert first.input_digest == second.input_digest
    assert first.input_digest != other.input_digest


def test_generate_schedule_render_includes_guarantee() -> None:
    text = generate_schedule(_config()).render()
    assert "GENERATE-ONLY" in text
    assert NO_INSTALL_GUARANTEE in text
    assert "[Service]" in text and "[Timer]" in text
    assert "0 3 * * * echo hi" in text


def test_generated_text_has_no_activation_commands() -> None:
    bundle = generate_schedule(_config())
    artifacts = bundle.service + bundle.timer + bundle.cron_line
    assert "systemctl" not in artifacts
    assert "crontab" not in artifacts
    assert "npm install" not in artifacts.lower()


def test_generate_schedule_rejects_empty_command() -> None:
    with pytest.raises(ValidationError):
        generate_schedule({"command": "", "schedule": "daily"})


def test_schedule_bundle_is_frozen() -> None:
    bundle = generate_schedule(_config())
    with pytest.raises((ValidationError, AttributeError)):
        bundle.cron_line = "changed"
