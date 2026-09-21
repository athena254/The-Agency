"""Hermes bridge (ACP — Agent Communication Protocol).

Drives the ``hermes`` CLI (``hermes agent run --acp``) over stdio using
JSON-RPC 2.0 ACP frames. Each request carries a unique id; responses and
streaming notifications (``agent/event``) are multiplexed on stdout and
demultiplexed by id. Retries cover transient spawn failures; per-execution
timeouts are enforced around the whole request/response cycle.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import shutil
from collections.abc import AsyncGenerator
from time import perf_counter
from typing import Any

from pydantic import Field

from agency.bridges.base import Bridge, BridgeConfig, BridgeResult, BridgeStatus, BridgeUsage


class HermesConfig(BridgeConfig):
    """Configuration for :class:`HermesBridge`."""

    name: str = Field(default="hermes")
    cli_path: str = Field(default="hermes", description="Hermes CLI binary.")
    agent: str = Field(default="default", description="Target ACP agent name.")
    working_dir: str | None = Field(default=None)
    protocol_version: str = Field(default="acp/1")


class HermesBridge(Bridge):
    """ACP-protocol bridge spawned as ``hermes agent run`` subprocess."""

    _ids = itertools.count(1)

    def __init__(self, config: HermesConfig | None = None, **kwargs: Any) -> None:
        super().__init__(config or HermesConfig(**kwargs))
        self._cfg: HermesConfig = self._config  # type: ignore[assignment]

    # ------------------------------------------------------------------ #
    # Bridge interface
    # ------------------------------------------------------------------ #

    async def execute(self, task: str, context: dict[str, Any] | None = None) -> BridgeResult:
        """Run one ACP ``agent/run`` request and await the matching response."""
        start = perf_counter()
        request = self._build_request(task, context)
        last_error = "unknown error"
        for attempt in range(self._cfg.max_retries + 1):
            try:
                response = await self._roundtrip(request)
                return self._to_result(response, start)
            except TimeoutError as exc:
                last_error, status = str(exc), BridgeStatus.TIMEOUT
            except (OSError, ValueError) as exc:
                last_error, status = str(exc), BridgeStatus.FAILED
                if "not found" in last_error.lower():
                    break
            if attempt < self._cfg.max_retries:
                await asyncio.sleep(self._cfg.backoff_for(attempt))
        return BridgeResult.failure(last_error, status=status, duration_s=perf_counter() - start)

    async def stream(
        self, task: str, context: dict[str, Any] | None = None
    ) -> AsyncGenerator[str, None]:
        """Yield ``agent/event`` notification deltas for one ACP run."""
        request = self._build_request(task, context)
        proc = await self._spawn()
        assert proc.stdin is not None and proc.stdout is not None
        try:
            proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
            async for chunk in self._read_events(proc, request["id"]):
                yield chunk
        finally:
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except TimeoutError:
                    proc.kill()

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": "hermes",
            "streaming": True,
            "tools": ["delegate", "message", "coordinate"],
            "modalities": ["text", "json-rpc"],
            "transport": "acp-stdio",
            "protocol": self._cfg.protocol_version,
            "notes": "JSON-RPC 2.0 ACP frames over `hermes agent run --acp`.",
        }

    async def health_check(self) -> bool:
        """Probe ``hermes --version`` plus an ACP ``initialize`` handshake."""
        if shutil.which(self._cfg.cli_path) is None:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cfg.cli_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=10.0)
            if proc.returncode != 0:
                return False
        except (OSError, TimeoutError):
            return False
        try:
            response = await self._roundtrip(self._handshake_request())
            return "error" not in response
        except (OSError, TimeoutError, ValueError):
            return False

    # ------------------------------------------------------------------ #
    # ACP framing
    # ------------------------------------------------------------------ #

    def _build_request(self, task: str, context: dict[str, Any] | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "agent": self._cfg.agent,
            "protocol": self._cfg.protocol_version,
            "input": self._build_prompt(task, context),
        }
        if context:
            params["context"] = context
        return {"jsonrpc": "2.0", "id": next(self._ids), "method": "agent/run", "params": params}

    def _handshake_request(self) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": "initialize",
            "params": {"protocol": self._cfg.protocol_version},
        }

    async def _spawn(self) -> asyncio.subprocess.Process:
        if shutil.which(self._cfg.cli_path) is None:
            raise OSError(f"hermes CLI not found: {self._cfg.cli_path!r}")
        env = dict(os.environ)
        return await asyncio.create_subprocess_exec(
            self._cfg.cli_path,
            "agent",
            "run",
            "--acp",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cfg.working_dir,
            env=env,
        )

    async def _roundtrip(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send one ACP request; collect response + interleaved events."""
        proc = await self._spawn()
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        target = request["id"]
        events: list[str] = []
        try:
            async with asyncio.timeout(self._cfg.timeout_s):
                assert proc.stdout is not None
                async for raw in proc.stdout:
                    frame = self._parse_frame(raw.decode("utf-8", errors="replace"))
                    if frame is None:
                        continue
                    if frame.get("id") == target and ("result" in frame or "error" in frame):
                        frame.setdefault("_events", events)
                        return frame
                    delta = self._event_delta(frame)
                    if delta:
                        events.append(delta)
                stderr = (
                    (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
                    if proc.stderr
                    else ""
                )
                raise ValueError(f"hermes closed stdio without a response: {stderr[:500]}")
        except TimeoutError:
            proc.kill()
            raise TimeoutError(f"hermes ACP request timed out after {self._cfg.timeout_s}s")
        finally:
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except TimeoutError:
                    proc.kill()

    async def _read_events(
        self, proc: asyncio.subprocess.Process, target: Any
    ) -> AsyncGenerator[str, None]:
        assert proc.stdout is not None
        try:
            async with asyncio.timeout(self._cfg.timeout_s):
                async for raw in proc.stdout:
                    frame = self._parse_frame(raw.decode("utf-8", errors="replace"))
                    if frame is None:
                        continue
                    if frame.get("id") == target and ("result" in frame or "error" in frame):
                        result = frame.get("result") or {}
                        text = str(result.get("output", "") or "")
                        if text:
                            yield text
                        return
                    delta = self._event_delta(frame)
                    if delta:
                        yield delta
        except TimeoutError:
            proc.kill()
            raise TimeoutError(f"hermes ACP stream timed out after {self._cfg.timeout_s}s")

    # ------------------------------------------------------------------ #
    # ACP parsing
    # ------------------------------------------------------------------ #

    def _parse_frame(self, line: str) -> dict[str, Any] | None:
        line = line.strip()
        if not line or line.startswith(":"):
            return None
        if line.startswith("data:"):
            line = line[len("data:") :].strip()
        if line == "[DONE]" or not line.startswith("{"):
            return None
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            return None
        return frame if isinstance(frame, dict) else None

    def _event_delta(self, frame: dict[str, Any]) -> str:
        if frame.get("method") not in {"agent/event", "notifications/message", "event"}:
            return ""
        params = frame.get("params", {})
        if isinstance(params, dict):
            for key in ("delta", "text", "content", "output"):
                value = params.get(key)
                if isinstance(value, str) and value:
                    return value
        return ""

    def _to_result(self, response: dict[str, Any], start: float) -> BridgeResult:
        duration = perf_counter() - start
        if "error" in response:
            error = response["error"]
            detail = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            return BridgeResult.failure(f"hermes ACP error: {detail}", duration_s=duration)
        result = response.get("result", {})
        output = str(result.get("output", "") or "")
        events = response.get("_events", [])
        raw_usage = result.get("usage", {})
        usage = BridgeUsage(
            input_tokens=int(raw_usage.get("input_tokens", 0) or 0),
            output_tokens=int(raw_usage.get("output_tokens", 0) or 0),
            total_tokens=int(raw_usage.get("total_tokens", 0) or 0),
        )
        artifacts: dict[str, Any] = {
            "agent": self._cfg.agent,
            "protocol": self._cfg.protocol_version,
            "events": events,
            "result": {k: v for k, v in result.items() if k not in {"output", "usage"}},
        }
        return BridgeResult(output=output, artifacts=artifacts, usage=usage, duration_s=duration)
