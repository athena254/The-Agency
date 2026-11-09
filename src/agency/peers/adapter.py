"""Per-peer log adapter: bind one peer identity to one local durable log.

§18 steps 2–3. This adapter is the sanctioned write path from verified protocol
messages into a peer's own :class:`~agency.peers.log.PeerLog`. It writes a
record only for a :class:`~agency.peers.envelope.PeerMessage` that came out of
:func:`~agency.peers.envelope.verify_message` — directly, or through
:meth:`PeerLogAdapter.verify_and_append` — so an unsigned or unverified payload
cannot enter the signed log by this route.

The adapter decides nothing about admission, membership, quorum, or authority.
It stores what a verified message said, redacts content-shaped values first
(§9), and binds the exact signed wire bytes by digest. The record's membership
epoch and root are read from the verified envelope, never supplied by the
caller, so an append cannot pin a membership the message did not carry.

Heartbeat messages are not persisted: §15 gives them no authority, cites no
votes, and the decision log is not a liveness sink.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agency.peer_envelope import EnvelopeError
from agency.peers.canonical import SchemaError, validate_digest, validate_u64
from agency.peers.envelope import (
    PeerMessage,
    envelope_digest,
    payload_digest,
    validate_payload,
    verify_message,
)
from agency.peers.identities import PeerIdentity
from agency.peers.log import LogRecord, PeerLog
from agency.peers.redaction import sanitize_log_payload

__all__ = [
    "MESSAGE_KIND_RECORD_KIND",
    "PeerLogAdapter",
    "PeerLogAdapterError",
]

# Verified message kind -> log record kind. A membership-change message is a
# proposal about membership, so it is stored under the ``proposal`` record kind;
# its payload is the membership body, which has no ``proposal_id``. Heartbeats
# are deliberately absent: they are not decision-log records.
MESSAGE_KIND_RECORD_KIND: Final = {
    "proposal": "proposal",
    "ballot": "ballot",
    "membership": "proposal",
    "decision-certificate": "certificate",
    "grant": "grant",
    "recovery-request": "recovery",
    "recovery-response": "recovery",
}


class PeerLogAdapterError(RuntimeError):
    """A write was refused because it is not a verified, loggable message."""


class PeerLogAdapter:
    """Bind one peer identity to one local log; write only verified messages.

    Construction fails when the log belongs to a different peer: an adapter
    cannot rebind a store, and a peer that logs into another peer's store would
    be forging provenance.
    """

    def __init__(self, identity: PeerIdentity, log: PeerLog) -> None:
        if type(identity) is not PeerIdentity:
            raise PeerLogAdapterError("identity must be a PeerIdentity")
        if type(log) is not PeerLog:
            raise PeerLogAdapterError("log must be a PeerLog")
        if log.peer_id != identity.peer_id:
            raise PeerLogAdapterError("log belongs to a different peer")
        self._identity = identity
        self._log = log

    @property
    def identity(self) -> PeerIdentity:
        return self._identity

    @property
    def log(self) -> PeerLog:
        return self._log

    def append_verified(self, message: PeerMessage, *, ts: int | None = None) -> LogRecord:
        """Append a record for an already-verified message.

        ``message`` must be a :class:`PeerMessage` produced by
        :func:`verify_message`. The digest and closed-schema invariants are
        re-checked here; the signature itself is checked by
        :meth:`verify_and_append` or by the caller before it hands the message
        over. Content-shaped payload values are redacted before storage (§9),
        while the original signed bytes are retained and bound by digest.
        """

        self._check_verified(message)
        record_kind = MESSAGE_KIND_RECORD_KIND[message.kind]
        payload = sanitize_log_payload(message.payload)
        return self._log.append(
            record_kind,
            payload,
            membership_epoch=message.membership_epoch,
            membership_root=message.membership_root,
            envelope_digest=message.envelope_digest,
            raw=message.wire,
            ts=ts,
        )

    def verify_and_append(
        self,
        wire: bytes,
        public_key: Ed25519PublicKey,
        *,
        transport_check: Callable[[dict[str, Any]], bool],
        membership_check: Callable[[dict[str, Any], Ed25519PublicKey], bool],
        replay_check: Callable[[dict[str, Any]], bool],
        expiry_check: Callable[[dict[str, Any]], bool],
        ts: int | None = None,
    ) -> LogRecord:
        """Verify a signed message, then append it through the verified path.

        This is the single entry point for live traffic: the message is only
        logged after every external prerequisite and the cryptography passed.
        """

        message = verify_message(
            wire,
            public_key,
            transport_check=transport_check,
            membership_check=membership_check,
            replay_check=replay_check,
            expiry_check=expiry_check,
        )
        return self.append_verified(message, ts=ts)

    @staticmethod
    def _check_verified(message: PeerMessage) -> None:
        """Re-check the invariants that do not need the sender's key."""

        if type(message) is not PeerMessage:
            raise PeerLogAdapterError("append_verified requires a verified PeerMessage")
        if type(message.wire) is not bytes or not message.wire:
            raise PeerLogAdapterError("verified message carries no wire bytes")
        try:
            kind = message.kind
            if kind not in MESSAGE_KIND_RECORD_KIND:
                raise PeerLogAdapterError(f"{kind} messages are not persisted to the decision log")
            if message.envelope_digest != envelope_digest(message.envelope):
                raise PeerLogAdapterError("envelope digest mismatch")
            if message.payload_digest != payload_digest(message.payload):
                raise PeerLogAdapterError("payload digest mismatch")
            validate_payload(kind, message.payload)
            # Validate the remaining bound fields so a hand-built message with
            # a malformed envelope fails here rather than mid-append.
            validate_u64(message.membership_epoch, "membership_epoch")
            validate_digest(message.membership_root, "membership_root")
        except (EnvelopeError, KeyError, TypeError, ValueError, SchemaError) as exc:
            raise PeerLogAdapterError(str(exc)) from exc
