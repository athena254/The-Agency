"""Tests for the manifest-based tool import flow (Phase 4a of tool provisioning).

``import_tool`` runs the fail-closed pipeline: parse manifest -> verify the
pinned content hash over the fetched bytes -> Forge gates (real inspection +
security scan + pytest via ``build_tool``, verification pass) -> reviewer
verdict (fake reviewer; no network, no real scanner) -> idempotent
registration with provenance metadata. Fetch is always injected; the default
``fetch=None`` must refuse loudly (no network code lives in this module).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agency.tools.importing import compute_content_hash, import_tool
from agency.tools.registry import ToolRegistry
from agency.tools.reviewer import ToolReviewVerdict

TOOL_PY = """\
def run(args, ctx):
    return {"result": int(args.get("a", 0)) + int(args.get("b", 0))}
"""

TEST_PY = """\
import tool


def test_add():
    assert tool.run({"a": 2, "b": 3}, None) == {"result": 5}
"""

UNSAFE_TOOL_PY = """\
def run(args, ctx):
    return eval(args["x"])
"""

SOURCE = "https://github.com/org/repo"
COMMIT = "ab" * 20


def _v(verdict: str, *, score: int = 95, summary: str = "clean") -> ToolReviewVerdict:
    return ToolReviewVerdict(
        scanner="fake",
        score=score,
        verdict=verdict,
        findings_summary=summary,
        report_json={},
    )


class FakeReviewer:
    """Scripted ToolReviewer double: queued verdicts, records scanned trees.

    Like a real reviewer, it reads the tree *during* review(); the import
    flow cleans its quarantine temp dir afterwards.
    """

    def __init__(self, verdicts: list[ToolReviewVerdict] | None = None) -> None:
        self.verdicts = list(verdicts) if verdicts is not None else [_v("PASS")]
        self.scanned: list[Path] = []
        self.trees: list[list[str]] = []

    def is_available(self) -> bool:
        return True

    def review(self, path: Path) -> ToolReviewVerdict:
        self.scanned.append(Path(path))
        self.trees.append(sorted(child.name for child in Path(path).iterdir()))
        if self.verdicts:
            return self.verdicts.pop(0)
        return _v("PASS")


def _manifest_data(
    *,
    name: str = "demo-pack",
    version: str = "1.0.0",
    tool_py: str = TOOL_PY,
    test_py: str = TEST_PY,
    extra_tools: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    files = {"tool.py": tool_py.encode("utf-8"), "test_tool.py": test_py.encode("utf-8")}
    data: dict[str, Any] = {
        "name": name,
        "version": version,
        "description": "Demo imported tool package.",
        "provenance": {"source": SOURCE, "commit_sha": COMMIT, "content_hash": ""},
        "tools": [
            {
                "name": "mini_add",
                "description": "Adds two integers.",
                "input_schema": {"type": "object"},
                "risk": "read",
            },
            *(extra_tools or []),
        ],
    }
    data["provenance"]["content_hash"] = compute_content_hash(data, files)
    return data, files


def _fetch(
    data: dict[str, Any],
    files: dict[str, bytes],
    *,
    manifest_bytes: bytes | None = None,
    extra_files: dict[str, bytes] | None = None,
):
    def fetch(source_url: str, commit_sha: str) -> dict[str, bytes]:
        assert source_url == data["provenance"]["source"]
        assert commit_sha == data["provenance"]["commit_sha"]
        out = dict(files)
        out["manifest.json"] = (
            manifest_bytes if manifest_bytes is not None else json.dumps(data).encode("utf-8")
        )
        out.update(extra_files or {})
        return out

    return fetch


def _manifest_path(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _import(tmp_path: Path, data: dict[str, Any], files: dict[str, bytes], **kw: Any):
    return import_tool(
        _manifest_path(tmp_path, data),
        reviewer=kw.pop("reviewer", FakeReviewer()),
        fetch=_fetch(
            data, files, **{k: v for k, v in kw.items() if k in ("manifest_bytes", "extra_files")}
        ),
        **{k: v for k, v in kw.items() if k not in ("manifest_bytes", "extra_files")},
    )


# --- contract -----------------------------------------------------------------


def test_fetch_adapter_is_required(tmp_path: Path) -> None:
    data, _ = _manifest_data()
    with pytest.raises(ValueError, match="network fetch adapter required"):
        import_tool(_manifest_path(tmp_path, data), reviewer=FakeReviewer(), fetch=None)


def test_reviewer_is_required(tmp_path: Path) -> None:
    data, files = _manifest_data()
    with pytest.raises(TypeError, match="reviewer"):
        import_tool(_manifest_path(tmp_path, data), reviewer=None, fetch=_fetch(data, files))


# --- happy path ---------------------------------------------------------------


def test_registered_with_provenance(tmp_path: Path) -> None:
    data, files = _manifest_data()
    registry = ToolRegistry()
    reviewer = FakeReviewer()
    result = _import(tmp_path, data, files, reviewer=reviewer, registry=registry)
    assert result.state == "REGISTERED"
    assert result.name == "demo-pack"
    assert result.review is not None and result.review.verdict == "PASS"
    assert len(result.report_ids) >= 1
    assert [s.name for s in registry.list_specs()] == ["mini_add"]
    tool = registry.get("mini_add")
    provenance = tool.import_provenance
    assert provenance["source"] == SOURCE
    assert provenance["commit_sha"] == COMMIT
    assert provenance["content_hash"] == data["provenance"]["content_hash"]
    assert provenance["review"]["verdict"] == "PASS"
    assert provenance["entry"]["description"] == "Adds two integers."


def test_reviewer_receives_written_quarantine_tree(tmp_path: Path) -> None:
    data, files = _manifest_data()
    reviewer = FakeReviewer()
    _import(tmp_path, data, files, reviewer=reviewer)
    assert len(reviewer.scanned) == 1
    assert reviewer.trees[0] == ["manifest.json", "test_tool.py", "tool.py"]


def test_verified_without_registry_reports_honestly(tmp_path: Path) -> None:
    data, files = _manifest_data()
    result = _import(tmp_path, data, files, registry=None)
    assert result.state == "REGISTERED"
    assert "no registry" in result.detail
    assert result.report_ids


def test_multi_entry_manifest_registers_every_entry(tmp_path: Path) -> None:
    second = {
        "name": "beta_tool",
        "description": "Second entry sharing the package module.",
        "input_schema": {"type": "object"},
        "risk": "read",
    }
    data, files = _manifest_data(extra_tools=[second])
    registry = ToolRegistry()
    result = _import(tmp_path, data, files, registry=registry)
    assert result.state == "REGISTERED"
    assert {s.name for s in registry.list_specs()} == {"mini_add", "beta_tool"}


# --- hash gate ----------------------------------------------------------------


def test_hash_mismatch_fails_before_gates(tmp_path: Path) -> None:
    data, files = _manifest_data()
    tampered = {
        "tool.py": files["tool.py"] + b"# rug pull\n",
        "test_tool.py": files["test_tool.py"],
    }
    registry = ToolRegistry()
    reviewer = FakeReviewer()
    result = _import(tmp_path, data, tampered, reviewer=reviewer, registry=registry)
    assert result.state == "HASH_MISMATCH"
    assert result.report_ids == ()
    assert result.review is None
    assert reviewer.scanned == []  # gates and review never ran
    assert registry.list_specs() == []


def test_fetched_manifest_disagreement_fails_closed(tmp_path: Path) -> None:
    data, files = _manifest_data()
    lying = json.loads(json.dumps(data))
    lying["description"] = "Totally different package."
    registry = ToolRegistry()
    result = _import(
        tmp_path, data, files, manifest_bytes=json.dumps(lying).encode("utf-8"), registry=registry
    )
    assert result.state == "HASH_MISMATCH"
    assert registry.list_specs() == []


def test_fetch_shape_is_enforced(tmp_path: Path) -> None:
    data, files = _manifest_data()
    with pytest.raises(ValueError, match="tool.py"):
        _import(tmp_path, data, {"tool.py": files["tool.py"]})
    with pytest.raises(ValueError, match="unexpected"):
        _import(tmp_path, data, {**files, "setup.py": b"x = 1\n"})


# --- forge gate ----------------------------------------------------------------


def test_gate_failure_quarantines_and_skips_review(tmp_path: Path) -> None:
    data, files = _manifest_data(
        tool_py=UNSAFE_TOOL_PY, test_py="import tool\n\n\ndef test_ok():\n    assert True\n"
    )
    registry = ToolRegistry()
    reviewer = FakeReviewer()
    result = _import(tmp_path, data, files, reviewer=reviewer, registry=registry)
    assert result.state == "GATE_FAILED"
    assert result.report_ids  # evidence exists
    assert result.review is None
    assert reviewer.scanned == []
    assert registry.list_specs() == []
    assert "gate" in result.detail.lower()


# --- review gate ---------------------------------------------------------------


def test_review_fail_keeps_tool_unregistered(tmp_path: Path) -> None:
    data, files = _manifest_data()
    registry = ToolRegistry()
    reviewer = FakeReviewer([_v("FAIL", score=30, summary="os.system use in tool.py")])
    result = _import(tmp_path, data, files, reviewer=reviewer, registry=registry)
    assert result.state == "REVIEW_FAILED"
    assert "os.system use in tool.py" in result.detail
    assert result.review is not None and result.review.verdict == "FAIL"
    assert result.report_ids  # gate evidence retained
    assert registry.list_specs() == []


def test_reviewer_unavailable_quarantines_retriably(tmp_path: Path) -> None:
    data, files = _manifest_data()
    registry = ToolRegistry()
    reviewer = FakeReviewer([_v("UNAVAILABLE", score=0, summary="skillspector not found on PATH")])
    result = _import(tmp_path, data, files, reviewer=reviewer, registry=registry)
    assert result.state == "REVIEWER_UNAVAILABLE"
    assert result.review is not None and result.review.verdict == "UNAVAILABLE"
    assert registry.list_specs() == []
    assert "quarantin" in result.detail.lower()


# --- re-vetting ----------------------------------------------------------------


def test_reimport_same_version_reruns_everything_idempotently(tmp_path: Path) -> None:
    data, files = _manifest_data()
    registry = ToolRegistry()
    reviewer = FakeReviewer()
    first = _import(tmp_path, data, files, reviewer=reviewer, registry=registry)
    second = _import(tmp_path, data, files, reviewer=reviewer, registry=registry)
    assert first.state == "REGISTERED"
    assert second.state == "REGISTERED"
    assert len(reviewer.scanned) == 2  # the whole flow re-ran
    assert [s.name for s in registry.list_specs()] == ["mini_add"]  # no duplicate
    assert "idempotent" in second.detail


def test_new_version_with_new_code_reruns_gates_and_review(tmp_path: Path) -> None:
    data, files = _manifest_data()
    registry = ToolRegistry()
    reviewer = FakeReviewer()
    assert (
        _import(tmp_path, data, files, reviewer=reviewer, registry=registry).state == "REGISTERED"
    )
    new_tool = TOOL_PY.replace(
        'return {"result": int(args.get("a", 0)) + int(args.get("b", 0))}',
        'return {"result": int(args.get("a", 0)) - int(args.get("b", 0))}',
    )
    new_test = TEST_PY.replace('== {"result": 5}', '== {"result": -1}')
    data2, files2 = _manifest_data(version="1.0.1", tool_py=new_tool, test_py=new_test)
    result = _import(tmp_path, data2, files2, reviewer=reviewer, registry=registry)
    assert result.state == "REGISTERED"
    assert len(reviewer.scanned) == 2  # re-vetted: never a shortcut
    assert [s.name for s in registry.list_specs()] == ["mini_add"]
    tool = registry.get("mini_add")
    assert tool.import_provenance["manifest_version"] == "1.0.1"
