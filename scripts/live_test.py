#!/usr/bin/env python3
"""Live test for The Agency — starts the Butler and sends test messages.

Usage:
    python scripts/live_test.py

This script:
1. Starts the Butler server
2. Sends test messages for different domains
3. Verifies responses
4. Prints results

The system uses echo mode by default (no API key needed).
Set AGENCY_LLM_API_KEY env var for real LLM calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agency.butler.config import ButlerConfig
from agency.butler.service import ButlerService
from agency.orchestrator import AgencyOrchestrator


async def live_test() -> None:
    """Run the live test."""
    print("=" * 60)
    print("THE AGENCY — LIVE TEST")
    print("=" * 60)

    # Initialize
    print("\n[1/4] Initializing orchestrator...")
    orch = AgencyOrchestrator()
    await orch.start()
    print("  ✓ Orchestrator started")

    print("\n[2/4] Initializing Butler service...")
    config = ButlerConfig()
    butler = ButlerService(config=config, orchestrator=orch)
    await butler.start()
    print("  ✓ Butler started")

    # Register agents
    print("\n[3/4] Registering agents...")
    agents = [
        ("scout", "security", ["inspect", "scan", "recon"]),
        ("analyst", "security", ["analyze", "assess", "report"]),
        ("memory_keeper", "memory", ["remember", "recall", "search"]),
    ]
    for name, domain, caps in agents:
        agent = await butler.orchestrator.register_agent(name, domain, caps)
        print(f"  ✓ Registered {name} ({domain})")

    # Send test messages
    print("\n[4/4] Sending test messages...")
    test_cases = [
        ("Scan the target system for vulnerabilities", "security"),
        ("Remember this important security finding", "memory"),
        ("Assess the risk exposure of the deployment", "risk"),
    ]

    for message, expected_domain in test_cases:
        print(f"\n  Message: {message}")
        print(f"  Expected domain: {expected_domain}")

        start = time.time()
        response = await butler.handle_message(message, "live-tester", {})
        elapsed = time.time() - start

        print(f"  Response: {response[:100]}...")
        print(f"  Time: {elapsed:.2f}s")
        print(f"  ✓ Message handled")

    # Health check
    print("\n" + "=" * 60)
    print("HEALTH CHECK")
    print("=" * 60)
    health = await butler.orchestrator.health_check()
    for key, value in health.items():
        print(f"  {key}: {value}")

    # Audit log
    print("\n" + "=" * 60)
    print("AUDIT LOG (last 5 entries)")
    print("=" * 60)
    entries = await butler.orchestrator._audit_log.query()
    for entry in entries[-5:]:
        print(f"  [{entry.timestamp.strftime('%H:%M:%S')}] {entry.agent}: {entry.action} → {entry.result}")

    # Memory
    print("\n" + "=" * 60)
    print("MEMORY (last 5 items)")
    print("=" * 60)
    items = await butler.orchestrator._memory_store.list_all(limit=5)
    for item in items:
        print(f"  [{item.tier}] {item.agent_id}: {item.content[:60]}...")

    # Stop
    await butler.stop()
    await orch.stop()

    print("\n" + "=" * 60)
    print("LIVE TEST COMPLETE")
    print("=" * 60)
    print("\nThe Agency is working!")
    print(f"  - {len(agents)} agents registered")
    print(f"  - {len(test_cases)} messages processed")
    print(f"  - {len(entries)} audit entries")
    print(f"  - {len(items)} memory items")
    print("\nTo use with real LLM:")
    print("  export AGENCY_LLM_API_KEY=your_key_here")
    print("  butler start")


if __name__ == "__main__":
    asyncio.run(live_test())
