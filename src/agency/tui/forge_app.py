# ruff: noqa: BLE001, S110
"""Forge TUI — full software factory. Intake -> spec-minimum -> build -> prove -> ship."""

from __future__ import annotations

from pathlib import Path

from agency.forge.inspection import inspect_changes
from agency.forge.pipeline import run_pipeline
from agency.forge.protected_paths import is_protected
from agency.tui.base import BaseTUI
from agency.tui.spec_rules import check_spec, draft_from_text, render_spec_md


class ForgeApp(BaseTUI):
    TITLE = "Forge"
    SUB_TITLE = "software factory — spec-gated build loop"

    async def show_status(self) -> None:
        self.say(f"mode={self.policy.mode.value} brain={self.brain.describe()} repo={self.repo}")

    async def handle_slash(self, text: str) -> None:
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        if cmd == "/tools":
            from agency.forge.tool_catalog import list_tools

            rows = "; ".join(f"{t.name}({t.command or '-'})" for t in list_tools())
            self.say(f"tools: {rows}")
        elif cmd == "/inspect":
            await self._inspect()
        elif cmd == "/pipeline":
            await self._pipeline()
        elif cmd == "/spec":
            arg = parts[1].strip() if len(parts) > 1 else ""
            await self._intake(arg or self.draft_text)
        else:
            await super().handle_slash(text)

    async def handle_message(self, text: str) -> None:
        await self._intake(text)

    async def _intake(self, text: str) -> None:
        self.draft_text = text
        draft = draft_from_text(text, repo=self.repo)
        result = check_spec(draft)
        self.say(
            f"spec check: {result.gate} type={result.system_type.value} missing={list(result.missing)}"
        )
        if not result.passed:
            self.say("[bold yellow]Not enough to build. Answer these:[/]")
            for q in result.questions:
                self.say(f"  - {q}")
            self.say(
                "[dim]Reply with the missing detail, e.g. '/spec <request> scope:... acceptance:...'[/]"
            )
            # Best-effort LLM elaboration (never replaces the deterministic check)
            try:
                reply = await self.brain.ask(
                    f"Turn this into a minimal spec request, do not invent facts: {text}\n"
                    f"Missing: {', '.join(result.missing)}"
                )
                self.say(f"[dim]llm suggests: {reply[:400]}[/]")
            except Exception:
                pass
            return
        self.spec_passed = True
        self.say("[bold green]spec minimum met[/]")
        spec_path = Path(self.repo) / ".forge-spec.md"
        try:
            spec_path.write_text(render_spec_md(draft), encoding="utf-8")
            self.say(f"wrote {spec_path}")
        except OSError as exc:
            self.say(f"[red]could not write spec: {exc}[/]")
        await self._pipeline()

    def _changed(self) -> list[str]:
        try:
            import subprocess

            out = subprocess.run(
                ["git", "status", "--porcelain"],
                check=False,
                cwd=self.repo,
                capture_output=True,
                text=True,
                timeout=10,
            )
            paths = []
            for line in out.stdout.splitlines():
                p = line[3:].strip()
                if p and not p.startswith(".forge-spec"):
                    paths.append(p.replace("\\", "/"))
            return paths[:64]
        except Exception:
            return []

    async def _inspect(self) -> None:
        changed = self._changed()
        if not changed:
            self.say("[yellow]no changed files (git status clean)[/]")
            return
        protected = [p for p in changed if is_protected(p)]
        if protected and not self.policy.yolo:
            self.say(f"[red]protected paths need approval: {protected}[/]")
            return
        report = inspect_changes(Path(self.repo).resolve(), changed)
        self.say(
            f"inspect gate={report.gate} files={len(report.files)} rejected={len(report.rejected)}"
        )

    async def _pipeline(self) -> None:
        changed = self._changed()
        if not changed:
            self.say("[yellow]no changed files to pipeline (git status clean)[/]")
            return
        protected = [p for p in changed if is_protected(p)]
        if protected and not self.policy.yolo:
            self.say(f"[red]protected paths need approval: {protected}[/]")
            return
        report = run_pipeline(Path(self.repo).resolve(), changed, run_tests_flag=False)
        self.say(
            f"pipeline gate={report.gate} inspect={report.inspection.gate} security={report.security.gate}"
        )

    def help_text(self) -> str:
        return super().help_text() + "\n/spec <request> /inspect /pipeline /tools /status"


def run_forge_tui(
    *,
    yolo: bool = False,
    model: str | None = None,
    repo: str = ".",
    server_url: str = "http://127.0.0.1:8000",
) -> None:
    ForgeApp(yolo=yolo, model=model, repo=repo, server_url=server_url).run()
