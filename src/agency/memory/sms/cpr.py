"""Compression node (CPR) for the Sovereign Mind System.

Context window reduction without a model dependency. The default strategy is
*extractive*: score sentences by term frequency and keep the highest-value
sentences within a token budget. This keeps compression deterministic, local
and cheap, which matters on the 4GB machines The Agency targets.

A model-backed abstractive summarizer can later be swapped in behind the same
:class:`ContextCompressor` interface.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_MAX_TOKENS = 1024
CHARS_PER_TOKEN = 4
"""Heuristic used when no model tokenizer is available."""

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD_PATTERN = re.compile(r"[A-Za-z0-9']+")

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "here",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "me",
        "might",
        "more",
        "most",
        "must",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "out",
        "over",
        "she",
        "should",
        "so",
        "some",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "up",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)


def estimate_tokens(text: str) -> int:
    """Approximate the token count of ``text`` (4 characters per token)."""

    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


def split_sentences(text: str) -> list[str]:
    """Split ``text`` into non-empty sentence-like fragments."""

    return [
        fragment.strip() for fragment in _SENTENCE_SPLIT.split(text.strip()) if fragment.strip()
    ]


def _content_words(text: str) -> list[str]:
    return [
        word
        for word in (match.lower() for match in _WORD_PATTERN.findall(text))
        if word not in _STOPWORDS
    ]


class ContextCompressor:
    """Extractive context summarization and compression."""

    def __init__(self, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")
        self._max_tokens = max_tokens

    def _rank_sentences(self, text: str) -> list[tuple[int, str, float]]:
        """Score sentences by normalized content-word frequency."""

        sentences = split_sentences(text)
        if len(sentences) <= 1:
            return [(0, sentence, 1.0) for sentence in sentences]

        frequencies = Counter(_content_words(text))
        if not frequencies:
            return [(index, sentence, 0.0) for index, sentence in enumerate(sentences)]

        peak = max(frequencies.values())
        ranked: list[tuple[int, str, float]] = []
        for index, sentence in enumerate(sentences):
            words = _content_words(sentence)
            if not words:
                score = 0.0
            else:
                score = sum(frequencies[word] for word in words) / (len(words) ** 0.5)
                score /= peak
            ranked.append((index, sentence, score))
        return ranked

    def extract_key_points(self, text: str, max_points: int = 5) -> list[str]:
        """Return up to ``max_points`` of the most informative sentences.

        Points are returned in their original document order.
        """

        if max_points < 1:
            return []
        ranked = self._rank_sentences(text)
        best = sorted(ranked, key=lambda entry: entry[2], reverse=True)[:max_points]
        return [sentence for _, sentence, _ in sorted(best, key=lambda entry: entry[0])]

    def summarize(self, text: str, target_length: int) -> str:
        """Extractively summarize ``text`` to roughly ``target_length`` tokens.

        ``target_length`` is a token budget, matching :meth:`compress`.
        """

        if target_length < 1:
            raise ValueError("target_length must be >= 1")
        stripped = text.strip()
        if not stripped or estimate_tokens(stripped) <= target_length:
            return stripped

        ranked = self._rank_sentences(stripped)
        ordered = sorted(ranked, key=lambda entry: entry[2], reverse=True)

        selected: list[tuple[int, str]] = []
        used = 0
        for index, sentence, _score in ordered:
            cost = estimate_tokens(sentence)
            if selected and used + cost > target_length:
                continue
            selected.append((index, sentence))
            used += cost
            if used >= target_length:
                break

        if not selected:
            return self._truncate(stripped, target_length)

        summary = " ".join(sentence for _, sentence in sorted(selected, key=lambda e: e[0]))
        if estimate_tokens(summary) > target_length:
            summary = self._truncate(summary, target_length)
        logger.debug(
            "cpr.summarized",
            source_tokens=estimate_tokens(stripped),
            target_length=target_length,
            result_tokens=estimate_tokens(summary),
        )
        return summary

    def compress(
        self,
        context: str | Sequence[str],
        max_tokens: int | None = None,
    ) -> str:
        """Compress ``context`` to fit within ``max_tokens``.

        ``context`` may be a single string or a sequence of strings (joined by
        newlines). Returns the original text unchanged when it already fits.
        """

        budget = max_tokens if max_tokens is not None else self._max_tokens
        if budget < 1:
            raise ValueError("max_tokens must be >= 1")

        if isinstance(context, str):
            combined = context.strip()
        else:
            combined = "\n".join(part.strip() for part in context if part.strip())

        source_tokens = estimate_tokens(combined)
        if source_tokens <= budget:
            return combined

        compressed = self.summarize(combined, budget)
        logger.info(
            "cpr.compressed",
            source_tokens=source_tokens,
            max_tokens=budget,
            result_tokens=estimate_tokens(compressed),
        )
        return compressed

    @staticmethod
    def _truncate(text: str, max_tokens: int) -> str:
        """Hard-truncate text on a word boundary to fit the token budget."""

        max_chars = max_tokens * CHARS_PER_TOKEN
        if len(text) <= max_chars:
            return text
        clipped = text[:max_chars]
        boundary = clipped.rfind(" ")
        if boundary > 0:
            clipped = clipped[:boundary]
        return clipped.rstrip() + " …"
