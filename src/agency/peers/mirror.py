"""Read-only mirror of legacy governance artifacts into the peer log.

§18 step 3. Legacy ``ConsensusProposal`` and ``Vote`` objects are not peer
protocol messages: they are unsigned, carry no membership epoch or root, and
their ``user`` override is exactly what D-03 removes. This shim copies them
into the new durable log as *observations*, so an operator can audit the legacy
path alongside the signed one.

Every mirror record is written under the ``mirror`` record kind with an
explicit ``authority: none`` marker and a zero membership binding. It is not a
proposal, not a ballot, not a quorum input, and cannot authorize anything. The
legacy engine remains the disabled-for-consequential path (§18 step 4); this
module does not read from it, wire to it, or change it.

The mirror is deliberately not the verified path: nothing here checks a
signature, because there is none. That is why it writes ``mirror`` records
instead of the protocol records the adapter owns.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any, Final

from agency.peers.canonical import digest_json, validate_enum
from agency.peers.log import ZERO_DIGEST, LogRecord, PeerLog
from agency.peers.redaction import sanitize_log_payload

__all__ = [
    "MIRROR_ARTIFACT_TYPES",
    "MIRROR_AUTHORITY",
    "MIRROR_MEMBERSHIP_EPOCH",
    "MIRROR_RECORD_KIND",
    "MIRROR_SOURCE",
    "LegacyMirror",
    "MirrorError",
]

MIRROR_RECORD_KIND: Final = "mirror"
MIRROR_SOURCE: Final = "legacy-governance"
MIRROR_AUTHORITY: Final = "none"
# A legacy artifact has no membership epoch and no root. Zero is not a real
# epoch (genesis is 1) and the all-zero digest is never a real root, so the
# pair is an honest "not bound" sentinel rather than an invented membership.
MIRROR_MEMBERSHIP_EPOCH: Final = 0
MIRROR_ARTIFACT_TYPES: Final = frozenset({"proposal", "ballot"})

_PROPOSAL_REQUIRED: Final = ("proposal_id", "proposer_id")
_BALLOT_REQUIRED: Final = ("voter_id", "proposal_id", "decision")


class MirrorError(RuntimeError):
    """A legacy artifact cannot be mirrored without inventing authority."""


class LegacyMirror:
    """Write legacy proposals and ballots into a peer log as observations only.

    The artifact may be a mapping or any object with a ``to_dict()`` method (the
    legacy dataclasses qualify). The stored payload cites a digest of the
    redacted artifact and states that it carries no authority.
    """

    def __init__(self, log: PeerLog) -> None:
        if type(log) is not PeerLog:
            raise MirrorError("log must be a PeerLog")
        self._log = log

    @property
    def log(self) -> PeerLog:
        return self._log

    def mirror_proposal(self, proposal: Any, *, ts: int | None = None) -> LogRecord:
        """Mirror a legacy proposal as an observation, never as a proposal."""

        artifact = _legacy_mapping(proposal, "proposal", _PROPOSAL_REQUIRED)
        return self._mirror("proposal", artifact, ts=ts)

    def mirror_ballot(self, vote: Any, *, ts: int | None = None) -> LogRecord:
        """Mirror a legacy ballot as an observation, never as a ballot."""

        artifact = _legacy_mapping(vote, "ballot", _BALLOT_REQUIRED)
        return self._mirror("ballot", artifact, ts=ts)

    def _mirror(self, artifact_type: str, artifact: dict[str, Any], *, ts: int | None) -> LogRecord:
        validate_enum(artifact_type, MIRROR_ARTIFACT_TYPES, "artifact_type")
        redacted = sanitize_log_payload(_canonical_legacy(artifact))
        if not isinstance(redacted, dict):
            raise MirrorError("legacy artifact did not canonicalize to an object")
        payload = {
            "source": MIRROR_SOURCE,
            "mirror": True,
            "authority": MIRROR_AUTHORITY,
            "artifact_type": artifact_type,
            "membership_bound": False,
            "legacy_digest": digest_json(redacted),
            "artifact": redacted,
        }
        return self._log.append(
            MIRROR_RECORD_KIND,
            payload,
            membership_epoch=MIRROR_MEMBERSHIP_EPOCH,
            membership_root=ZERO_DIGEST,
            ts=ts,
        )


def _legacy_mapping(artifact: Any, expected: str, required: tuple[str, ...]) -> dict[str, Any]:
    if isinstance(artifact, Mapping):
        mapping = dict(artifact)
    else:
        to_dict = getattr(artifact, "to_dict", None)
        if not callable(to_dict):
            raise MirrorError(f"{expected} must be a mapping or provide to_dict()")
        converted = to_dict()
        if not isinstance(converted, Mapping):
            raise MirrorError(f"{expected} to_dict() did not return a mapping")
        mapping = dict(converted)
    if not mapping:
        raise MirrorError(f"{expected} mapping is empty")
    missing = [field for field in required if field not in mapping]
    if missing:
        raise MirrorError(f"{expected} mapping is missing {', '.join(missing)}")
    return mapping


def _canonical_legacy(value: Any) -> Any:
    """Convert a legacy value to the protocol's canonical data model.

    Floats become deterministic text (canonical JSON is integer-only), datetimes
    become ISO-8601 strings, and enums become their values. Anything else that
    is not JSON-like is refused rather than silently dropped: a mirror that
    loses fields is a false observation.
    """

    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise MirrorError("legacy artifact contains a non-finite float")
        return repr(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return _canonical_legacy(value.value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_legacy(item) for key, item in value.items()}
    if type(value) is list:
        return [_canonical_legacy(item) for item in value]
    if type(value) is tuple:
        return [_canonical_legacy(item) for item in value]
    raise MirrorError(f"legacy artifact contains an unsupported {type(value).__name__}")
