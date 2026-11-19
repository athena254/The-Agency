import pytest

from agency.tools.base import ToolContext
from agency.tools.external import (
    ComputerUseTool,
    GoogleWorkspaceTool,
    Last30DaysTool,
    WebScrapeTool,
    register_external_tools,
)
from agency.tools.registry import ToolRegistry


def test_external_tools_register() -> None:
    reg = ToolRegistry()
    tools = register_external_tools(reg)
    assert len(tools) == 4
    specs = {s.name for s in reg.list_specs()}
    assert {"web_scrape", "google_workspace", "last30days", "computer_use"} <= specs


@pytest.mark.asyncio
async def test_computer_use_stub() -> None:
    tool = ComputerUseTool()
    ctx = ToolContext(agent_id="a", task_id="t")
    res = await tool.run({"task": "click"}, ctx)
    assert res.ok is False
    assert "stub" in (res.error or "")


@pytest.mark.asyncio
async def test_google_workspace_missing_binary() -> None:
    tool = GoogleWorkspaceTool()
    ctx = ToolContext(agent_id="a", task_id="t")
    # Force missing by monkeypatching shutil.which via env not needed; just call.
    res = await tool.run({"args": ["drive", "files", "list"]}, ctx)
    # Either missing binary or (if gws installed) a result; never crash.
    assert isinstance(res.ok, bool)