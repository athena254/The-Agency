"""Forge entry point: `forge` launches the Forge TUI.

Non-interactive subcommands delegate to the agency CLI's forge app:
`forge code <task>`, `forge tools`, `forge tool <name>`.
Use `-yolo` / `--yolo` to bypass human approvals (audited, session-only).
"""

from __future__ import annotations

import os
import sys


def _strip_yolo(argv: list[str]) -> tuple[bool, list[str]]:
    yolo = False
    rest: list[str] = []
    for arg in argv:
        if arg in ("-yolo", "--yolo"):
            yolo = True
        else:
            rest.append(arg)
    if yolo:
        os.environ["AGENCY_YOLO"] = "1"
    return yolo, rest


def main() -> None:
    yolo, argv = _strip_yolo(sys.argv[1:])
    if argv and argv[0] in ("code", "tools", "tool"):
        from agency.cli.main import app as agency_app

        agency_app(args=["forge", *argv])
        return
    if argv and argv[0] in ("--help", "-h", "help"):
        print(
            "forge — Forge TUI (full software factory)\n\nUsage: forge [-yolo] [--model M] [--repo PATH]\n       forge code <task> | forge tools | forge tool <name>\n"
        )
        return
    model: str | None = None
    repo = "."
    i = 0
    while i < len(argv):
        if argv[i] in ("--model", "-m") and i + 1 < len(argv):
            model = argv[i + 1]
            i += 2
        elif argv[i] in ("--repo", "-r") and i + 1 < len(argv):
            repo = argv[i + 1]
            i += 2
        else:
            i += 1
    from agency.tui.forge_app import run_forge_tui

    run_forge_tui(yolo=yolo, model=model, repo=repo)


if __name__ == "__main__":
    main()
