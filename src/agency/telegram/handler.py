"""Telegram message handler."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

import structlog

from agency.agents.demo.agent import DemoAgent
from agency.telegram.adapter import TelegramAdapter
from agency.telegram.config import TelegramConfig
from agency.telegram.profile_store import ProfileStore

logger = structlog.get_logger(__name__)

BETA_CREATION_DISABLED = "Agent creation is disabled in this beta."

_CREATION_ATTEMPT = re.compile(
    r"\b(create|make|build|spawn|add)\b.*\b(agent|bot)\b"
    r"|\b(agent|bot)\b.*\b(create|make|build|spawn|add|new)\b"
    r"|\bnew\s+(agent|bot)\b",
    re.IGNORECASE,
)


class TelegramHandler:
    """Handles incoming Telegram messages and routes them to the Butler."""

    def __init__(
        self, config: TelegramConfig, butler: Any = None, profile_store: ProfileStore | None = None
    ) -> None:
        self._config = config
        self._butler = butler
        self._adapter = TelegramAdapter(config)
        self._demo = DemoAgent()
        self._profiles = profile_store if profile_store is not None else ProfileStore(":memory:")
        self._log = structlog.get_logger(__name__)

    def _beta_rejection_reason(self, message: dict[str, Any]) -> str | None:
        """Shared beta preflight: invite/private/text boundary before any side effect."""
        from_field = message.get("from")
        from_map = from_field if isinstance(from_field, dict) else {}
        user_id = from_map.get("id")
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            return "missing Telegram user ID"
        chat_field = message.get("chat")
        chat_map = chat_field if isinstance(chat_field, dict) else {}
        if chat_map.get("type") != "private":
            return "beta requires private chat"
        chat_id = chat_map.get("id")
        if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id <= 0:
            return "missing Telegram chat ID"
        if chat_id != user_id:
            return "chat id mismatch"
        if user_id not in self._config.allowed_user_ids:
            return "user not invited"
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return "missing text"
        if len(text) > self._config.max_message_length:
            return "message too long"
        return None

    def _is_agent_creation_attempt(self, text: str) -> bool:
        """Conservative plain-English creation check used only for the beta guard."""
        return bool(_CREATION_ATTEMPT.search(text))

    async def handle_update(self, update: dict[str, Any]) -> dict[str, Any]:
        """Handle a single Telegram update."""
        if not isinstance(update, dict):
            if self._config.beta_mode:
                return {"status": "rejected", "reason": "invalid update"}
            return {"status": "ignored", "reason": "no message"}
        message = update.get("message", {})
        if self._config.beta_mode:
            if not isinstance(message, dict) or not message:
                return {"status": "rejected", "reason": "no message"}
            beta_reason = self._beta_rejection_reason(message)
            if beta_reason is not None:
                return {"status": "rejected", "reason": beta_reason}
        if not message:
            return {"status": "ignored", "reason": "no message"}

        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")
        user_id = message.get("from", {}).get("id")
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            return {"status": "rejected", "reason": "missing Telegram user ID"}
        sender = f"telegram:{user_id}"

        if not text:
            return {"status": "ignored", "reason": "empty text"}

        # Check allowed chat IDs
        if self._config.allowed_chat_ids and chat_id not in self._config.allowed_chat_ids:
            return {"status": "rejected", "reason": "chat not allowed"}

        private = message.get("chat", {}).get("type") == "private" and chat_id == user_id
        if message.get("chat", {}).get("type") in ("group", "supergroup"):
            if isinstance(chat_id, bool) or not isinstance(chat_id, int):
                return {"status": "rejected", "reason": "missing Telegram chat ID"}
            conversation_sender = f"telegram:chat:{chat_id}:user:{user_id}"
        else:
            conversation_sender = sender
        try:
            display_name = (self._profiles.get_name(user_id) if private else None) or "Remex"
        except sqlite3.Error:
            self._log.exception("telegram.name_read_failed", user_id=user_id)
            if chat_id:
                await self._adapter.send_message(chat_id, "Profile unavailable. Please try again.")
            return {"status": "error", "reason": "profile unavailable"}

        # Bot commands are answered deterministically from real system
        # state — never through the LLM, so no fiction is possible.
        command = text.strip().lower()
        if self._config.beta_mode:
            proposal_token = command.split(None, 1)[0] if command else ""
            if proposal_token == "/proposals":
                if chat_id:
                    await self._adapter.send_message(chat_id, BETA_CREATION_DISABLED)
                return {"status": "rejected", "reason": "agent creation disabled"}
        if command == "/name" or command.startswith("/name "):
            response = self._name_command(user_id, text.strip()[len("/name") :].strip(), private)
            if chat_id:
                await self._adapter.send_message(chat_id, response)
            return {"status": "ok", "chat_id": chat_id, "command": "/name"}
        if command in ("/agents", "/status", "/whoami", "/proposals", "/start", "/help"):
            if self._config.beta_mode and command == "/proposals":
                if chat_id:
                    await self._adapter.send_message(chat_id, BETA_CREATION_DISABLED)
                return {"status": "rejected", "reason": "agent creation disabled"}
            response = await self._system_answer(command, display_name)
            if chat_id:
                await self._adapter.send_message(chat_id, response)
            return {"status": "ok", "chat_id": chat_id, "command": command}

        # /research <topic> — run the research agent's tool loop
        # (web search → fetch → synthesize → cite → store).
        if command.startswith("/research"):
            args = text.strip()[len("/research") :].strip()
            response = await self._handle_research(args, conversation_sender, display_name)
            if chat_id:
                await self._send_long(chat_id, response)
            return {"status": "ok", "chat_id": chat_id, "command": "/research"}

        # /propose-agent <name> <domain> <capability1> [capability2 ...]
        # (also /propose_agent — Telegram menus can't contain hyphens)
        if command.startswith(("/propose-agent", "/propose_agent")):
            if self._config.beta_mode:
                if chat_id:
                    await self._adapter.send_message(chat_id, BETA_CREATION_DISABLED)
                return {"status": "rejected", "reason": "agent creation disabled"}
            args = text.strip().split(maxsplit=1)[1] if " " in text.strip() else ""
            response = await self._handle_propose_agent(args, sender)
            if chat_id:
                await self._adapter.send_message(chat_id, response)
            return {"status": "ok", "chat_id": chat_id, "command": "/propose-agent"}

        # Plain-English agent creation: detect intent and handle
        # without LLM. This lets users say "create a new agent
        # called X that does Y" instead of memorizing commands.
        # Beta denies creation before any governance path.
        if self._config.beta_mode and self._is_agent_creation_attempt(text):
            if chat_id:
                await self._adapter.send_message(chat_id, BETA_CREATION_DISABLED)
            return {"status": "rejected", "reason": "agent creation disabled"}
        agent_intent = self._detect_agent_creation_intent(text)
        if agent_intent and self._butler:
            self._log.info("telegram.agent_creation_intent", intent=agent_intent, sender=sender)
            response = await self._handle_plain_english_agent_creation(agent_intent, sender)
            self._log.info("telegram.agent_creation_response", response=response[:200])
            if chat_id:
                await self._adapter.send_message(chat_id, response)
            return {"status": "ok", "chat_id": chat_id, "intent": "create_agent"}

        # Process via Butler if available, else demo agent
        if self._butler:
            response = await self._butler.handle_message(
                text, conversation_sender, {"chat_id": chat_id, "assistant_name": display_name}
            )
        else:
            response = await self._demo.handle(text, {"sender": sender, "chat_id": chat_id})

        # Send response
        if chat_id:
            await self._adapter.send_message(chat_id, response)

        return {"status": "ok", "chat_id": chat_id, "response_length": len(response)}

    async def _handle_research(self, args: str, sender: str, display_name: str = "Remex") -> str:
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
        result = await orchestrator.execute_task(
            task.task_id, context={"sender": sender, "assistant_name": display_name}
        )
        output = result.output if isinstance(result.output, str) else str(result.output)
        return f"🔍 *Research complete*\n\n{output}"

    async def _send_long(self, chat_id: int, text: str, limit: int = 3800) -> None:
        """Send text, splitting into multiple messages above the limit."""
        for i in range(0, max(len(text), 1), limit):
            await self._adapter.send_message(chat_id, text[i : i + limit])

    def _name_command(self, user_id: int, value: str, private: bool) -> str:
        """Update this Telegram user's local presentation name only."""
        if not private:
            return "Please use /name in a private chat with this bot."
        if not value:
            current = self._profiles.get_name(user_id) or "Remex"
            return f"My name here is {current}. Use /name <nickname> or /name reset."
        try:
            if value.lower() == "reset":
                self._profiles.reset_name(user_id)
                return "Name reset to Remex for your conversations."
            self._profiles.set_name(user_id, value)
        except ValueError:
            return "Name must be 1–32 characters: letters, digits, spaces or hyphens."
        except Exception:  # noqa: BLE001 — never claim a failed database write succeeded.
            self._log.exception("telegram.name_save_failed", user_id=user_id)
            return "Could not save your name. Please try again."
        return f"You can call me {value.strip()} in your conversations."

    async def _system_answer(self, command: str, display_name: str = "Remex") -> str:
        """Deterministic answers built from real system state."""
        if command == "/agents":
            if self._butler:
                agents = await self._butler.orchestrator.list_agents()
                lines = [f"• {a.name} — domain: {a.domain}" for a in agents]
                return "🤖 *Registered agents (live from the registry)*\n\n" + "\n".join(lines)
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
                f"🤖 I am *{display_name}* — your assistant and the gateway of The Agency, a real "
                "multi-agent system running on this machine. I route your "
                "messages to registered agents and report what they actually "
                "did. Use /agents to see them, /status for system health. "
                "The Telegram bot account is shared; this name is private to your conversations."
            )
        if command == "/proposals":
            return await self._list_proposals()
        if command in ("/start", "/help"):
            if self._config.beta_mode:
                return (
                    f"🤖 *The Agency — {display_name}*\n\n"
                    "Commands:\n"
                    "• /research <topic> — Web research with cited sources\n"
                    "• /agents — List registered agents\n"
                    "• /status — Live system health\n"
                    "• /whoami — What this bot is\n"
                    "• /name <nickname> — Your private name for me (/name reset to undo)\n\n"
                    "Or just chat — plain English routes to the right agent.\n"
                    "The Telegram bot account is shared; this name is private to your conversations."
                )
            return (
                f"🤖 *The Agency — {display_name}*\n\n"
                "Commands:\n"
                "• /research <topic> — Web research with cited sources\n"
                "• /agents — List registered agents\n"
                "• /status — Live system health\n"
                "• /propose_agent <name> <domain> <cap...> — Governance spawn\n"
                "• /proposals — Open governance proposals\n"
                "• /whoami — What this bot is\n"
                "• /name <nickname> — Your private name for me (/name reset to undo)\n\n"
                "Or just chat — plain English routes to the right agent.\n"
                "The Telegram bot account is shared; this name is private to your conversations."
            )
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
            return (
                f"Proposal passed but agent not found in registry. Proposal: {proposal_id[:16]}..."
            )

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

    def _detect_agent_creation_intent(self, text: str) -> dict[str, Any] | None:
        """Detect if the user wants to create a new agent.

        Parses plain English and returns structured intent, or None.
        Examples:
            "create a new agent" → {}
            "make a bot called X" → {"name": "X"}
            "new agent named Y that does Z" → {"name": "Y", "description": "Z"}
            "spawn agent for finance" → {"domain": "finance"}
        """

        lower = text.lower().strip()
        # Remove common filler words
        lower = re.sub(
            r"^(hey|hi|hello|please|can you|could you|i want|i'd like|i need)\s*", "", lower
        )
        lower = re.sub(
            r"^(create|make|build|spawn|add|new)\s+(a|an|the)\s+(new\s+)?(agent|bot|one)\s*",
            "",
            lower,
        )
        lower = re.sub(r"^(create|make|build|spawn|add|new)\s+(agent|bot)\s*", "", lower)
        lower = re.sub(r"^(agent|bot)\s*", "", lower)

        if not lower or lower in ("agent", "a agent", "an agent", "the agent"):
            # User just said "create agent" without details
            return {}

        result = {}

        # Extract name: "called X", "named X", "name is X"
        name_match = re.search(
            r"(?:called|named|name is|name)\s+[\"']?([a-zA-Z][a-zA-Z0-9_-]*)", text, re.IGNORECASE
        )
        if name_match:
            result["name"] = name_match.group(1)

        # Extract domain: "for Y", "in Y", "that does Y"
        domain_match = re.search(
            r"(?:for|in|domain|specializ(?:e|es?)\s+in)\s+[\"']?([a-zA-Z][a-zA-Z0-9_-]*)",
            text,
            re.IGNORECASE,
        )
        if domain_match:
            result["domain"] = domain_match.group(1)

        # Extract capabilities BEFORE purpose (so "that does X" doesn't get consumed by purpose)
        cap_match = re.search(
            r"(?:that\s+(?:does|can)|can\s+do|with\s+capabilities?|capable\s+of)\s+(.+?)(?:\.|$)",
            text,
            re.IGNORECASE,
        )
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

    async def _handle_plain_english_agent_creation(
        self, intent: dict[str, Any], sender: str
    ) -> str:
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
            return (
                f"Proposal passed but agent not found in registry. Proposal: {proposal_id[:16]}..."
            )

        return f"Proposal submitted: {proposal_id[:16]}... Status: {proposal.status}"

    async def process_message(self, message: dict[str, Any]) -> str:
        """Process a message and return the response text."""
        if self._config.beta_mode:
            if not isinstance(message, dict) or not message:
                raise ValueError("no message")
            beta_reason = self._beta_rejection_reason(message)
            if beta_reason is not None:
                raise ValueError(beta_reason)
            beta_text = message.get("text", "")
            if isinstance(beta_text, str):
                normalized = beta_text.strip().lower()
                token = normalized.split(None, 1)[0] if normalized else ""
                if token == "/proposals" or normalized.startswith(
                    ("/propose-agent", "/propose_agent")
                ):
                    return BETA_CREATION_DISABLED
                if self._is_agent_creation_attempt(beta_text):
                    return BETA_CREATION_DISABLED
        text = message.get("text", "")
        user_id = message.get("from", {}).get("id")
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("missing Telegram user ID")
        sender = f"telegram:{user_id}"
        chat = message.get("chat", {})
        private = chat.get("type") == "private" and chat.get("id") == user_id
        if chat.get("type") in ("group", "supergroup"):
            chat_id = chat.get("id")
            if isinstance(chat_id, bool) or not isinstance(chat_id, int):
                raise ValueError("missing Telegram chat ID")
            sender = f"telegram:chat:{chat_id}:user:{user_id}"
        display_name = (self._profiles.get_name(user_id) if private else None) or "Remex"
        if self._butler:
            return str(
                await self._butler.handle_message(text, sender, {"assistant_name": display_name})
            )
        return await self._demo.handle(text, {"sender": sender})

    async def send_response(self, chat_id: int, text: str) -> dict[str, Any]:
        """Send a response to a Telegram chat."""
        return await self._adapter.send_message(chat_id, text)

    async def close(self) -> None:
        await self._adapter.close()
        self._profiles.close()
