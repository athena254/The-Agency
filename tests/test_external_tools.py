import pytest

from agency.tools.base import ToolContext, ToolResult, ToolSpec
from agency.tools.driver import ToolDriver
from agency.tools.external import register_external_tools
from agency.tools.registry import ToolRegistry


class _FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    async def generate(self, prompt, context=None):
        return self._responses.pop(0)


def test_external_tools_register() -> None:
    reg = ToolRegistry()
    tools = register_external_tools(reg)
    assert len(tools) == 4
    specs = {s.name for s in reg.list_specs()}
    assert {"web_scrape", "google_workspace", "last30days", "computer_use"} <= specs


@pytest.mark.asyncio
async def test_computer_use_stub() -> None:
    from agency.tools.external import ComputerUseTool

    tool = ComputerUseTool()
    ctx = ToolContext(agent_id="a", task_id="t")
    res = await tool.run({"task": "click"}, ctx)
    assert res.ok is False
    assert "stub" in (res.error or "")


@pytest.mark.asyncio
async def test_google_workspace_missing_binary() -> None:
    from agency.tools.external import GoogleWorkspaceTool

    tool = GoogleWorkspaceTool()
    ctx = ToolContext(agent_id="a", task_id="t")
    res = await tool.run({"args": ["drive", "files", "list"]}, ctx)
    assert isinstance(res.ok, bool)


@pytest.mark.asyncio
async def test_driver_dynamically_invokes_web_scrape() -> None:
    reg = ToolRegistry()
    register_external_tools(reg)

    class FakeScrape:
        spec = ToolSpec(name="web_scrape", description="scrape", parameters={"type": "object"})

        async def run(self, args, ctx):
            return ToolResult(
                tool="web_scrape", ok=True, output={"text": "hello"}, evidence={"url": args["url"]}
            )

    reg._tools["web_scrape"] = FakeScrape()
    llm = _FakeLLM(
        [
            '{"action": {"name": "web_scrape", "args": {"url": "https://example.com"}}}',
            '{"final": "done"}',
        ]
    )
    driver = ToolDriver(registry=reg, llm=llm)
    ctx = ToolContext(agent_id="a", task_id="t")
    result = await driver.run(task="scrape", system_prompt="sys", ctx=ctx)
    assert result.status == "completed"
    assert result.final_answer == "done"
    assert any(s.tool == "web_scrape" and s.ok for s in result.steps)
