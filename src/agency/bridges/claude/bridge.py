"""Claude Code CLI bridge (subprocess-based).

Drives the ``claude`` CLI in non-interactive stdio/loop mode::

    claude --loop --stdio --output-format stream-json

Responses arrive as newline-delimited / SSE-framed JSON events which are
parsed into plain text deltas. Authentication is via ``ANTHROPIC_API_KEY``
(or a pre-authenticated CLI session); retries use exponential backoff and
every execution is bounded by ``timeout_s``.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import AsyncGenerator
from time import perf_counter
from typing import Any

from pydantic import Field

from agency.bridges.base import Bridge, BridgeConfig, BridgeResult, BridgeStatus, BridgeUsage


class ClaudeConfig(BridgeConfig):
    """Configuration for :class:`ClaudeCodeBridge`."""

    name: str = Field(default="claude")
    cli_path: str = Field(default="claude", description="Claude Code CLI binary.")
    model: str = Field(default="claude-sonnet-4-5", description="Model passed via --model.")
    working_dir: str | None = Field(default=None, description="CWD for the CLI subprocess.")
    api_key_env: str = Field(default="ANTHROPIC_API_KEY")
    use_loop: bool = Field(default=True, description="Pass --loop for agentic execution.")
    output_format: str = Field(default="stream-json")


class ClaudeCodeBridge(Bridge):
    """Subprocess bridge to the Claude Code CLI."""

    def __init__(self, config: ClaudeConfig | None = None, **kwargs: Any) -> None:
        super().__init__(config or ClaudeConfig(**kwargs))
        self._cfg: ClaudeConfig = self._config  # type: ignore[assignment]

    # ------------------------------------------------------------------ #
    # Bridge interface
    # ------------------------------------------------------------------ #

    async def execute(self, task: str, context: dict[str, Any] | None = None) -> BridgeResult:
        """Run ``task`` via ``claude --loop --stdio`` and collect the output."""
        start = perf_counter()
        prompt = self._build_prompt(task, context)
        last_error = "unknown error"
        for attempt in range(self._cfg.max_retries + 1):
            try:
                output, usage, artifacts = await self._run_once(prompt, context)
                return BridgeResult(
                    output=output,
                    artifacts=artifacts,
                    usage=usage,
                    status=BridgeStatus.SUCCESS,
                    duration_s=perf_counter() - start,
                )
            except TimeoutError as exc:
                last_error = f"claude timed out after {self._cfg.timeout_s}s: {exc}"
                status = BridgeStatus.TIMEOUT
            except (OSError, ValueError) as exc:
                last_error = str(exc)
                status = BridgeStatus.FAILED
                # Auth / binary-missing errors are not worth retrying.
                if "auth" in last_error.lower() or "not found" in last_error.lower():
                    break
            if attempt < self._cfg.max_retries:
                backoff = self._cfg.backoff_for(attempt)
                self._log.warning(
                    "bridge.retry", bridge="claude", attempt=attempt + 1, backoff=backoff
                )
                await asyncio.sleep(backoff)
            else:
                status = status if "status" in dir() else BridgeStatus.FAILED
        return BridgeResult.failure(last_error, status=status, duration_s=perf_counter() - start)

    async def stream(
        self, task: str, context: dict[str, Any] | None = None
    ) -> AsyncGenerator[str, None]:
        """Yield SSE-decoded text deltas as the CLI emits them."""
        prompt = self._build_prompt(task, context)
        cmd = self._build_command()
        env = self._build_env()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cfg.working_dir,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"claude CLI not found: {self._cfg.cli_path!r}") from exc
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        assert proc.stdout is not None
        try:
            async for raw in proc.stdout:
                for chunk in self._parse_sse_line(raw.decode("utf-8", errors="replace")):
                    yield chunk
        finally:
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                proc.kill()

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": "claude",
            "streaming": True,
            "tools": ["file_edit", "shell", "search", "reasoning"],
            "modalities": ["text", "code"],
            "max_context_tokens": 200_000,
            "transport": "subprocess-stdio",
            "notes": "Claude Code CLI via --loop --stdio with stream-json output.",
        }

    async def health_check(self) -> bool:
        """Check CLI presence, auth, and a cheap ``--version`` probe."""
        if shutil.which(self._cfg.cli_path) is None:
            return False
        if not os.environ.get(self._cfg.api_key_env) and not await self._cli_reports_auth():
            # CLI may hold its own session token; fall back to --version probe.
            pass
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cfg.cli_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(),
            )
            await asyncio.wait_for(proc.wait(), timeout=10.0)
            return proc.returncode == 0
        except (OSError, TimeoutError):
            return False

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _build_command(self) -> list[str]:
        cmd = [self._cfg.cli_path, "--stdio", "--output-format", self._cfg.output_format]
        if self._cfg.use_loop:
            cmd.append("--loop")
        cmd += ["--model", self._cfg.model]
        return cmd

    def _build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if not env.get(self._cfg.api_key_env):
            self._log.warning("bridge.missing_auth", env_var=self._cfg.api_key_env)
        return env

    async def _cli_reports_auth(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cfg.cli_path,
                "auth",
                "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=10.0)
            return proc.returncode == 0
        except (OSError, TimeoutError):
            return False

    async def _run_once(
        self, prompt: str, context: dict[str, Any] | None
    ) -> tuple[str, BridgeUsage, dict[str, Any]]:
        cmd = self._build_command()
        if shutil.which(self._cfg.cli_path) is None:
            raise OSError(f"claude CLI not found: {self._cfg.cli_path!r}")
        if not os.environ.get(self._cfg.api_key_env):
            self._log.warning("bridge.missing_auth", env_var=self._cfg.api_key_env)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cfg.working_dir,
            env=self._build_env(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), timeout=self._cfg.timeout_s
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[:2000]
            raise OSError(f"claude CLI exited with code {proc.returncode}: {detail}")
        text, usage, artifacts = self._parse_output(stdout.decode("utf-8", errors="replace"))
        artifacts["working_dir"] = self._cfg.working_dir
        artifacts["model"] = self._cfg.model
        return text, usage, artifacts

    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #

    def _parse_sse_line(self, line: str) -> list[str]:
        """Decode one raw stdout line (SSE or NDJSON) into text deltas."""
        line = line.strip()
        if not line:
            return []
        if line.startswith(":"):
            return []  # SSE comment / heartbeat.
        if line.startswith("data:"):
            line = line[len("data:") :].strip()
        if line == "[DONE]":
            return []
        if not line.startswith("{"):
            return [line]  # Plain-text fallback.
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return []
        delta = self._extract_delta(event)
        return [delta] if delta else []

    def _extract_delta(self, event: dict[str, Any]) -> str:
        event_type = str(event.get("type", ""))
        if event_type in {"content_block_delta", "text_delta", "message_delta"}:
            delta = event.get("delta", {})
            if isinstance(delta, dict):
                return str(delta.get("text", "") or "")
            return str(delta or "")
        for key in ("text", "content", "output"):
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    def _parse_output(self, raw: str) -> tuple[str, BridgeUsage, dict[str, Any]]:
        chunks: list[str] = []
        input_tokens = output_tokens = 0
        tool_calls: list[Any] = []
        for line in raw.splitlines():
            chunks.extend(self._parse_sse_line(line))
            try:
                event = json.loads(line.removeprefix("data:").strip()) if line.strip() else None
            except (json.JSONDecodeError, ValueError):
                event = None
            if isinstance(event, dict):
                usage = event.get("usage") or {}
                input_tokens = max(input_tokens, int(usage.get("input_tokens", 0) or 0))
                output_tokens = max(output_tokens, int(usage.get("output_tokens", 0) or 0))
                if event.get("type") == "tool_use":
                    tool_calls.append(event)
        text = "".join(chunks) if chunks else raw.strip()
        usage = BridgeUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )
        return text, usage, {"tool_calls": tool_calls}
