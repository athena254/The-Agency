"""Live smoke: general agent chat + memory persistence through butler path."""
import asyncio
import sys

sys.path.insert(0, "src")

from agency.butler.service import ButlerService
from agency.orchestrator import AgencyOrchestrator


async def main():
    # Real persistent store (same file the bot uses) — proves the full path.
    orch = AgencyOrchestrator()
    butler = ButlerService(orchestrator=orch)
    await orch.start()
    try:
        r1 = await butler.handle_message(
            "Hi! Please remember that my favorite programming language is Rust.",
            "danny",
            {"chat_id": 1},
        )
        print("TURN 1:", str(r1)[:200])
        r2 = await butler.handle_message(
            "What is my favorite programming language?", "danny", {"chat_id": 1}
        )
        print("TURN 2:", str(r2)[:200])
        assert "Rust" in str(r2), "recall failed — assistant forgot!"
        print("\nRECALL OK — assistant remembered Rust across turns.")
    finally:
        await butler.stop()


asyncio.run(main())
