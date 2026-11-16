# ruff: noqa: BLE001
"""Agency TUI — full access to the system (status/agents/tasks/evidence)."""

from __future__ import annotations

import httpx

from agency.tui.base import BaseTUI


class AgencyApp(BaseTUI):
    TITLE = "Agency"
    SUB_TITLE = "governed multi-agent execution"

    async def show_status(self) -> None:
        try:
            async with httpx.AsyncClient(base_url=self.server_url, timeout=5.0) as client:
                resp = await client.get("/v1/health")
                resp.raise_for_status()
                self.say(f"[bold]Agency status[/] {resp.json()}")
        except Exception:
            self.say("[yellow]API not reachable — start with: agency start[/]")
        self.say(f"mode={self.policy.mode.value} brain={self.brain.describe()}")

    async def _get(self, path: str, params: dict[str, object] | None = None) -> None:
        try:
            async with httpx.AsyncClient(base_url=self.server_url, timeout=10.0) as client:
                resp = await client.get(path, params=params)  # type: ignore[arg-type]
                resp.raise_for_status()
                self.say(f"{path} -> {resp.json()}")
        except Exception as exc:
            self.say(f"[red]{path} failed: {type(exc).__name__}[/]")

    async def handle_slash(self, text: str) -> None:
        parts = text.split()
        cmd = parts[0].lower()
        if cmd == "/agents":
            await self._get("/v1/agents")
        elif cmd == "/tasks":
            await self._get("/v1/tasks")
        elif cmd == "/evidence":
            await self._get("/v1/evidence")
        elif cmd == "/memory":
            q = " ".join(parts[1:]) or "test"
            await self._get("/v1/memory/search", {"q": q})
        elif cmd == "/forge":
            self.say("Forge is a separate TUI: run `forge` (or `agency forge code ...`).")
        else:
            await super().handle_slash(text)

    def help_text(self) -> str:
        return super().help_text() + "\n/agents /tasks /evidence /memory <q> /status /forge"


def run_agency_tui(
    *, yolo: bool = False, model: str | None = None, server_url: str = "http://127.0.0.1:8000"
) -> None:
    AgencyApp(yolo=yolo, model=model, server_url=server_url).run()
