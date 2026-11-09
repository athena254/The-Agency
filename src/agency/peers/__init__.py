"""Known-peer protocol contracts: identity, envelopes, membership, log.

This package is the P2 slice-A protocol checkpoint. It supplies types and pure
functions only — no transport, no executor, no actuator, no consensus engine,
and no authority. A verified message here is well-formed signed input, not
admission, not a ballot of record, and not permission to act.

Approved policy inputs (D-01 – D-05) are reflected as type-level bounds: four
validator peers, one Byzantine fault, a 3/4 application quorum, genesis
membership with epoch bumps, and digest-cited redacted evidence. They are not
implemented as decisions here.
"""

from __future__ import annotations

from agency.peers.canonical import (
    MAX_MEMBERS,
    SchemaError,
    canonical_json,
    digest_hex,
    digest_json,
    validate_closed,
    validate_digest,
    validate_peer_id,
)
from agency.peers.derive import (
    derive_eligible_set_digest,
    derive_proposal_id,
    validate_proposal_id,
)
from agency.peers.envelope import (
    EnvelopeBindingError,
    PeerMessage,
    build_envelope,
    validate_payload,
    verify_message,
)
from agency.peers.identities import (
    ConsumerId,
    PeerIdentity,
    PeerPublicKey,
    consumer_id_from_ingress,
    derive_peer_id,
    peer_fingerprint,
)
from agency.peers.log import (
    ChainVerification,
    LogIntegrityError,
    LogRecord,
    LogScan,
    PeerLog,
    build_record,
    genesis_record,
    scan_records,
    verify_chain,
)
from agency.peers.membership import (
    Member,
    Membership,
    Removal,
    derive_genesis_membership,
    derive_membership_root,
    derive_next_membership,
    eligible_voter_ids,
    membership_from_payload,
    quorum_approvals_required,
)
from agency.peers.redaction import (
    redact_mapping,
    redact_structure,
    redact_text,
    redacted_finding,
    sanitize_log_payload,
)

__all__ = [
    "MAX_MEMBERS",
    "ChainVerification",
    "ConsumerId",
    "EnvelopeBindingError",
    "LogIntegrityError",
    "LogRecord",
    "LogScan",
    "Member",
    "Membership",
    "PeerIdentity",
    "PeerLog",
    "PeerMessage",
    "PeerPublicKey",
    "Removal",
    "SchemaError",
    "build_envelope",
    "build_record",
    "canonical_json",
    "consumer_id_from_ingress",
    "derive_eligible_set_digest",
    "derive_genesis_membership",
    "derive_membership_root",
    "derive_next_membership",
    "derive_peer_id",
    "derive_proposal_id",
    "digest_hex",
    "digest_json",
    "eligible_voter_ids",
    "genesis_record",
    "membership_from_payload",
    "peer_fingerprint",
    "quorum_approvals_required",
    "redact_mapping",
    "redact_structure",
    "redact_text",
    "redacted_finding",
    "sanitize_log_payload",
    "scan_records",
    "validate_closed",
    "validate_digest",
    "validate_payload",
    "validate_peer_id",
    "validate_proposal_id",
    "verify_chain",
    "verify_message",
]
