"""Telegram message handler."""

from __future__ import annotations

from typing import Any

import re
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
            return {"status": "ignored", "reason": "empty text"}

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

        # /research <topic> — run the research agent's tool loop
        # (web search → fetch → synthesize → cite → store).
        if command.startswith("/research"):
            args = text.strip()[len("/research"):].strip()
            response = await self._handle_research(args, sender)
            if chat_id:
                await self._send_long(chat_id, response)
            return {"status": "ok", "chat_id": chat_id, "command": "/research"}

        # /propose-agent <name> <domain> <capability1> [capability2 ...]
        if command.startswith("/propose-agent"):
            args = text.strip()[len("/propose-agent"):].strip()
            response = await self._handle_propose_agent(args, sender)
            if chat_id:
                await self._adapter.send_message(chat_id, response)
            return {"status": "ok", "chat_id": chat_id, "command": "/propose-agent"}

        # Plain-English agent creation: detect intent and handle
        # without LLM. This lets users say "create a new agent
        # called X that does Y" instead of memorizing commands.
        agent_intent = self._detect_agent_creation_intent(text)
        if agent_intent and self._butler:
            self._log.info("telegram.agent_creation_intent", intent=agent_intent, sender=sender)
            response = await self._handle_plain_english_agent_creation(
                agent_intent, sender
            )
            self._log.info("telegram.agent_creation_response", response=response[:200])
            if chat_id:
                await self._adapter.send_message(chat_id, response)
            return {"status": "ok", "chat_id": chat_id, "intent": "create_agent"}

        # Process via Butler if available, else demo agent
        if self._butler:
            response = await self._butler.handle_message(text, sender, {"chat_id": chat_id})
        else:
            response = await self._demo.handle(text, {"sender": sender, "chat_id": chat_id})

        # Send response
        if chat_id:
            await self._adapter.send_message(chat_id, response)

        return {"status": "ok", "chat_id": chat_id, "response_length": len(response)}

    async def _handle_research(self, args: str, sender: str) -> str:
        """Run the research agent's tool loop on a topic."""
        topic = args.strip()
        if not topic:
            return "Usage: /research <topic>"
        if not self._butler:
            return "Butler not running."

        orchestrator = self._butler.orchestrator
        research_agent = next(
            (a for a in await orchestrator.list_agents() if a.domain == "research"),
            None,
        )
        if research_agent is None:
            return "No research agent registered. Restart the bot to seed it."

        task = await orchestrator.submit_task(
            title=f"Research: {topic[:80]}",
            description=topic,
            agent_id=research_agent.id,
        )
        result = await orchestrator.execute_task(task.task_id)
        output = result.output if isinstance(result.output, str) else str(result.output)
        return f"🔍 *Research complete*\n\n{output}"

    async def _send_long(self, chat_id: int, text: str, limit: int = 3800) -> None:
        """Send text, splitting into multiple messages above the limit."""
        for i in range(0, max(len(text), 1), limit):
            await self._adapter.send_message(chat_id, text[i : i + limit])

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

    def _detect_agent_creation_intent(self, text: str) -> dict | None:
        """Detect if the user wants to create a new agent.

        Parses plain English and returns structured intent, or None.
        Examples:
            "create a new agent" → {}
            "make a bot called X" → {"name": "X"}
            "new agent named Y that does Z" → {"name": "Y", "description": "Z"}
            "spawn agent for finance" → {"domain": "finance"}
        """
        import re

        lower = text.lower().strip()
        # Remove common filler words
        lower = re.sub(r"^(hey|hi|hello|please|can you|could you|i want|i'd like|i need)\s*", "", lower)
        lower = re.sub(r"^(create|make|build|spawn|add|new)\s+(a|an|the)\s+(new\s+)?(agent|bot|one)\s*", "", lower)
        lower = re.sub(r"^(create|make|build|spawn|add|new)\s+(agent|bot)\s*", "", lower)
        lower = re.sub(r"^(agent|bot)\s*", "", lower)

        if not lower or lower in ("agent", "a agent", "an agent", "the agent"):
            # User just said "create agent" without details
            return {}

        result = {}

        # Extract name: "called X", "named X", "name is X"
        name_match = re.search(r"(?:called|named|name is|name)\s+[\"']?([a-zA-Z][a-zA-Z0-9_-]*)", text, re.IGNORECASE)
        if name_match:
            result["name"] = name_match.group(1)

        # Extract domain: "for Y", "in Y", "that does Y"
        domain_match = re.search(r"(?:for|in|domain|specializ(?:e|es?)\s+in)\s+[\"']?([a-zA-Z][a-zA-Z0-9_-]*)", text, re.IGNORECASE)
        if domain_match:
            result["domain"] = domain_match.group(1)

        # Extract capabilities BEFORE purpose (so "that does X" doesn't get consumed by purpose)
        cap_match = re.search(r"(?:that\s+(?:does|can)|can\s+do|with\s+capabilities?|capable\s+of)\s+(.+?)(?:\.|$)", text, re.IGNORECASE)
        if cap_match:
            caps_text = cap_match.group(1)
            # Replace " and " with "," then split on ","
            caps_text = re.sub(r"\s+and\s+", ",", caps_text)
            caps = [c.strip().replace(" ", "_") for c in caps_text.split(",") if c.strip()]
            result["capabilities"] = caps

        # Extract purpose/description (after capabilities to avoid overlap)
        purpose_match = re.search(r"(?:to|so\s+it|that|which)\s+(.+?)(?:\.|$)", text, re.IGNORECASE)
        if purpose_match:
            # Don't override capabilities with purpose text
            existing_caps = result.get("capabilities")
            new_purpose = purpose_match.group(1).strip()
            if not existing_caps:
                result["purpose"] = new_purpose

        return result if result else {}

    async def _handle_plain_english_agent_creation(self, intent: dict, sender: str) -> str:
        """Handle agent creation intent from plain English."""
        if not self._butler:
            return "Butler not running."

        name = intent.get("name")
        domain = intent.get("domain", "general")
        capabilities = intent.get("capabilities", [])
        purpose = intent.get("purpose", "")

        # Build capabilities from purpose if none given
        if not capabilities and purpose:
            # Convert "analyze financial data" to ["analyze", "financial_data"]
            words = purpose.replace("-", " ").split()
            capabilities = [w[:20] for w in words[:3] if len(w) > 2]
        if not capabilities:
            capabilities = [domain, "respond"]

        # If no name, tell user we need one
        if not name:
            return (
                "I can create a new agent for you through governance. "
                "To proceed, I need at minimum a name.\n\n"
                "Examples:\n"
                '• "Create an agent called StockBot for finance"\n'
                '• "New bot named HealthTracker that tracks medical records"\n'
                '• "Spawn a CryptoAgent for cryptocurrency analysis"\n\n'
                "What would you like to call it?"
            )

        orchestrator = self._butler.orchestrator
        lattice = orchestrator._lattice
        if lattice is None:
            return "Lattice not available. Cannot create proposal."

        # Submit the proposal
        self._log.info("lattice.spawn_submit", name=name, domain=domain, capabilities=capabilities)
        proposal_id = await lattice.submit_proposal(
            proposer_id=sender,
            proposal_type="spawn_agent",
            payload={"name": name, "domain": domain, "capabilities": capabilities},
            quorum=0.66,
            ttl_seconds=3600,
        )
        self._log.info("lattice.spawn_proposal_created", proposal_id=proposal_id)

        # Butler and user auto-approve
        await orchestrator.resolve_agent_proposal(
            proposal_id=proposal_id,
            voter_id="butler",
            decision="approve",
            evidence=["Direct request from human user via plain English"],
        )
        self._log.info("lattice.spawn_butler_voted", proposal_id=proposal_id)
        await orchestrator.resolve_agent_proposal(
            proposal_id=proposal_id,
            voter_id="user",
            decision="approve",
            evidence=["User initiated the agent proposal"],
        )
        self._log.info("lattice.spawn_user_voted", proposal_id=proposal_id)

        proposal = await lattice.get_proposal_status(proposal_id)
        self._log.info("lattice.spawn_proposal_status", status=proposal.status)
        if proposal.status == "passed":
            agents = await orchestrator.list_agents()
            self._log.info("lattice.spawn_agents_count", count=len(agents))
            for a in agents:
                self._log.info("lattice.spawn_agent", name=a.name, domain=a.domain, id=a.id)
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
