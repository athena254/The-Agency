import subprocess
from pathlib import Path

import pytest

from agency.forge.coding_agent import run_coding_agent


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_sequential_multiline_edits_preserve_exact_utf8_bytes(tmp_path, newline):
    repo = tmp_path / "fixture"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    (repo / "index.html").write_bytes(b"SEED")
    git("add", "index.html")
    git(
        "-c",
        "user.name=Synthetic Fixture",
        "-c",
        "user.email=fixture@localhost",
        "commit",
        "-q",
        "-m",
        "seed",
    )
    first = "<main>" + newline + "first" + newline + "</main>"
    edits = [
        {"path": "index.html", "old": "SEED", "new": first},
        {
            "path": "index.html",
            "old": "<main>" + newline + "first",
            "new": "<main>" + newline + "second",
        },
    ]
    report = run_coding_agent(
        repo, edits, branch="line-endings", worktree_root=tmp_path / "worktrees"
    )
    assert report.gate == "PASS", report.note
    assert report.edits_applied == 2
    expected = ("<main>" + newline + "second" + newline + "</main>").encode("utf-8")
    assert (Path(report.worktree_path) / "index.html").read_bytes() == expected
