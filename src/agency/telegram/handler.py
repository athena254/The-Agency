"""Telegram message handler."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

import structlog

from agency.agents.demo.agent import DemoAgent
from agency.telegram.adapter import TelegramAdapter
from agency.telegram.budget_store import BetaBudgetStore, BudgetUnavailable
from agency.telegram.config import TelegramConfig
from agency.telegram.profile_store import ProfileStore

logger = structlog.get_logger(__name__)

BETA_CREATION_DISABLED = "Agent creation is disabled until peer governance is available."
BETA_UNSUPPORTED_COMMAND = "Unsupported command in this beta."
BETA_MODEL_DISABLED = "Model requests are unavailable in this beta."
BETA_BUDGET_DENIED = "Request limit reached or budget unavailable."
BETA_REPLAY = "Request already received; status uncertain. Please do not resend it."
# These commands are deterministic and do not invoke a model or write a profile.
# Trusted polling still reserves request quota before sending their replies.
_BETA_EXEMPT_COMMANDS = frozenset({"/start", "/help", "/whoami", "/status", "/agents"})

_CREATION_ATTEMPT = re.compile(
    r"\b(create|make|build|spawn|add|set\s+up|spin\s+up|stand\s+up|launch)\b.*\b(agent|bot)\b"
    r"|\b(agent|bot)\b.*\b(create|make|build|spawn|add|new|set\s+up|spin\s+up|stand\s+up|launch)\b"
    r"|\bnew\s+(agent|bot)\b"
    r"|\b(create|make|build|spawn|add)\s+(?:(?:a|an|the|new)\s+)?one\s+(called|named|for)\b",
    re.IGNORECASE,
)


class TelegramHandler:
    """Handles incoming Telegram messages and routes them to the Butler."""

    def __init__(
        self,
        config: TelegramConfig,
        butler: Any = None,
        profile_store: ProfileStore | None = None,
        *,
        budget: BetaBudgetStore | None = None,
        bot_id: str | None = None,
        polling_marker: object | None = None,
    ) -> None:
        self._config = config
        self._budget = budget
        self._bot_id = bot_id
        self._polling_marker = polling_marker
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
        if self._config.allowed_chat_ids and chat_id not in self._config.allowed_chat_ids:
            return "chat not allowed"
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

    async def _handle_polled_update(
        self, update: dict[str, Any], update_id: int, marker: object
    ) -> dict[str, Any]:
        """Ingress from the poller only. JSON fields never supply the marker."""
        if not self._config.beta_mode:
            return await self.handle_update(update)
        if (
            self._polling_marker is None
            or marker is not self._polling_marker
            or type(update_id) is not int
            or update_id < 0
            or type(update.get("update_id")) is not int
            or update["update_id"] != update_id
        ):
            return {"status": "rejected", "reason": "trusted polling required"}
        message = update.get("message")
        if not isinstance(message, dict) or (reason := self._beta_rejection_reason(message)):
            return {
                "status": "rejected",
                "reason": reason if isinstance(message, dict) else "no message",
            }
        text = message["text"]
        token = text.strip().lower().split(None, 1)[0]
        # Every trusted update consumes request quota, including deterministic
        # replies and disabled model paths. The model itself remains unreachable.
        deterministic = (
            token in _BETA_EXEMPT_COMMANDS
            or token == "/name"
            or token in ("/proposals", "/propose-agent", "/propose_agent")
            or self._is_agent_creation_attempt(text)
            or (token.startswith("/") and token != "/research")
        )
        if self._budget is None or self._bot_id is None:
            return {"status": "rejected", "reason": "budget unavailable"}
        user_id = message["from"]["id"]
        try:
            await self._budget.initialize()
            reservation = await self._budget.reserve_request(self._bot_id, update_id, user_id)
            if not reservation.allowed:
                if reservation.duplicate:
                    await self._adapter.send_message(message["chat"]["id"], BETA_REPLAY)
                    return {"status": "rejected", "reason": "duplicate update"}
                await self._adapter.send_message(message["chat"]["id"], BETA_BUDGET_DENIED)
                return {"status": "rejected", "reason": "budget denied"}
            await self._budget.mark_running(
                self._bot_id, update_id, user_id, capability=reservation.capability
            )
            # A send failure leaves RUNNING; replay cannot repeat the profile write.
            if deterministic:
                result = await self._handle_update(
                    update, marker=marker, quota_capability=reservation.capability
                )
            else:
                await self._adapter.send_message(message["chat"]["id"], BETA_MODEL_DISABLED)
                result = {"status": "rejected", "reason": "beta model path disabled"}
            await self._budget.finish_request(
                self._bot_id,
                update_id,
                user_id,
                success=result["status"] == "ok",
                capability=reservation.capability,
            )
            return result
        except (BudgetUnavailable, ValueError, TypeError):
            return {"status": "rejected", "reason": "budget unavailable"}

    async def handle_update(self, update: dict[str, Any]) -> dict[str, Any]:
        """Public dict path has no transport provenance or beta capability."""
        return await self._handle_update(update, marker=None)

    async def _handle_update(
        self,
        update: dict[str, Any],
        *,
        marker: object | None,
        quota_capability: str | None = None,
    ) -> dict[str, Any]:
        """Handle a single Telegram update after optional trusted admission."""
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
            beta_text = message["text"]
            beta_token = beta_text.strip().lower().split(None, 1)[0]
            if beta_token not in _BETA_EXEMPT_COMMANDS:
                if marker is not self._polling_marker or marker is None:
                    return {"status": "rejected", "reason": "trusted polling required"}
                if beta_token == "/name" and (
                    self._budget is None
                    or self._bot_id is None
                    or type(update.get("update_id")) is not int
                    or not self._budget._owns(
                        self._bot_id,
                        update["update_id"],
                        message["from"]["id"],
                        quota_capability,
                    )
                ):
                    return {"status": "rejected", "reason": "budget unavailable"}
                # Model-bearing work remains blocked until every provider
                # attempt and its audit completion are wired end to end.
                if beta_token == "/research" or (
                    beta_token != "/name"
                    and not self._is_agent_creation_attempt(beta_text)
                    and beta_token not in ("/proposals", "/propose-agent", "/propose_agent")
                    and not beta_token.startswith("/")
                ):
                    return {"status": "rejected", "reason": "beta model path disabled"}
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
        command_token = command.split(None, 1)[0] if command else ""
        command_base = command_token.split("@", 1)[0]
        if command_base in ("/proposals", "/propose-agent", "/propose_agent"):
            if chat_id:
                await self._adapter.send_message(chat_id, BETA_CREATION_DISABLED)
            return {"status": "rejected", "reason": "agent creation disabled"}
        if self._is_agent_creation_attempt(text):
            if chat_id:
                await self._adapter.send_message(chat_id, BETA_CREATION_DISABLED)
            return {"status": "rejected", "reason": "agent creation disabled"}
        if (
            self._config.beta_mode
            and command_token.startswith("/")
            and command_token
            not in (
                "/start",
                "/help",
                "/whoami",
                "/status",
                "/agents",
                "/name",
                "/research",
            )
        ):
            if chat_id:
                await self._adapter.send_message(chat_id, BETA_UNSUPPORTED_COMMAND)
            return {"status": "rejected", "reason": "unsupported command"}
        effective_command = command_token if self._config.beta_mode else command
        if effective_command == "/name" or command.startswith("/name "):
            response, saved = self._name_command(
                user_id, text.strip()[len("/name") :].strip(), private
            )
            if chat_id:
                await self._adapter.send_message(chat_id, response)
            return {"status": "ok" if saved else "error", "chat_id": chat_id, "command": "/name"}
        if effective_command in ("/agents", "/status", "/whoami", "/proposals", "/start", "/help"):
            response = await self._system_answer(effective_command, display_name)
            if chat_id:
                await self._adapter.send_message(chat_id, response)
            return {"status": "ok", "chat_id": chat_id, "command": effective_command}

        # /research <topic> — run the research agent's tool loop
        # (web search → fetch → synthesize → cite → store).
        research_command = (
            command_token == "/research"
            if self._config.beta_mode
            else command.startswith("/research")
        )
        if research_command:
            args = text.strip()[len("/research") :].strip()
            response = await self._handle_research(args, conversation_sender, display_name)
            if chat_id:
                await self._send_long(chat_id, response)
            return {"status": "ok", "chat_id": chat_id, "command": "/research"}

        # Creation is refused before the Butler/LLM path until peer governance exists.

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
        if getattr(result, "status", None) != "completed":
            if getattr(result, "status", None) == "timeout":
                return "Research timed out. Please try again."
            return "Research could not be completed. Please try again."
        output = result.output if isinstance(result.output, str) else str(result.output)
        return f"🔍 *Research complete*\n\n{output}"

    async def _send_long(self, chat_id: int, text: str, limit: int = 3800) -> None:
        """Send text, splitting into multiple messages above the limit."""
        for i in range(0, max(len(text), 1), limit):
            await self._adapter.send_message(chat_id, text[i : i + limit])

    def _name_command(self, user_id: int, value: str, private: bool) -> tuple[str, bool]:
        """Return a reply and whether profile storage completed without error."""
        if not private:
            return "Please use /name in a private chat with this bot.", True
        if not value:
            current = self._profiles.get_name(user_id) or "Remex"
            return f"My name here is {current}. Use /name <nickname> or /name reset.", True
        try:
            if value.lower() == "reset":
                self._profiles.reset_name(user_id)
                return "Name reset to Remex for your conversations.", True
            self._profiles.set_name(user_id, value)
        except ValueError:
            return "Name must be 1–32 characters: letters, digits, spaces or hyphens.", True
        except Exception as exc:  # noqa: BLE001 — never claim a failed database write succeeded.
            # SQLite/driver errors can contain private input; log only the class.
            self._log.error("telegram.name_save_failed", error_class=type(exc).__name__)
            return "Could not save your name. Please try again.", False
        return f"You can call me {value.strip()} in your conversations.", True

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
        if command in ("/start", "/help"):
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
        return "Unknown command."

    async def _handle_propose_agent(self, args: str, sender: str) -> str:
        """Reject legacy creation until affected-peer governance exists."""
        _ = args, sender
        return BETA_CREATION_DISABLED

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
        """Reject legacy natural-language creation without synthetic votes."""
        _ = intent, sender
        return BETA_CREATION_DISABLED

    async def process_message(self, message: dict[str, Any]) -> str:
        """Process a message and return the response text."""
        if self._config.beta_mode:
            if not isinstance(message, dict) or not message:
                raise ValueError("no message")
            beta_reason = self._beta_rejection_reason(message)
            if beta_reason is not None:
                raise ValueError(beta_reason)
            beta_text = message.get("text", "")
            normalized = beta_text.strip().lower()
            token = normalized.split(None, 1)[0] if normalized else ""
            if token == "/proposals" or token in ("/propose-agent", "/propose_agent"):
                return BETA_CREATION_DISABLED
            if self._is_agent_creation_attempt(beta_text):
                return BETA_CREATION_DISABLED
            if token.startswith("/") and token not in (
                "/start",
                "/help",
                "/whoami",
                "/status",
                "/agents",
                "/name",
                "/research",
            ):
                return BETA_UNSUPPORTED_COMMAND
            # This API accepts caller-supplied dictionaries, not Telegram
            # transport evidence. Even deterministic commands route through
            # Butler here, so deny rather than create a principal from from.id.
            raise ValueError("trusted polling required")
        text = message.get("text", "")
        user_id = message.get("from", {}).get("id")
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("missing Telegram user ID")
        sender = f"telegram:{user_id}"
        command_token = text.strip().lower().split(maxsplit=1)[0] if text.strip() else ""
        command_base = command_token.split("@", 1)[0]
        if command_base in (
            "/proposals",
            "/propose-agent",
            "/propose_agent",
        ) or self._is_agent_creation_attempt(text):
            return BETA_CREATION_DISABLED
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
