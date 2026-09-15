"""Butler-Only Gateway - routes directly to domain agents (no Buddy UI agent)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.request
import urllib.error
from typing import Any

from .gateway_agent import GatewayAgent, GovernanceError, SessionState

logger = logging.getLogger(__name__)


class ButlerOnlyGateway:
    """Butler-only gateway mode.

    In this mode, the Butler acts as the sole intermediary between
    user interfaces and domain agents. There is no Buddy UI agent -
    all responses are text-based and go directly back to the user.

    Routing rules:
      - @agent_name → routes to the specified domain agent
      - No prefix → routes to the default domain agent (first in config)
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.gateway_agent = GatewayAgent(config)
        self.adapter_config = config.get("adapters", {})
        self.default_agent = (
            config.get("domain_agents", [{}])[0].get("name", "research")
        )
        self._agent_pattern = re.compile(r"@(\w+)")

        # Register default governance
        self.gateway_agent.register_governance_hook(
            self.gateway_agent._default_governance
        )

    async def start(self) -> None:
        """Start the Butler-only gateway."""
        logger.info("Starting Butler-Only Gateway")
        logger.info("Default agent: %s", self.default_agent)

        await self.gateway_agent.start_health_monitoring()

        try:
            await self._run_adapters()
        finally:
            await self.gateway_agent.shutdown()

    async def _run_adapters(self) -> None:
        """Start all enabled adapters and run them concurrently."""
        tasks: list[asyncio.Task[None]] = []

        if self.adapter_config.get("cli", {}).get("enabled", True):
            from .adapters.cli_adapter import CLIAdapter

            cli = CLIAdapter(self)
            tasks.append(asyncio.create_task(cli.run()))

        if self.adapter_config.get("telegram", {}).get("enabled", False):
            from .adapters.telegram_adapter import TelegramAdapter

            tg = TelegramAdapter(self)
            tasks.append(asyncio.create_task(tg.run()))

        if self.adapter_config.get("vscode", {}).get("enabled", False):
            from .adapters.vscode_adapter import VSCodeAdapter

            vscode = VSCodeAdapter(self)
            tasks.append(asyncio.create_task(vscode.run()))

        if not tasks:
            logger.warning("No adapters enabled. Running in idle mode.")
            tasks.append(asyncio.create_task(self._idle_loop()))

        await asyncio.gather(*tasks)

    async def _idle_loop(self) -> None:
        """Keep the process alive when no adapters are running."""
        while True:
            await asyncio.sleep(60)

    async def handle_message(
        self,
        content: str,
        user_id: str,
        adapter: str,
        session_id: str | None = None,
    ) -> str:
        """Handle an incoming message and return a text response."""
        # Apply governance
        try:
            message = await self.gateway_agent.apply_governance({"content": content})
        except GovernanceError as e:
            logger.warning("Message rejected by governance: %s", e)
            return f"Message rejected: {e}"

        # Determine target agent
        target_agent = self._resolve_target(content)

        # Get or create session
        if session_id is None:
            session_id = f"{adapter}:{user_id}"
        session = self.gateway_agent.get_session(session_id)
        if session is None:
            session = self.gateway_agent.create_session(session_id, user_id, adapter)
        session.active_agent = target_agent
        session.last_activity = asyncio.get_event_loop().time()

        # Route to domain agent
        response = await self._route_to_domain_agent(target_agent, message["content"])

        return response

    def _resolve_target(self, content: str) -> str:
        """Determine which domain agent should handle the message."""
        match = self._agent_pattern.match(content.strip())
        if match:
            agent_name = match.group(1)
            if agent_name in self.gateway_agent.agents:
                return agent_name
            logger.warning("Unknown agent '%s', using default", agent_name)
        return self.default_agent

    async def _route_to_domain_agent(self, agent_name: str, content: str) -> str:
        """Send a message to a domain agent and return its response."""
        agent_cfg = next(
            (a for a in self.config.get("domain_agents", []) if a["name"] == agent_name),
            None,
        )
        if not agent_cfg:
            return f"Error: Unknown agent '{agent_name}'"

        endpoint = agent_cfg["endpoint"]
        payload = json.dumps({"message": content, "agent": agent_name}).encode("utf-8")

        loop = asyncio.get_event_loop()
        try:
            req = urllib.request.Request(
                endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            response = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=30),
            )
            result = json.loads(response.read().decode("utf-8"))
            return result.get("response", result.get("content", "No response"))
        except urllib.error.HTTPError as e:
            logger.error("HTTP error from %s: %s", agent_name, e)
            return f"Agent '{agent_name}' returned error: {e.code}"
        except urllib.error.URLError as e:
            logger.error("Connection error to %s: %s", agent_name, e)
            return f"Agent '{agent_name}' is unreachable: {e.reason}"
        except Exception as e:
            logger.error("Unexpected error routing to %s: %s", agent_name, e)
            return f"Error communicating with '{agent_name}': {e}"

    async def format_response(self, raw_response: str) -> str:
        """Format a raw agent response for text-based output."""
        # In Butler-only mode, responses are already text
        # Apply output length limits
        max_len = self.config.get("governance", {}).get("max_output_length", 50000)
        if len(raw_response) > max_len:
            return raw_response[:max_len] + "\n... (truncated)"
        return raw_response
