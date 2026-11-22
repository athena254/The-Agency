"""Behavioral safety regressions at the direct Forge write/gate boundary.

Repositories and source contents below are disposable trusted fixtures, not a
sandbox or a provider-backed execution experiment.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agency.forge import coding_agent, run_coding_agent
from agency.forge.protected_paths import is_protected
from agency.forge.test_runner import TestReport as ForgeTestReport


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.email", "fixture@example.invalid"],
        ["config", "user.name", "Forge safety fixture"],
        ["config", "core.autocrlf", "false"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    (root / "m.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "trusted fixture"], cwd=root, check=True, capture_output=True
    )
    return root


def _edit(repo: Path, **kwargs):
    return coding_agent.run_coding_agent(
        repo, [{"path": "m.py", "old": "x = 1", "new": "x = 2"}], **kwargs
    )


def _test_report(root: Path, gate: str = "PASS", returncode: int | None = 0):
    return ForgeTestReport(
        report_id="fixture-report",
        created_at=datetime.now(UTC),
        root=str(root),
        workdir=str(root),
        command=("pytest",),
        gate=gate,
        returncode=returncode,
        duration_s=0,
    )


@pytest.mark.parametrize("outcome", ["exception", "timeout_exception", "missing", "malformed"])
def test_requested_tests_without_valid_report_fail_closed(repo: Path, monkeypatch, outcome: str):
    calls = []

    def unavailable(root, paths, *, timeout_s):
        calls.append((root, paths, timeout_s))
        if outcome == "exception":
            raise RuntimeError("fixture runner unavailable")
        if outcome == "timeout_exception":
            raise subprocess.TimeoutExpired("fixture-pytest", timeout_s)
        if outcome == "malformed":
            return {"gate": "PASS"}
        return None

    monkeypatch.setattr(coding_agent, "run_tests", unavailable)
    report = _edit(repo, run_tests_flag=True, test_paths=["test_fixture.py"], test_timeout_s=7)
    assert len(calls) == 1
    assert calls[0][1:] == (["test_fixture.py"], 7)
    assert report.inspection.gate == report.security.gate == "PASS"
    assert report.edits_applied == 1
    assert report.gate == "FAIL"
    assert report.tests is None
    assert "tests_not_run" in report.caveats
    assert (repo / "m.py").read_text(encoding="utf-8") == "x = 1\n"


@pytest.mark.parametrize(
    ("gate", "returncode"),
    [("FAIL", 1), ("ERROR", None), ("TIMEOUT", None), ("PASS", 1), ("PASS", None)],
)
def test_requested_tests_require_successful_exit(repo: Path, monkeypatch, gate, returncode):
    result = _test_report(repo, gate, returncode)
    monkeypatch.setattr(coding_agent, "run_tests", lambda *args, **kwargs: result)
    report = _edit(repo, run_tests_flag=True)
    assert report.inspection.gate == report.security.gate == "PASS"
    assert report.tests == result
    assert report.gate == "FAIL"


def test_successful_required_tests_can_pass(repo: Path, monkeypatch):
    result = _test_report(repo)
    monkeypatch.setattr(coding_agent, "run_tests", lambda *args, **kwargs: result)
    report = _edit(repo, run_tests_flag=True)
    assert report.gate == "PASS"
    assert report.tests == result
    assert "tests_not_run" not in report.caveats


@pytest.mark.parametrize("representation", ["construct", "copy", "dict"])
@pytest.mark.parametrize("raw_exit", [False, "0", 0.0, True, "1", 1.0])
def test_complete_malformed_exit_evidence_cannot_pass(
    repo: Path, monkeypatch, representation, raw_exit
):
    valid = _test_report(repo)
    fields = valid.model_dump()
    fields["returncode"] = raw_exit
    if representation == "construct":
        result = ForgeTestReport.model_construct(**fields)
    elif representation == "copy":
        result = valid.model_copy(update={"returncode": raw_exit})
    else:
        result = fields
    monkeypatch.setattr(coding_agent, "run_tests", lambda *args, **kwargs: result)
    report = _edit(repo, run_tests_flag=True)
    assert report.edits_applied == 1
    assert report.inspection.gate == report.security.gate == "PASS"
    assert report.gate == "FAIL"
    assert report.tests is None
    assert "tests_unavailable" in report.note


@pytest.mark.parametrize("representation", ["construct", "copy", "dict"])
def test_complete_integer_zero_evidence_can_pass(repo: Path, monkeypatch, representation):
    valid = _test_report(repo)
    fields = valid.model_dump()
    if representation == "construct":
        result = ForgeTestReport.model_construct(**fields)
    elif representation == "copy":
        result = valid.model_copy(update={"returncode": 0})
    else:
        result = fields
    monkeypatch.setattr(coding_agent, "run_tests", lambda *args, **kwargs: result)
    report = _edit(repo, run_tests_flag=True)
    assert report.gate == "PASS"
    assert report.tests is not None
    assert type(report.tests.returncode) is int
    assert report.tests.returncode == 0


def test_optional_tests_not_requested_preserve_allowed_edit(repo: Path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("optional test runner must not be called")

    monkeypatch.setattr(coding_agent, "run_tests", unexpected)
    report = _edit(repo)
    assert report.gate == "PASS"
    assert report.tests is None
    assert "tests_not_run" in report.caveats
    assert (Path(report.worktree_path) / "m.py").read_text(encoding="utf-8") == "x = 2\n"
    assert (repo / "m.py").read_text(encoding="utf-8") == "x = 1\n"


@pytest.mark.parametrize(
    "entrypoint",
    [run_coding_agent, coding_agent.apply_edits, coding_agent.run_agent, coding_agent.code],
    ids=["package_export", "apply_edits", "run_agent", "code"],
)
@pytest.mark.parametrize("protected", [".env", "infra/deploy.yml"])
def test_direct_aliases_refuse_entire_protected_batch_before_worktree(
    repo: Path, monkeypatch, entrypoint, protected: str
):
    target = repo / protected
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("value = 1\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "protected fixture"], cwd=repo, check=True, capture_output=True
    )
    before = {"m.py": (repo / "m.py").read_bytes(), protected: target.read_bytes()}
    calls = []
    create = coding_agent.create_worktree

    def observe(*args, **kwargs):
        calls.append(True)
        return create(*args, **kwargs)

    monkeypatch.setattr(coding_agent, "create_worktree", observe)
    with pytest.raises(ValueError, match="protected"):
        entrypoint(
            repo,
            [
                {"path": "m.py", "old": "x = 1", "new": "x = 2"},
                {"path": protected, "old": "value = 1", "new": "value = 2"},
            ],
        )
    assert calls == []
    assert not (repo.parent / "repo-worktrees").exists()
    assert {path: (repo / path).read_bytes() for path in before} == before


@pytest.mark.parametrize(
    "entrypoint",
    [run_coding_agent, coding_agent.apply_edits, coding_agent.run_agent, coding_agent.code],
    ids=["package_export", "apply_edits", "run_agent", "code"],
)
@pytest.mark.parametrize(
    "path",
    [
        ".env::$DATA",
        ".gitlab-ci.yml::$DATA",
        "docs/SPEC_REVIEW.md::$DATA",
        "MIGRAT~1/value.py",
        "src/allowed.py:notes",
        "src/notes~draft.py",
    ],
)
def test_unsupported_alias_batch_refused_before_any_worktree_effect(
    tmp_path: Path, monkeypatch, entrypoint, path: str
):
    safe = tmp_path / "safe.py"
    safe.write_bytes(b"x = 1\n")
    calls = []

    def unexpected(*args, **kwargs):
        calls.append(True)
        pytest.fail("unsupported raw alias reached worktree creation")

    monkeypatch.setattr(coding_agent, "create_worktree", unexpected)
    with pytest.raises(ValueError, match="protected"):
        entrypoint(
            tmp_path,
            [
                {"path": "safe.py", "old": "x = 1", "new": "x = 2"},
                {"path": path, "old": "x = 1", "new": "x = 2"},
            ],
        )
    assert calls == []
    assert safe.read_bytes() == b"x = 1\n"
    assert list(tmp_path.iterdir()) == [safe]
    assert not (tmp_path.parent / f"{tmp_path.name}-worktrees").exists()
    assert is_protected(path)


@pytest.mark.skipif(os.name != "nt", reason="requires actual Windows alias resolution")
@pytest.mark.parametrize("kind", ["env_stream", "ci_stream", "spec_stream", "short_directory"])
def test_retained_windows_alias_cannot_change_ordinary_protected_bytes(
    tmp_path: Path, monkeypatch, kind: str, request
):
    root = tmp_path / "trusted-worktree"
    root.mkdir()
    safe = root / "safe.py"
    safe.write_bytes(b"x = 1\n")
    ordinary_path, alias = {
        "env_stream": (".env", ".env::$DATA"),
        "ci_stream": (".gitlab-ci.yml", ".gitlab-ci.yml::$DATA"),
        "spec_stream": ("docs/SPEC_REVIEW.md", "docs/SPEC_REVIEW.md::$DATA"),
        "short_directory": ("migrations/value.py", None),
    }[kind]
    target = root / ordinary_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x = 1\n")
    if alias is None:
        import ctypes
        from ctypes import wintypes

        get_short = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_short(str(target.parent), buffer, len(buffer))
        assert 0 < length < len(buffer), f"alias discovery failed: {ctypes.get_last_error()}"
        component = Path(buffer.value).name
        if "~" not in component:
            pytest.skip("owned migrations fixture has no generated 8.3 alias on this volume")
        alias = f"{component}/value.py"
    request.node.user_properties.append(("retained_alias", alias))
    assert (root / alias).read_bytes() == target.read_bytes()
    calls = []

    def boundary_worktree(*args, **kwargs):
        calls.append(True)
        return coding_agent.WorktreeInfo(worktree_path=str(root), branch="fixture", base_ref="HEAD")

    monkeypatch.setattr(coding_agent, "create_worktree", boundary_worktree)
    with pytest.raises(ValueError, match="protected"):
        run_coding_agent(
            root,
            [
                {"path": "safe.py", "old": "x = 1", "new": "x = 2"},
                {"path": alias, "old": "x = 1", "new": "x = 2"},
            ],
        )
    assert calls == []
    assert safe.read_bytes() == b"x = 1\n"
    assert target.read_bytes() == b"x = 1\n"


NEW_PROTECTED_PATHS = [
    ".github/workflows/check.yml",
    r".GITHUB\WORKFLOWS\check.YML",
    ".gitlab-ci.yml",
    ".travis.yml",
    ".circleci/config.yml",
    ".buildkite/pipeline.yml",
    "azure-pipelines.yml",
    "Jenkinsfile",
    "bitbucket-pipelines.yml",
    "appveyor.yml",
    ".drone.yml",
    "docs/SPEC.md",
    "docs/SPEC_FACTORY.md",
    r"DOCS\spec_factory.MD",
    r"InFrA\deploy.yml",
    "nested/Secrets/key.txt",
    "MIGRATIONS/001.py",
    "config/.ENV.LOCAL",
    "infra./deploy.yml",
    "docs/SPEC_FACTORY.md.",
]


@pytest.mark.parametrize("path", NEW_PROTECTED_PATHS)
def test_protected_policy_covers_ci_specs_and_windows_spellings(path: str):
    assert is_protected(path)


@pytest.mark.parametrize("path", NEW_PROTECTED_PATHS)
def test_new_protected_categories_refuse_before_worktree(tmp_path: Path, monkeypatch, path: str):
    calls = []

    def unexpected(*args, **kwargs):
        calls.append(True)
        pytest.fail("protected batch reached worktree creation")

    monkeypatch.setattr(coding_agent, "create_worktree", unexpected)
    with pytest.raises(ValueError, match="protected"):
        run_coding_agent(
            tmp_path,
            [{"path": "safe.py", "old": "a", "new": "b"}, {"path": path, "old": "a", "new": "b"}],
        )
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("inside", [True, False], ids=["internal_protected", "external_escape"])
def test_reparse_directory_cannot_redirect_direct_edit(repo: Path, monkeypatch, inside: bool):
    create = coding_agent.create_worktree
    targets = []

    def with_reparse(*args, **kwargs):
        info = create(*args, **kwargs)
        root = Path(info.worktree_path)
        target = root / "infra" if inside else repo.parent / "outside"
        target.mkdir()
        source = target / "value.py"
        source.write_text("x = 1\n", encoding="utf-8", newline="\n")
        targets.append(source)
        link = root / "alias"
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        else:
            link.symlink_to(target, target_is_directory=True)
        return info

    monkeypatch.setattr(coding_agent, "create_worktree", with_reparse)
    report = run_coding_agent(repo, [{"path": "alias/value.py", "old": "x = 1", "new": "x = 2"}])
    assert report.gate == "FAIL"
    assert report.edits_applied == 0
    assert len(targets) == 1
    assert targets[0].read_bytes() == b"x = 1\n"


def test_unvalidated_test_report_instance_cannot_pass(repo: Path, monkeypatch):
    invalid = ForgeTestReport.model_construct(gate="PASS", returncode=0)
    monkeypatch.setattr(coding_agent, "run_tests", lambda *args, **kwargs: invalid)
    report = _edit(repo, run_tests_flag=True)
    assert report.gate == "FAIL"
    assert report.tests is None
    assert "tests_unavailable" in report.note


@pytest.mark.parametrize(
    ("raw_path", "reason"),
    [
        ("infra/../m.py", "path_traversal_not_allowed"),
        (r"infra\..\m.py", "path_traversal_not_allowed"),
        ("../m.py", "path_traversal_not_allowed"),
        (r"C:\infra\deploy.yml", "absolute_path_not_allowed"),
        (r"\\server\share\infra\deploy.yml", "absolute_path_not_allowed"),
        ("/infra/deploy.yml", "absolute_path_not_allowed"),
        ("./m.py", "dot_prefix_not_allowed"),
        ("infra/../.env::$DATA", "path_traversal_not_allowed"),
        (r"MIGRAT~1\..\m.py", "path_traversal_not_allowed"),
        ("./MIGRAT~1/value.py", "dot_prefix_not_allowed"),
        (r"\\server\share\MIGRAT~1\value.py", "absolute_path_not_allowed"),
    ],
)
def test_original_name_guards_precede_policy_normalization(repo: Path, raw_path, reason):
    report = run_coding_agent(repo, [{"path": raw_path, "old": "x = 1", "new": "x = 2"}])
    assert report.gate == "FAIL"
    assert report.edits_applied == 0
    assert f"failure={reason}" in report.note
    assert (repo / "m.py").read_bytes() == b"x = 1\n"


@pytest.mark.parametrize("edit", [{"path": "m.py"}, ["m.py"], object()])
def test_invalid_edit_shapes_still_fail_closed(repo: Path, edit):
    report = run_coding_agent(repo, [edit])
    assert report.gate == "FAIL"
    assert report.edits_applied == 0
    assert "failure=invalid_edit_shape" in report.note


@pytest.mark.parametrize(
    "path",
    [
        "src/secrets_helper.py",
        "docs/guide.md",
        "docs/spec_helper.py",
        "src/SPEC.md",
        ".github/ISSUE_TEMPLATE/bug.md",
        "workflows/check.py",
    ],
)
def test_similarly_named_unprotected_paths_remain_allowed(repo: Path, path: str):
    assert not is_protected(path)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "safe fixture"], cwd=repo, check=True, capture_output=True
    )
    report = run_coding_agent(repo, [{"path": path.replace("/", "\\"), "old": "1", "new": "2"}])
    assert report.gate == "PASS"
    assert report.edits_applied == 1
    assert (Path(report.worktree_path) / path).read_text(encoding="utf-8") == "x = 2\n"
    assert target.read_bytes() == b"x = 1\n"


@pytest.mark.parametrize(
    ("contents", "timeout", "gate", "returncode"),
    [
        ("def test_fixture():\n    assert True\n", 30, "PASS", 0),
        ("def test_fixture():\n    assert False\n", 30, "FAIL", 1),
        ("# no tests collected\n", 30, "FAIL", 5),
        ("import time\ndef test_fixture():\n    time.sleep(60)\n", 1, "TIMEOUT", None),
    ],
    ids=["pass", "fail", "no_tests", "timeout"],
)
def test_real_required_pytest_outcomes(repo: Path, contents, timeout, gate, returncode):
    (repo / "test_fixture.py").write_text(contents, encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "test fixture"], cwd=repo, check=True, capture_output=True
    )
    report = _edit(
        repo, run_tests_flag=True, test_paths=["test_fixture.py"], test_timeout_s=timeout
    )
    assert report.tests is not None
    assert report.tests.gate == gate
    assert report.tests.returncode == returncode
    assert report.gate == ("PASS" if gate == "PASS" else "FAIL")
    assert (repo / "m.py").read_bytes() == b"x = 1\n"


def test_syntax_failure_still_blocks_overall_pass(repo: Path):
    report = run_coding_agent(repo, [{"path": "m.py", "old": "x = 1", "new": "x ="}])
    assert report.inspection.gate == "FAIL"
    assert report.gate == "FAIL"
