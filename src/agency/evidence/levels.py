"""Evidence level transitions and validation.

The evidence ladder is strictly incremental: a finding that has reached
``LEVEL_3_REPEATED`` cannot take a piece of ``LEVEL_5_VERIFIED`` evidence
without first being graded at ``LEVEL_4_REPRODUCED``. Skipping levels is
forbidden so that each claim is independently demonstrated as it climbs.
"""

from __future__ import annotations

from collections.abc import Sequence

from structlog import get_logger

from .store.models import EvidenceEntry, EvidenceLevel

logger = get_logger(__name__)


class EvidenceLevelManager:
    """Enforces the evidence-level lattice.

    A ``None`` starting level is treated as the implicit null hypothesis
    (``LEVEL_0_HYPOTHESIS``): the ladder always begins at the base.
    """

    CHAIN: tuple[EvidenceLevel, ...] = (
        EvidenceLevel.LEVEL_0_HYPOTHESIS,
        EvidenceLevel.LEVEL_1_STATIC,
        EvidenceLevel.LEVEL_2_BEHAVIORAL,
        EvidenceLevel.LEVEL_3_REPEATED,
        EvidenceLevel.LEVEL_4_REPRODUCED,
        EvidenceLevel.LEVEL_5_VERIFIED,
    )

    MAX_STEP: int = 1

    @classmethod
    def is_valid_level(cls, level: EvidenceLevel) -> bool:
        """Whether ``level`` is part of the recognised ladder."""
        return level in cls.CHAIN

    @classmethod
    def can_transition(cls, from_level: EvidenceLevel | None, to_level: EvidenceLevel) -> bool:
        """``True`` only when ``to_level`` is exactly one step up the ladder.

        No skipping, no downgrades, no lateral moves: an entry may only move the
        finding to the highest level that the *previous* evidence demonstrates.
        """
        if not cls.is_valid_level(to_level):
            return False
        base = from_level if from_level is not None else EvidenceLevel.LEVEL_0_HYPOTHESIS
        if not cls.is_valid_level(base):
            return False
        return cls.CHAIN.index(to_level) - cls.CHAIN.index(base) == cls.MAX_STEP

    @classmethod
    def suggest_next(cls, from_level: EvidenceLevel | None) -> EvidenceLevel | None:
        """The level a new piece of evidence should target, if any."""
        base = from_level if from_level is not None else EvidenceLevel.LEVEL_0_HYPOTHESIS
        index = cls.CHAIN.index(base) + cls.MAX_STEP
        if index >= len(cls.CHAIN):
            return None
        return cls.CHAIN[index]

    @classmethod
    def validate_chain(cls, levels: Sequence[EvidenceLevel]) -> bool:
        """``True`` when ``levels`` is a valid contiguous ascent.

        Useful for replaying a finding's evidence history after the fact. The
        implicit null hypothesis (``LEVEL_0_HYPOTHESIS``) is permitted once, as
        the opening anchor, after which every step must climb exactly one rung.
        """
        previous: EvidenceLevel | None = None
        for level in levels:
            if level is EvidenceLevel.LEVEL_0_HYPOTHESIS:
                if previous is not None:
                    return False
                previous = level
                continue
            if not cls.can_transition(previous, level):
                return False
            previous = level
        return True

    @classmethod
    def entry_advances(cls, current_level: EvidenceLevel | None, entry: EvidenceEntry) -> bool:
        """Whether appending ``entry`` legitimately raises the finding's level."""
        return cls.can_transition(current_level, entry.level)

    @classmethod
    def strongest_level(cls, levels: Sequence[EvidenceLevel]) -> EvidenceLevel:
        """Highest level present; ``LEVEL_0_HYPOTHESIS`` when empty."""
        if not levels:
            return EvidenceLevel.LEVEL_0_HYPOTHESIS
        return max(levels, key=lambda level: cls.CHAIN.index(level))


__all__ = ["EvidenceLevelManager"]
