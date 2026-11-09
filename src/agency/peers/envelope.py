"""Envelope binding: build, sign, and cryptographically verify peer messages.

This is a thin, honest layer over :mod:`agency.peer_envelope`. It adds the
kind-specific payload schemas the envelope primitive deliberately omits, and it
keeps the failure order the protocol requires: transport, membership, signature,
digests, replay, expiry, schema, then kind-specific validation.

A return value from :func:`verify_message` means *the bytes were signed by the
key that matches the pinned sender identity and the payload is structurally
well-formed*. It is not admission, not a ballot, not a grant, and not authority.
Callers must still pin the membership epoch/root, durably check replay, and
apply expiry themselves — this module only makes those checks explicit and
ordered, and refuses to proceed when they fail.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agency.peer_envelope import (
    KINDS,
    PREFIX,
    PROTO,
    EnvelopeError,
    canonical_json,
    decode_envelope,
    peer_fingerprint,
    peer_id,
    sign_envelope,
)
from agency.peers.canonical import (
    MAX_MEMBERS,
    SchemaError,
    digest_hex,
    validate_closed,
    validate_consumer_id,
    validate_digest,
    validate_digest_list,
    validate_enum,
    validate_integer,
    validate_list,
    validate_optional_digest,
    validate_optional_integer,
    validate_peer_id,
    validate_proposal_id_format,
    validate_string,
    validate_timestamp,
    validate_u64,
)
from agency.peers.identities import PeerIdentity

__all__ = [
    "HEADER_FIELDS",
    "KINDS",
    "PAYLOAD_FIELDS",
    "PROTO",
    "EnvelopeBindingError",
    "PeerMessage",
    "build_envelope",
    "envelope_digest",
    "payload_digest",
    "signed_bytes",
    "validate_payload",
    "verify_message",
]

HEADER_FIELDS: Final = frozenset(
    {
        "proto",
        "kind",
        "sender_peer_id",
        "membership_epoch",
        "membership_root",
        "msg_id",
        "monotonic_seq",
        "issued_at_ms",
        "expires_at_ms",
        "correlation_id",
        "payload_digest",
    }
)

PAYLOAD_FIELDS: Final = frozenset(
    {
        "proposal",
        "ballot",
        "membership",
        "decision-certificate",
        "grant",
        "heartbeat",
        "recovery-request",
        "recovery-response",
    }
)

_MAX_REASON = 4096
_MAX_EVIDENCE = 64
MAX_MEMBERS_PAYLOAD: Final = MAX_MEMBERS


class EnvelopeBindingError(EnvelopeError):
    """A message violates the peer protocol's payload or binding rules."""


@dataclass(frozen=True, slots=True)
class PeerMessage:
    """A structurally valid, not-yet-trusted peer message.

    ``envelope`` is the decoded wire object. ``payload`` is the validated
    kind-specific payload. No admission decision has been made.
    """

    envelope: dict[str, Any]
    payload: dict[str, Any]
    wire: bytes

    @property
    def kind(self) -> str:
        return str(self.envelope["kind"])

    @property
    def sender_peer_id(self) -> str:
        return str(self.envelope["sender_peer_id"])

    @property
    def msg_id(self) -> str:
        return str(self.envelope["msg_id"])

    @property
    def monotonic_seq(self) -> int:
        return int(self.envelope["monotonic_seq"])

    @property
    def correlation_id(self) -> str:
        return str(self.envelope["correlation_id"])

    @property
    def membership_epoch(self) -> int:
        return int(self.envelope["membership_epoch"])

    @property
    def membership_root(self) -> str:
        return str(self.envelope["membership_root"])

    @property
    def envelope_digest(self) -> str:
        return str(self.envelope["envelope_digest"])

    @property
    def payload_digest(self) -> str:
        return str(self.envelope["payload_digest"])

    @property
    def signature_digest(self) -> str:
        """Digest of the raw signature bytes; a stable citation handle."""

        return digest_hex(base64.b64decode(self.envelope["signature"]["sig"], validate=True))


def payload_digest(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical payload bytes (§5.2)."""

    return digest_hex(canonical_json(dict(payload)))


def envelope_digest(header: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical header projection (§5.2).

    The projection excludes ``signature`` and ``envelope_digest`` and binds the
    payload through ``payload_digest`` alone.
    """

    projection = {field: header[field] for field in HEADER_FIELDS}
    return digest_hex(canonical_json(projection))


def signed_bytes(digest: str) -> bytes:
    """Exact versioned bytes an Ed25519 signature covers (§5.2)."""

    validate_digest(digest, "envelope_digest")
    return PREFIX + bytes.fromhex(digest)


def build_envelope(
    *,
    kind: str,
    sender: PeerIdentity,
    membership_epoch: int,
    membership_root: str,
    monotonic_seq: int,
    issued_at_ms: int,
    expires_at_ms: int,
    correlation_id: str,
    payload: Mapping[str, Any],
) -> bytes:
    """Validate a payload, then sign the exact versioned signed bytes.

    The sender identity in the header is derived from the signing key; a caller
    cannot put a different peer's identity in a message it signs.
    """

    if type(kind) is not str or kind not in KINDS:
        raise EnvelopeBindingError("unknown message kind")
    payload = validate_payload(kind, payload)
    header: dict[str, Any] = {
        "proto": PROTO,
        "kind": kind,
        "sender_peer_id": sender.peer_id,
        "membership_epoch": validate_u64(membership_epoch, "membership_epoch"),
        "membership_root": validate_digest(membership_root, "membership_root"),
        "msg_id": f"{sender.peer_id}:{validate_u64(monotonic_seq, 'monotonic_seq')}",
        "monotonic_seq": validate_u64(monotonic_seq, "monotonic_seq"),
        "issued_at_ms": validate_timestamp(issued_at_ms, "issued_at_ms"),
        "expires_at_ms": validate_timestamp(expires_at_ms, "expires_at_ms"),
        "correlation_id": validate_string(correlation_id, "correlation_id"),
    }
    if header["expires_at_ms"] <= header["issued_at_ms"]:
        raise EnvelopeBindingError("expiry is not after issue")
    # ``sign_envelope`` derives ``payload_digest`` from the payload itself;
    # it must not be supplied in the signing header.
    return sign_envelope(header, payload, sender.key)


def validate_payload(kind: str, payload: Any) -> dict[str, Any]:
    """Closed schema validation for a kind-specific payload.

    Unknown fields are rejected, not ignored. No default is invented: a field
    the schema does not list cannot silently appear in a signed message.
    Schema violations are surfaced as :class:`EnvelopeBindingError`.
    """

    try:
        return _validate_payload(kind, payload)
    except SchemaError as exc:
        raise EnvelopeBindingError(str(exc)) from exc


def _validate_payload(kind: str, payload: Any) -> dict[str, Any]:
    if type(kind) is not str or kind not in KINDS:
        raise EnvelopeBindingError("unknown message kind")
    if not isinstance(payload, Mapping):
        raise EnvelopeBindingError("payload must be an object")
    body = dict(payload)
    if kind == "heartbeat":
        return _validate_heartbeat(body)
    if kind == "proposal":
        return _validate_proposal(body)
    if kind == "ballot":
        return _validate_ballot(body)
    if kind == "membership":
        return _validate_membership(body)
    if kind == "decision-certificate":
        return _validate_certificate(body)
    if kind == "grant":
        return _validate_grant(body)
    if kind == "recovery-request":
        return _validate_recovery_request(body)
    return _validate_recovery_response(body)


def _validate_heartbeat(body: dict[str, Any]) -> dict[str, Any]:
    validate_closed(body, frozenset({"note"}), "heartbeat payload")
    note = body["note"]
    if note is not None:
        validate_string(note, "note", maximum=256)
    return body


def _validate_proposal(body: dict[str, Any]) -> dict[str, Any]:
    validate_closed(
        body,
        frozenset(
            {
                "proposal_id",
                "proposer_peer_id",
                "proposal_version",
                "action",
                "affected_snapshot",
                "membership_epoch",
                "membership_root",
                "policy_version",
                "evidence_manifest",
                "idempotency_key",
                "fencing_base",
                "expires_at_ms",
                "supersedes",
            }
        ),
        "proposal payload",
    )
    validate_proposal_id_format(body["proposal_id"], "proposal_id")
    validate_peer_id(body["proposer_peer_id"], "proposer_peer_id")
    validate_u64(body["proposal_version"], "proposal_version")
    validate_u64(body["membership_epoch"], "membership_epoch")
    validate_digest(body["membership_root"], "membership_root")
    validate_timestamp(body["expires_at_ms"], "expires_at_ms")
    validate_string(body["policy_version"], "policy_version", maximum=128)
    validate_string(body["idempotency_key"], "idempotency_key", maximum=256)
    validate_optional_integer(body["fencing_base"], 0, (1 << 64) - 1, "fencing_base")
    if body["supersedes"] is not None:
        validate_proposal_id_format(body["supersedes"], "supersedes")
    _validate_action(body["action"])
    _validate_affected_snapshot(body["affected_snapshot"])
    _validate_evidence_manifest(body["evidence_manifest"])
    return body


def _validate_action(body: Any) -> dict[str, Any]:
    validate_closed(
        body,
        frozenset({"action_type", "resource", "params_digest", "params_schema_version"}),
        "action",
    )
    validate_string(body["action_type"], "action_type", maximum=64)
    validate_string(body["resource"], "resource", maximum=256)
    validate_digest(body["params_digest"], "params_digest")
    validate_string(body["params_schema_version"], "params_schema_version", maximum=64)
    return cast(dict[str, Any], body)


def _validate_affected_snapshot(body: Any) -> dict[str, Any]:
    validate_closed(
        body,
        frozenset(
            {
                "graph_root_digest",
                "epoch",
                "membership_root",
                "members",
                "derivation",
            }
        ),
        "affected_snapshot",
    )
    validate_digest(body["graph_root_digest"], "graph_root_digest")
    validate_u64(body["epoch"], "epoch")
    validate_digest(body["membership_root"], "membership_root")
    validate_string(body["derivation"], "derivation", maximum=256)
    members = validate_list(body["members"], "members", maximum=MAX_MEMBERS_PAYLOAD)
    seen: set[str] = set()
    for index, member in enumerate(members):
        _validate_affected_member(member, index, seen)
    return cast(dict[str, Any], body)


def _validate_affected_member(member: Any, index: int, seen: set[str]) -> None:
    label = f"members[{index}]"
    validate_closed(
        member,
        frozenset({"peer_id", "consumer_id", "kind", "role", "reason"}),
        label,
    )
    kind = validate_enum(member["kind"], frozenset({"peer", "human-consumer"}), f"{label}.kind")
    peer_id = member["peer_id"]
    consumer_id = member["consumer_id"]
    if kind == "peer":
        if consumer_id is not None:
            raise EnvelopeBindingError(f"{label} must not carry a consumer_id for a peer member")
        validate_peer_id(peer_id, f"{label}.peer_id")
        member_id = peer_id
    else:
        if peer_id is not None:
            raise EnvelopeBindingError(f"{label} must not carry a peer_id for a human consumer")
        validate_consumer_id(consumer_id, f"{label}.consumer_id")
        member_id = consumer_id
    assert isinstance(member_id, str)
    validate_string(member["role"], f"{label}.role", maximum=128)
    validate_string(member["reason"], f"{label}.reason", maximum=_MAX_REASON)
    if member_id in seen:
        raise EnvelopeBindingError(f"duplicate affected member {member_id}")
    seen.add(member_id)


def _validate_evidence_manifest(body: Any) -> list[dict[str, Any]]:
    items = validate_list(body, "evidence_manifest", maximum=_MAX_EVIDENCE)
    seen: set[str] = set()
    for index, item in enumerate(items):
        label = f"evidence_manifest[{index}]"
        validate_closed(
            item,
            frozenset({"name", "sha256", "media_type", "provenance"}),
            label,
        )
        validate_string(item["name"], f"{label}.name", maximum=128)
        digest = validate_digest(item["sha256"], f"{label}.sha256")
        if digest in seen:
            raise EnvelopeBindingError(f"duplicate {label}.sha256")
        seen.add(digest)
        validate_string(item["media_type"], f"{label}.media_type", maximum=64)
        validate_string(item["provenance"], f"{label}.provenance", maximum=256)
    return items


def _validate_ballot(body: dict[str, Any]) -> dict[str, Any]:
    validate_closed(
        body,
        frozenset(
            {
                "proposal_id",
                "proposal_version",
                "voter_peer_id",
                "decision",
                "affected_role",
                "evidence_cited",
                "human_input_ref",
                "ballot_seq",
                "expires_at_ms",
            }
        ),
        "ballot payload",
    )
    validate_proposal_id_format(body["proposal_id"], "proposal_id")
    validate_u64(body["proposal_version"], "proposal_version")
    validate_peer_id(body["voter_peer_id"], "voter_peer_id")
    validate_enum(body["decision"], frozenset({"approve", "deny", "abstain"}), "decision")
    validate_enum(
        body["affected_role"],
        frozenset({"affected-consumer", "peer-voter"}),
        "affected_role",
    )
    validate_digest_list(body["evidence_cited"], "evidence_cited", maximum=_MAX_EVIDENCE)
    validate_optional_digest(body["human_input_ref"], "human_input_ref")
    validate_u64(body["ballot_seq"], "ballot_seq")
    validate_timestamp(body["expires_at_ms"], "expires_at_ms")
    return body


def _validate_membership(body: dict[str, Any]) -> dict[str, Any]:
    validate_closed(
        body,
        frozenset(
            {
                "epoch",
                "members",
                "removed",
                "policy_version",
                "prev_epoch_digest",
            }
        ),
        "membership payload",
    )
    validate_u64(body["epoch"], "epoch")
    validate_string(body["policy_version"], "policy_version", maximum=128)
    validate_digest(body["prev_epoch_digest"], "prev_epoch_digest")
    members = validate_list(body["members"], "members", maximum=MAX_MEMBERS_PAYLOAD)
    seen: set[str] = set()
    for index, member in enumerate(members):
        label = f"members[{index}]"
        validate_closed(
            member,
            frozenset({"peer_id", "public_key", "roles", "joined_epoch"}),
            label,
        )
        validate_peer_id(member["peer_id"], f"{label}.peer_id")
        validate_digest(member["public_key"], f"{label}.public_key")
        if member["peer_id"] in seen:
            raise EnvelopeBindingError(f"duplicate member {member['peer_id']}")
        seen.add(member["peer_id"])
        roles = validate_list(member["roles"], f"{label}.roles", maximum=8)
        for role_index, role in enumerate(roles):
            validate_string(role, f"{label}.roles[{role_index}]", maximum=64)
        validate_u64(member["joined_epoch"], f"{label}.joined_epoch")
    removed = validate_list(body["removed"], "removed", maximum=MAX_MEMBERS_PAYLOAD)
    for index, entry in enumerate(removed):
        label = f"removed[{index}]"
        validate_closed(
            entry,
            frozenset({"peer_id", "removed_epoch", "reason_digest"}),
            label,
        )
        validate_peer_id(entry["peer_id"], f"{label}.peer_id")
        validate_u64(entry["removed_epoch"], f"{label}.removed_epoch")
        validate_digest(entry["reason_digest"], f"{label}.reason_digest")
    return body


def _validate_certificate(body: dict[str, Any]) -> dict[str, Any]:
    validate_closed(
        body,
        frozenset(
            {
                "proposal_id",
                "proposal_version",
                "membership_epoch",
                "membership_root",
                "outcome",
                "eligible_set_digest",
                "ballot_refs",
                "human_input_refs",
                "tally",
                "derived_at_ms",
            }
        ),
        "decision-certificate payload",
    )
    validate_proposal_id_format(body["proposal_id"], "proposal_id")
    validate_u64(body["proposal_version"], "proposal_version")
    validate_u64(body["membership_epoch"], "membership_epoch")
    validate_digest(body["membership_root"], "membership_root")
    validate_enum(body["outcome"], frozenset({"passed", "denied", "expired"}), "outcome")
    validate_digest(body["eligible_set_digest"], "eligible_set_digest")
    validate_timestamp(body["derived_at_ms"], "derived_at_ms")
    _validate_ballot_refs(body["ballot_refs"])
    _validate_human_input_refs(body["human_input_refs"])
    _validate_tally(body["tally"])
    return body


def _validate_ballot_refs(body: Any) -> list[dict[str, Any]]:
    refs = validate_list(body, "ballot_refs", maximum=MAX_MEMBERS_PAYLOAD)
    for index, ref in enumerate(refs):
        label = f"ballot_refs[{index}]"
        validate_closed(
            ref,
            frozenset({"voter_peer_id", "msg_id", "signature_digest"}),
            label,
        )
        validate_peer_id(ref["voter_peer_id"], f"{label}.voter_peer_id")
        validate_string(ref["msg_id"], f"{label}.msg_id", maximum=128)
        validate_digest(ref["signature_digest"], f"{label}.signature_digest")
    return cast(list[dict[str, Any]], refs)


def _validate_human_input_refs(body: Any) -> list[dict[str, Any]]:
    refs = validate_list(body, "human_input_refs", maximum=MAX_MEMBERS_PAYLOAD)
    for index, ref in enumerate(refs):
        label = f"human_input_refs[{index}]"
        validate_closed(
            ref,
            frozenset(
                {
                    "consumer_id",
                    "input_digest",
                    "attestation_digest",
                    "ingress_identity_binding",
                }
            ),
            label,
        )
        validate_consumer_id(ref["consumer_id"], f"{label}.consumer_id")
        validate_digest(ref["input_digest"], f"{label}.input_digest")
        validate_digest(ref["attestation_digest"], f"{label}.attestation_digest")
        validate_digest(ref["ingress_identity_binding"], f"{label}.ingress_identity_binding")
    return cast(list[dict[str, Any]], refs)


def _validate_tally(body: Any) -> dict[str, Any]:
    validate_closed(
        body,
        frozenset(
            {
                "approve",
                "deny",
                "abstain",
                "participation",
                "coverage",
            }
        ),
        "tally",
    )
    for name in ("approve", "deny", "abstain"):
        validate_integer(body[name], 0, MAX_MEMBERS_PAYLOAD, f"tally.{name}")
    participation = validate_list(
        body["participation"], "tally.participation", maximum=MAX_MEMBERS_PAYLOAD
    )
    for index, entry in enumerate(participation):
        validate_string(entry, f"tally.participation[{index}]", maximum=128)
    coverage = validate_list(body["coverage"], "tally.coverage", maximum=MAX_MEMBERS_PAYLOAD)
    for index, entry in enumerate(coverage):
        label = f"tally.coverage[{index}]"
        validate_closed(
            entry,
            frozenset({"peer_id", "kind", "satisfied_by"}),
            label,
        )
        validate_peer_id(entry["peer_id"], f"{label}.peer_id")
        validate_enum(entry["kind"], frozenset({"peer", "human-consumer"}), f"{label}.kind")
        validate_enum(
            entry["satisfied_by"],
            frozenset({"ballot", "human-attestation", "missing"}),
            f"{label}.satisfied_by",
        )
    return cast(dict[str, Any], body)


def _validate_grant(body: dict[str, Any]) -> dict[str, Any]:
    validate_closed(
        body,
        frozenset(
            {
                "proposal_id",
                "decision_certificate_digest",
                "action",
                "affected_set_digest",
                "policy_version",
                "voter_certificate",
                "membership_epoch",
                "membership_root",
                "idempotency_key",
                "expected_current",
                "next_token",
                "expires_at_ms",
                "grant_signers",
                "nonce",
            }
        ),
        "grant payload",
    )
    validate_proposal_id_format(body["proposal_id"], "proposal_id")
    validate_digest(body["decision_certificate_digest"], "decision_certificate_digest")
    validate_digest(body["affected_set_digest"], "affected_set_digest")
    validate_string(body["policy_version"], "policy_version", maximum=128)
    validate_string(body["idempotency_key"], "idempotency_key", maximum=256)
    validate_u64(body["membership_epoch"], "membership_epoch")
    validate_digest(body["membership_root"], "membership_root")
    validate_u64(body["expected_current"], "expected_current")
    validate_u64(body["next_token"], "next_token")
    validate_timestamp(body["expires_at_ms"], "expires_at_ms")
    validate_string(body["nonce"], "nonce", maximum=64)
    _validate_action(body["action"])
    signers = validate_list(body["grant_signers"], "grant_signers", maximum=MAX_MEMBERS_PAYLOAD)
    for index, signer in enumerate(signers):
        validate_peer_id(signer, f"grant_signers[{index}]")
    voter_certificate = body["voter_certificate"]
    validate_closed(
        voter_certificate,
        frozenset({"ballot_refs", "human_input_refs"}),
        "voter_certificate",
    )
    _validate_ballot_refs(voter_certificate["ballot_refs"])
    _validate_human_input_refs(voter_certificate["human_input_refs"])
    if body["next_token"] != body["expected_current"] + 1:
        raise EnvelopeBindingError("next_token must be expected_current + 1")
    return body


def _validate_recovery_request(body: dict[str, Any]) -> dict[str, Any]:
    validate_closed(body, frozenset({"need_digests"}), "recovery-request payload")
    validate_digest_list(body["need_digests"], "need_digests", maximum=MAX_MEMBERS_PAYLOAD)
    return body


def _validate_recovery_response(body: dict[str, Any]) -> dict[str, Any]:
    validate_closed(body, frozenset({"messages"}), "recovery-response payload")
    items = validate_list(body["messages"], "messages", maximum=MAX_MEMBERS_PAYLOAD)
    for index, item in enumerate(items):
        if type(item) is not str:
            raise EnvelopeBindingError(f"messages[{index}] must be a base64 string")
        try:
            raw = base64.b64decode(item, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise EnvelopeBindingError(f"messages[{index}] is not base64") from exc
        if base64.b64encode(raw).decode("ascii") != item:
            raise EnvelopeBindingError(f"messages[{index}] is not canonical base64")
    return body


def verify_message(
    wire: bytes,
    public_key: Ed25519PublicKey,
    *,
    transport_check: Callable[[dict[str, Any]], bool],
    membership_check: Callable[[dict[str, Any], Ed25519PublicKey], bool],
    replay_check: Callable[[dict[str, Any]], bool],
    expiry_check: Callable[[dict[str, Any]], bool],
) -> PeerMessage:
    """Verify a signed message's cryptography and payload schema, in order.

    The four external prerequisites are mandatory and are applied in the
    protocol's order: transport, membership, then signature, then digests,
    then replay, then expiry, then payload schema. Failing any of them raises;
    there is no partial-success return. The result is untrusted input.
    """

    envelope = decode_envelope(wire)
    for name, check in (
        ("transport_check", transport_check),
        ("membership_check", membership_check),
        ("replay_check", replay_check),
        ("expiry_check", expiry_check),
    ):
        if not callable(check):
            raise EnvelopeBindingError(f"{name} is required")

    def checked(callback: Callable[..., bool], *args: Any) -> None:
        candidate = dict(envelope)
        if callback(candidate, *args) is not True:
            raise EnvelopeBindingError("external prerequisite failed")

    checked(transport_check)
    checked(membership_check, public_key)
    if envelope["sender_peer_id"] != peer_id(public_key):
        raise EnvelopeBindingError("sender does not match public key")
    if envelope["signature"]["key_id"] != peer_fingerprint(public_key):
        raise EnvelopeBindingError("key fingerprint mismatch")
    try:
        public_key.verify(
            _signature_bytes(envelope["signature"]["sig"]),
            signed_bytes(envelope["envelope_digest"]),
        )
    except InvalidSignature as exc:
        raise EnvelopeBindingError("invalid Ed25519 signature") from exc
    if envelope["payload_digest"] != payload_digest(envelope["payload"]):
        raise EnvelopeBindingError("payload digest mismatch")
    if envelope["envelope_digest"] != envelope_digest(envelope):
        raise EnvelopeBindingError("envelope digest mismatch")
    checked(replay_check)
    checked(expiry_check)
    payload = validate_payload(envelope["kind"], envelope["payload"])
    return PeerMessage(envelope=envelope, payload=payload, wire=wire)


def _signature_bytes(encoded: Any) -> bytes:
    if type(encoded) is not str:
        raise EnvelopeBindingError("invalid signature encoding")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise EnvelopeBindingError("invalid signature encoding") from exc
    if len(raw) != 64 or base64.b64encode(raw).decode("ascii") != encoded:
        raise EnvelopeBindingError("invalid signature length or noncanonical base64")
    return raw
