"""Live end-to-end test for the Butler HTTP service.

Starts the Butler server (in-process uvicorn), sends test messages via HTTP
across multiple domains (security, memory, evidence), verifies the responses,
and prints a summary.

Usage::

    python scripts/live_test.py [--host 127.0.0.1] [--port 8001]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading
import time
from typing import Any

import httpx
import uvicorn

from agency.butler.config import ButlerConfig
from agency.butler.server import create_app


CASES: list[dict[str, Any]] = [
    {
        "name": "security",
        "message": "Scan for vulnerabilities and check the firewall for exploits",
        "expected_domain": "security",
    },
    {
        "name": "memory",
        "message": "Please remember this conversation — what did we discuss earlier?",
        "expected_domain": "memory",
    },
    {
        "name": "evidence",
        "message": "Verify this forensic report and collect the evidence artifacts",
        "expected_domain": "evidence",
    },
]


def _wait_for_health(base_url: str, timeout_s: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_error: str = "no attempt yet"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/v1/health", timeout=5.0)
            if resp.status_code == 200:
                return resp.json()  # type: ignore[no-any-return]
            last_error = f"status={resp.status_code}"
        except Exception as exc:  # noqa: BLE001 — keep polling until timeout.
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"Server at {base_url} did not become healthy: {last_error}")


def run_live_test(host: str, port: int) -> int:
    config = ButlerConfig(host=host, port=port)
    app = create_app(config)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}"
    try:
        health = _wait_for_health(base_url)
        print(f"[health] {health}")

        agents = httpx.get(f"{base_url}/v1/agents", timeout=10.0).json()
        print(f"[agents] {len(agents)} registered: {[a['domain'] for a in agents]}")

        failures = 0
        with httpx.Client(base_url=base_url, timeout=60.0) as client:
            for case in CASES:
                name = case["name"]
                resp = client.post(
                    "/v1/message",
                    json={"message": case["message"], "sender": "live-test"},
                )
                if resp.status_code != 200:
                    print(f"[FAIL] {name}: HTTP {resp.status_code}: {resp.text[:300]}")
                    failures += 1
                    continue
                payload = resp.json()
                response_text = payload.get("response", "")
                domain = payload.get("domain", "")
                ok = bool(response_text) and domain == case["expected_domain"]
                status = "PASS" if ok else "FAIL"
                if not ok:
                    failures += 1
                print(
                    f"[{status}] {name}: domain={domain!r} "
                    f"(expected {case['expected_domain']!r}) "
                    f"response={response_text[:160]!r}"
                )

        print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
        return 0 if failures == 0 else 1
    finally:
        server.should_exit = True
        thread.join(timeout=15.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live end-to-end test for the Butler service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args(argv)
    try:
        return run_live_test(args.host, args.port)
    except Exception as exc:  # noqa: BLE001 — surface any startup failure.
        print(f"[ERROR] live test failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
