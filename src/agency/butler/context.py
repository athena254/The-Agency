"""Layered conversational context assembly for the Butler.

Given one owner and one thread, :class:`ThreadContextEngine` assembles
the prompt context for a turn in strict layers:

1. **Rolling summary** — older turns (covered by the stored summary) as
   one compressed block.
2. **Verbatim window** — the last ``window`` turns after the summary
   point, rendered as role-tagged lines.
3. **Topical recall** — keyword (FTS5) search restricted to *this
   thread's* messages, for anything relevant beyond the window.

Pollution rules enforced here:

* Everything is scoped by ``(owner, thread_id)``; the store's
  ownership checks run before any message is read.
* Sender-global memory is never blended into thread context. If a
  thread is active, thread context is the *only* conversational
  context assembled.
* A thread with no messages yields empty context — never another
  thread's content.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from agency.butler.threads import ThreadMessage, ThreadStore, ThreadSummary
from agency.memory.sms.models import MemoryItem, MemoryTier
from agency.memory.sms.retrieval import RetrievalEngine
from agency.memory.sms.store import MemoryStore

log = structlog.get_logger(__name__)

DEFAULT_WINDOW = 10
DEFAULT_RECALL_LIMIT = 5

# A turn is one user message plus one assistant reply: two stored
# messages. The verbatim window counts stored messages.
_MAX_SUMMARY_CHARS = 2000
_WINDOW_LINE_CHARS = 300


@dataclass(frozen=True)
class ThreadContext:
    """The assembled conversational context for one turn."""

    thread_id: str
    owner: str
    summary: str
    window: str
    topical: str

    @property
    def is_empty(self) -> bool:
        """True when no context layers produced any content."""
        return not (self.summary or self.window or self.topical)

    def render(self) -> str:
        """Render the layered context as one block for the agent prompt."""
        parts: list[str] = []
        if self.summary:
            parts.append(f"Thread summary (older conversation):\n{self.summary}")
        if self.window:
            parts.append(f"Recent conversation:\n{self.window}")
        if self.topical:
            parts.append(f"Earlier in this thread (topically relevant):\n{self.topical}")
        return "\n\n".join(parts)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class ThreadContextEngine:
    """Assemble pollution-safe per-thread context for a turn."""

    def __init__(
        self,
        thread_store: ThreadStore,
        memory_store: MemoryStore,
        *,
        window: int = DEFAULT_WINDOW,
        recall_limit: int = DEFAULT_RECALL_LIMIT,
    ) -> None:
        self._threads = thread_store
        self._memory = memory_store
        self._retrieval = RetrievalEngine(memory_store)
        self._window = window
        self._recall_limit = recall_limit

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @property
    def window(self) -> int:
        """Verbatim window size in stored messages."""
        return self._window

    async def assemble(
        self,
        owner: str,
        thread_id: str,
        message: str,
        *,
        local_only: bool = False,
    ) -> ThreadContext:
        """Assemble context for ``owner``'s turn in ``thread_id``.

        Raises ``KeyError`` when the thread does not exist or belongs to
        another owner (no existence oracle). Never raises for empty
        threads: empty context is a valid result.

        With ``local_only=True``, use only committed thread messages and
        their rolling summary; skip shared-memory topical recall entirely.
        This does not mirror turns into memory. The default retains legacy
        thread-scoped recall. Both modes share the same summary/window logic.

        No-gap guarantee: before rendering, the stored summary is
        extended forward to cover every message behind the verbatim
        window. A summary that lags behind new turns therefore never
        drops the messages between the summary point and the window —
        they are folded into the summary instead.
        """
        rows = self._threads.list_message_rows(owner, thread_id)
        summary = self._threads.get_summary(owner, thread_id)

        summary_text = ""
        covered_rowid = 0
        if summary is not None:
            summary_text = summary.content
            covered_rowid = summary.up_to_rowid

        if rows:
            keep = min(self._window, len(rows))
            window_start_index = len(rows) - keep
            if window_start_index > 0:
                boundary_rowid = rows[window_start_index - 1][0]
                if covered_rowid < boundary_rowid:
                    stored = self.summarize_prefix(owner, thread_id, up_to_rowid=boundary_rowid)
                    summary_text = stored.content
                    covered_rowid = stored.up_to_rowid

        window_rows = [m for rowid, m in rows if rowid > covered_rowid]
        window_text = self._render_window(window_rows[-self._window :])

        topical_text = "" if local_only else await self._topical_recall(owner, thread_id, message)
        return ThreadContext(
            thread_id=thread_id,
            owner=owner,
            summary=summary_text,
            window=window_text,
            topical=topical_text,
        )

    # ------------------------------------------------------------------ #
    # Summarization (deterministic; LLM may replace this later)
    # ------------------------------------------------------------------ #

    def summarize_prefix(
        self,
        owner: str,
        thread_id: str,
        up_to_rowid: int | None = None,
    ) -> ThreadSummary:
        """Build and store the rolling summary for ``owner``'s thread.

        Deterministic extractive baseline: recent turns condensed in
        order. The stored summary covers messages up to and including
        ``up_to_rowid`` (default: exactly everything behind the current
        verbatim window), so the engine never renders covered messages
        twice and never leaves a gap.
        """
        rows = self._threads.list_message_rows(owner, thread_id)
        if not rows:
            raise ValueError("cannot summarize a thread with no messages.")
        if up_to_rowid is None:
            if len(rows) <= self._window:
                raise ValueError("history does not exceed the window yet.")
            # Same boundary compute as assemble: keep exactly the last
            # ``window`` messages verbatim.
            up_to_rowid = rows[-(self._window + 1)][0]
        covered = [m for rowid, m in rows if rowid <= up_to_rowid]
        if not covered:
            raise ValueError("up_to_rowid covers no messages.")
        text = self._summarize_lines(covered)
        return self._threads.set_summary(owner, thread_id, text, up_to_rowid)

    def _summarize_lines(self, rows: list[ThreadMessage]) -> str:
        """Deterministic extractive summary of message rows."""
        lines: list[str] = []
        for m in rows:
            role = "user" if m.role == "user" else "agent"
            lines.append(f"- [{role}] {_clip(m.content, _WINDOW_LINE_CHARS)}")
        text = "\n".join(lines)
        if len(text) > _MAX_SUMMARY_CHARS:
            # Keep the most recent content within the cap.
            text = "…" + text[-_MAX_SUMMARY_CHARS:]
        return text

    # ------------------------------------------------------------------ #
    # Layers
    # ------------------------------------------------------------------ #

    def _render_window(self, rows: list[ThreadMessage]) -> str:
        lines = [f"[{m.role}] {_clip(m.content, _WINDOW_LINE_CHARS)}" for m in rows]
        return "\n".join(lines)

    async def _topical_recall(
        self,
        owner: str,
        thread_id: str,
        message: str,
    ) -> str:
        """Search *this thread's* messages only for relevant older turns.

        Thread messages are mirrored into sender memory under a
        thread-unique agent id (``thread-{thread_id}``), so FTS recall
        is scoped by construction and cannot cross threads.
        """
        try:
            items = await self._retrieval.semantic_search(
                message,
                agent_id=f"thread-{thread_id}",
                limit=self._recall_limit,
            )
        except Exception:  # noqa: BLE001 — recall is best-effort, never blocks a turn.
            log.debug("butler.topical_recall_failed", thread_id=thread_id)
            return ""
        if not items:
            return ""
        lines = [f"- {item.content[:_WINDOW_LINE_CHARS]}" for item in items]
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Persistence of thread turns into thread-scoped recall
    # ------------------------------------------------------------------ #

    async def record_turn(
        self,
        owner: str,
        thread_id: str,
        user_message: str,
        assistant_reply: str,
    ) -> None:
        """Mirror a completed turn into thread-scoped memory for FTS recall.

        Stored under the thread-unique agent id ``thread-{thread_id}``
        with the thread id as a tag. Best-effort: recall is an
        optimization, so persistence failures never fail the turn.

        Ownership is authorized first via the thread store (same
        ``KeyError`` as every other thread operation for unknown or
        foreign threads), so a foreign owner cannot poison this
        thread's recall.
        """
        # Authorize before writing anything: assemble() is protected by
        # list_message_rows, but this write path must check too.
        self._threads.get_thread(owner, thread_id)
        try:
            await self._memory.store(
                MemoryItem(
                    agent_id=f"thread-{thread_id}",
                    content=f"[user] {user_message}\n[agent] {assistant_reply}",
                    tier=MemoryTier.NORMAL,
                    importance=0.6,
                    tags=["butler-thread", thread_id, owner],
                )
            )
        except Exception:  # noqa: BLE001 — best-effort persistence.
            log.debug("butler.thread_turn_store_failed", thread_id=thread_id)
