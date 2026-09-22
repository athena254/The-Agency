"""Live smoke test: real LLM + real web tools end-to-end."""
import asyncio
import sys

sys.path.insert(0, "src")

from agency.agents.research import RESEARCH_SYSTEM_PROMPT
from agency.llm.adapter import LLMAdapter
from agency.tools.base import ToolContext
from agency.tools.builtin import register_all
from agency.tools.driver import ToolDriver
from agency.tools.registry import ToolRegistry


async def main():
    registry = ToolRegistry()
    register_all(registry)  # real web tools, no mock transport
    llm = LLMAdapter()  # pollinations, keyless
    driver = ToolDriver(registry=registry, llm=llm)
    ctx = ToolContext(agent_id="smoke-test", task_id="smoke-1")
    result = await driver.run(
        task="What is the latest stable version of Python? Cite sources.",
        system_prompt=RESEARCH_SYSTEM_PROMPT,
        ctx=ctx,
    )
    print("STATUS:", result.status)
    print("LLM CALLS:", result.llm_calls)
    print("TOOL STEPS:")
    for s in result.steps:
        print(f"  - {s.tool} ok={s.ok} {s.duration_ms}ms error={s.error}")
    print("EVIDENCE:", result.evidence)
    print("\nFINAL ANSWER (first 600 chars):")
    print(result.final_answer[:600])


asyncio.run(main())
