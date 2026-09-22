"""Telegram message handler."""

from __future__ import annotations

from typing import Any

import structlog

from agency.agents.demo.agent import DemoAgent
from agency.telegram.adapter import TelegramAdapter
from agency.telegram.config import TelegramConfig

logger = structlog.get_logger(__name__)


class TelegramHandler:
    """Handles incoming Telegram messages and routes them to the Butler."""

    def __init__(self, config: TelegramConfig, butler: Any = None) -> None:
        self._config = config
        self._butler = butler
        self._adapter = TelegramAdapter(config)
        self._demo = DemoAgent()
        self._log = structlog.get_logger(__name__)

    async def handle_update(self, update: dict[str, Any]) -> dict[str, Any]:
        """Handle a single Telegram update."""
        message = update.get("message", {})
        if not message:
            return {"status": "ignored", "reason": "no message"}

        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")
        sender = message.get("from", {}).get("username", "unknown")

        if not text:
            return {"status": "ignored", "reason": "empty message"}

        # Check allowed chat IDs
        if self._config.allowed_chat_ids and chat_id not in self._config.allowed_chat_ids:
            return {"status": "rejected", "reason": "chat not allowed"}

        # Bot commands are answered deterministically from real system
        # state — never through the LLM, so no fiction is possible.
        command = text.strip().lower()
        if command in ("/agents", "/status", "/whoami", "/proposals"):
            response = await self._system_answer(command)
            if chat_id:
                await self._adapter.send_message(chat_id, response)
            return {"status": "ok", "chat_id": chat_id, "command": command}

        # /propose-agent <name> <domain> <capability1> [capability2 ...]
        if command.startswith("/propose-agent"):
            args = text.strip()[len("/propose-agent"):].strip()
            response = await self._handle_propose_agent(args, sender)
            if chat_id:
                await self._adapter.send_message(chat_id, response)
            return {"status": "ok", "chat_id": chat_id, "command": "/propose-agent"}

        # Process via Butler if available, else demo agent
        if self._butler:
            response = await self._butler.handle_message(text, sender, {"chat_id": chat_id})
        else:
            response = await self._demo.handle(text, {"sender": sender, "chat_id": chat_id})

        # Send response
        if chat_id:
            await self._adapter.send_message(chat_id, response)

        return {"status": "ok", "chat_id": chat_id, "response_length": len(response)}

    async def _system_answer(self, command: str) -> str:
        """Deterministic answers built from real system state."""
        if command == "/agents":
            if self._butler:
                agents = await self._butler.orchestrator.list_agents()
                lines = [f"• {a.name} — domain: {a.domain}" for a in agents]
                return (
                    "🤖 *Registered agents (live from the registry)*\n\n"
                    + "\n".join(lines)
                )
            return "Butler not running."
        if command == "/status":
            if self._butler:
                health = await self._butler.orchestrator.health_check()
                return (
                    "🤖 *System status (live)*\n\n"
                    f"• Orchestrator: {health.get('orchestrator')}\n"
                    f"• Agents: {health.get('identity_registry')}\n"
                    f"• Tasks: {health.get('tasks')}\n"
                    f"• LLM: {health.get('llm', {}).get('provider')}/"
                    f"{health.get('llm', {}).get('model')}\n"
                    f"• Memory: {health.get('memory')}\n"
                    f"• Evidence: {health.get('evidence')}"
                )
            return "Butler not running."
        if command == "/whoami":
            return (
                "🤖 I am the *Butler* — the gateway of The Agency, a real "
                "multi-agent system running on this machine. I route your "
                "messages to registered agents and report what they actually "
                "did. Use /agents to see them, /status for system health."
            )
        if command == "/proposals":
            return await self._list_proposals()
        return "Unknown command."

    async def _handle_propose_agent(self, args: str, sender: str) -> str:
        """Handle /propose-agent <name> <domain> <capability1> [capability2 ...]."""
        if not self._butler:
            return "Butler not running."
        if not args.strip():
            return "Usage: /propose-agent <name> <domain> <capability1> [capability2 ...]"
        
        parts = args.strip().split()
        if len(parts) < 3:
            return "Usage: /propose-agent <name> <domain> <capability1> [capability2 ...]"
        
        name = parts[0]
        domain = parts[1]
        capabilities = parts[2:]
        
        orchestrator = self._butler.orchestrator
        lattice = orchestrator._lattice
        if lattice is None:
            return "Lattice not available. Cannot create proposal."
        
        # Submit the proposal
        proposal_id = await lattice.submit_proposal(
            proposer_id=sender,
            proposal_type="spawn_agent",
            payload={"name": name, "domain": domain, "capabilities": capabilities},
            quorum=0.66,
            ttl_seconds=3600,
        )
        
        # Butler auto-approves (it routes requests, and this is a direct command)
        await orchestrator.resolve_agent_proposal(
            proposal_id=proposal_id,
            voter_id="butler",
            decision="approve",
            evidence=["Direct command from human user"],
        )
        
        # User auto-approves (they initiated the request)
        await orchestrator.resolve_agent_proposal(
            proposal_id=proposal_id,
            voter_id="user",
            decision="approve",
            evidence=["User initiated the agent proposal"],
        )
        
        # Check if proposal passed and agent was spawned
        proposal = await lattice.get_proposal_status(proposal_id)
        if proposal.status == "passed":
            # Get the newly spawned agent
            agents = await orchestrator.list_agents()
            new_agent = next((a for a in agents if a.name == name and a.domain == domain), None)
            if new_agent:
                return (
                    f"✅ Agent spawned successfully!\n\n"
                    f"• Name: {new_agent.name}\n"
                    f"• ID: {new_agent.id}\n"
                    f"• Domain: {new_agent.domain}\n"
                    f"• Capabilities: {', '.join(capabilities)}\n"
                    f"• Proposal: {proposal_id[:16]}..."
                )
            return f"Proposal passed but agent not found in registry. Proposal: {proposal_id[:16]}..."
        
        return f"Proposal submitted: {proposal_id[:16]}... Status: {proposal.status}"

    async def _list_proposals(self) -> str:
        """List all open governance proposals."""
        if not self._butler:
            return "Butler not running."
        orchestrator = self._butler.orchestrator
        lattice = orchestrator._lattice
        if lattice is None:
            return "Lattice not available."
        
        proposals = await lattice.list_open_proposals()
        if not proposals:
            return "📋 No open proposals."
        
        lines = []
        for p in proposals:
            lines.append(
                f"• {p.proposal_id[:16]}... — {p.proposal_type} "
                f"(quorum: {p.quorum_required}, votes: {len(p.votes)})"
            )
        return "📋 *Open Proposals*\n\n" + "\n".join(lines)

    async def process_message(self, message: dict[str, Any]) -> str:
        """Process a message and return the response text."""
        text = message.get("text", "")
        sender = message.get("from", {}).get("username", "unknown")

        if self._butler:
            return await self._butler.handle_message(text, sender, {})
        return await self._demo.handle(text, {"sender": sender})

    async def send_response(self, chat_id: int, text: str) -> dict[str, Any]:
        """Send a response to a Telegram chat."""
        return await self._adapter.send_message(chat_id, text)

    async def close(self) -> None:
        await self._adapter.close()
