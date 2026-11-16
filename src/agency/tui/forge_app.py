# ruff: noqa: BLE001, S110
"""Forge TUI — full software factory. Intake -> spec-minimum -> build -> prove -> ship.

The build step is deliberately a plain callable (:func:`build_from_spec`) so tests
can drive the TUI with a fake agent and a fake pipeline runner. No real agent is
ever launched from a test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from agency.forge.external_agent import run_external_agent
from agency.forge.inspection import inspect_changes
from agency.forge.pipeline import run_pipeline
from agency.forge.protected_paths import is_protected, protected_paths
from agency.forge.worktree import WorktreeError, remove_worktree
from agency.tui.base import BaseTUI
from agency.tui.spec_rules import check_spec, draft_from_text, render_spec_md

DEFAULT_BUILD_MODEL = "opencode/beam-free"
BUILD_BRANCH = "forge-tui-build"
FORGE_BUILD_PROTECTED_PATHS: tuple[str, ...] = (*protected_paths(), "uv.lock")

_NO_MERGE_NOTE = (
    "Forge build: isolated agent + gated pipeline. "
    "Does not auto-merge, does not authorize release, never edits the original checkout."
)


class _AgentReport(Protocol):
    """The slice of an agent report the build step depends on."""

    @property
    def gate(self) -> str: ...

    @property
    def changed_paths(self) -> tuple[str, ...]: ...

    @property
    def worktree_path(self) -> str: ...

    @property
    def isolation_violations(self) -> tuple[str, ...]: ...

    @property
    def main_repo_changes(self) -> tuple[str, ...]: ...


class _PipelineReport(Protocol):
    """The slice of a pipeline report the build step depends on."""

    @property
    def gate(self) -> str: ...


AgentRunner = Any
PipelineRunner = Any


class BuildResult(BaseModel):
    """Verdict of one spec-gated, isolated build. Never authorizes merge/release."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: str
    agent_gate: str
    pipeline_gate: str
    inspection_gate: str
    security_gate: str
    tests_gate: str | None
    changed_paths: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()
    isolation_violations: tuple[str, ...] = ()
    worktree_path: str = ""
    note: str = _NO_MERGE_NOTE


def _gate_of(report: Any) -> str:
    return str(getattr(report, "gate", "FAIL"))


def _quiet_remove_worktree(repo: str | Path, worktree: str) -> None:
    if not worktree:
        return
    try:
        remove_worktree(repo, worktree)
    except WorktreeError:
        pass


def build_from_spec(
    repo_root: str | Path,
    spec_text: str,
    *,
    agent_runner: AgentRunner = run_external_agent,
    pipeline_runner: PipelineRunner = run_pipeline,
    worktree_remover: Any = _quiet_remove_worktree,
    model: str = DEFAULT_BUILD_MODEL,
    run_tests_flag: bool = True,
) -> BuildResult:
    """Build *spec_text* in an isolated worktree, then verify with the real Forge pipeline.

    ``agent_runner`` is injectable so tests substitute a fake; the production
    default is ``run_external_agent`` — the same entry point the ``forge code``
    CLI uses. The agent only writes inside its worktree; the pipeline runs there
    with ``run_tests_flag=True`` and the original checkout is never touched.
    The isolated worktree is removed in a ``finally`` block.

    The agent's own internal pipeline pass is asked not to run the LLM review:
    the verification pipeline below runs it once, on the final artifact. Running
    it in both places would double the LLM review cost for one build.
    """
    repo = Path(repo_root)
    command = ["opencode", "run", "--auto", "--dir", "{workdir}", "--model", model]

    agent_report: _AgentReport = agent_runner(
        repo,
        command,
        spec_text,
        branch=BUILD_BRANCH,
        run_tests_flag=False,
        run_review_flag=False,
        cleanup=False,
        protected_paths=FORGE_BUILD_PROTECTED_PATHS,
    )
    changed = tuple(agent_report.changed_paths)
    worktree_root = agent_report.worktree_path
    protected_hits = tuple(p for p in changed if is_protected(p))
    isolation = tuple(agent_report.isolation_violations) + tuple(agent_report.main_repo_changes)

    try:
        pipeline_report: _PipelineReport = pipeline_runner(
            worktree_root,
            list(changed),
            run_tests_flag=run_tests_flag,
        )
    finally:
        worktree_remover(repo, worktree_root)

    inspection = getattr(pipeline_report, "inspection", None)
    security = getattr(pipeline_report, "security", None)
    tests = getattr(pipeline_report, "tests", None)

    gate = "PASS"
    if _gate_of(agent_report) != "PASS":
        gate = "FAIL"
    if _gate_of(pipeline_report) != "PASS":
        gate = "FAIL"
    if protected_hits or isolation:
        gate = "FAIL"

    return BuildResult(
        gate=gate,
        agent_gate=_gate_of(agent_report),
        pipeline_gate=_gate_of(pipeline_report),
        inspection_gate=_gate_of(inspection),
        security_gate=_gate_of(security),
        tests_gate=_gate_of(tests) if tests is not None else None,
        changed_paths=changed,
        protected_paths=protected_hits,
        isolation_violations=isolation,
        worktree_path=worktree_root,
    )


class ForgeApp(BaseTUI):
    TITLE = "Forge"
    SUB_TITLE = "software factory — spec-gated build loop"

    def __init__(
        self,
        *,
        agent_runner: AgentRunner | None = None,
        pipeline_runner: PipelineRunner | None = None,
        build_fn: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.agent_runner: AgentRunner = agent_runner or run_external_agent
        self.pipeline_runner: PipelineRunner = pipeline_runner or run_pipeline
        self.build_fn = build_fn or build_from_spec
        self.spec_text: str = ""

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
        spec_md = render_spec_md(draft)
        self.spec_text = spec_md
        spec_path = Path(self.repo) / ".forge-spec.md"
        try:
            spec_path.write_text(spec_md, encoding="utf-8")
            self.say(f"wrote {spec_path}")
        except OSError as exc:
            self.say(f"[red]could not write spec: {exc}[/]")
        await self._build()

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

    async def _build(self) -> None:
        self.say("[dim]launching isolated coding agent...[/]")
        try:
            result: BuildResult = self.build_fn(
                self.repo,
                self.spec_text,
                agent_runner=self.agent_runner,
                pipeline_runner=self.pipeline_runner,
            )
        except Exception as exc:
            self.say(f"[red]build error: {type(exc).__name__}: {exc}[/]")
            return
        style = "green" if result.gate == "PASS" else "red"
        self.say(
            f"[bold {style}]build gate={result.gate}[/] "
            f"agent={result.agent_gate} pipeline={result.pipeline_gate} "
            f"inspect={result.inspection_gate} security={result.security_gate} "
            f"tests={result.tests_gate}"
        )
        self.say(f"agent changed: {', '.join(result.changed_paths) or '(none)'}")
        if result.protected_paths:
            self.say(f"[red]protected paths rejected: {list(result.protected_paths)}[/]")
        if result.isolation_violations:
            self.say(f"[red]isolation violations: {list(result.isolation_violations)}[/]")
        self.say(f"[dim]{result.note}[/]")

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
