"""Minimal peer authority: registry, signed ballots, replay, quorum (KISS).

Local deterministic derivation only. No transport, no consensus engine.
Uses existing Ed25519 envelope primitives (not HMAC placeholder, cryptography
lib is already a dependency and used by peers/identities.py).

Quorum is D-01: 3-of-4 via membership.quorum_approvals_required.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from agency.peer_envelope import EnvelopeError
from agency.peers.envelope import PeerMessage, build_envelope, verify_message
from agency.peers.identities import PeerIdentity, PeerPublicKey
from agency.peers.membership import (
    Membership,
    eligible_voter_ids,
    quorum_approvals_required,
)


class AuthorityError(ValueError):
    """Ballot, registry, or quorum check failed (fail closed)."""


class PeerRegistry:
    """peer_id -> PeerPublicKey, in-memory + optional SQLite file."""

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path) if db_path else None
        self._keys: dict[str, PeerPublicKey] = {}
        self._seq: dict[str, int] = {}
        self._conn: sqlite3.Connection | None = None
        if self._db_path is not None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS peer_registry "
                "(peer_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS peer_seq "
                "(peer_id TEXT PRIMARY KEY, seq INTEGER NOT NULL)"
            )
            self._conn.commit()
            for peer_id, fp in self._conn.execute("SELECT peer_id, fingerprint FROM peer_registry"):
                self._keys[peer_id] = PeerPublicKey.from_fingerprint(fp)
            for peer_id, seq in self._conn.execute("SELECT peer_id, seq FROM peer_seq"):
                self._seq[peer_id] = int(seq)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def register(self, identity: PeerIdentity) -> PeerPublicKey:
        pub = identity.public
        existing = self._keys.get(pub.peer_id)
        if existing is not None and existing.fingerprint != pub.fingerprint:
            raise AuthorityError("peer_id fingerprint mismatch")
        self._keys[pub.peer_id] = pub
        if self._conn is not None:
            self._conn.execute(
                "INSERT OR REPLACE INTO peer_registry (peer_id, fingerprint) VALUES (?, ?)",
                (pub.peer_id, pub.fingerprint),
            )
            self._conn.commit()
        return pub

    def get(self, peer_id: str) -> PeerPublicKey | None:
        return self._keys.get(peer_id)

    def last_seq(self, peer_id: str) -> int:
        return self._seq.get(peer_id, 0)

    def bump_seq(self, peer_id: str, seq: int) -> None:
        self._seq[peer_id] = seq
        if self._conn is not None:
            self._conn.execute(
                "INSERT OR REPLACE INTO peer_seq (peer_id, seq) VALUES (?, ?)",
                (peer_id, seq),
            )
            self._conn.commit()


class PeerAuthority:
    """Derives governance outcomes only from signed quorum ballots."""

    def __init__(self, membership: Membership, registry: PeerRegistry | None = None):
        self.membership = membership
        self.registry = registry or PeerRegistry()
        self._ballots: dict[str, dict[str, PeerMessage]] = {}  # proposal -> voter -> msg
        self._ballot_seq: dict[tuple[str, str], int] = {}  # (proposal, voter) -> seq

    @property
    def epoch(self) -> int:
        return self.membership.epoch

    def set_membership(self, membership: Membership) -> None:
        # Epoch counter always advances with membership; ballots from older
        # epoch are rejected by submit_ballot epoch check.
        self.membership = membership

    def quorum_required(self) -> int:
        return quorum_approvals_required(self.membership.member_count)

    # -- ballot construction --
    def make_ballot(
        self,
        identity: PeerIdentity,
        proposal_id: str,
        decision: str,
        *,
        proposal_version: int = 1,
        ballot_seq: int | None = None,
        monotonic_seq: int,
        issued_at_ms: int = 1000,
        expires_at_ms: int = 9999999999999,
    ) -> bytes:
        """Sender signs {proposal_id, voter, decision, ballot_seq} via envelope.

        monotonic_seq is the sender's global seq (msg_id), ballot_seq is
        per-proposal. Both are bound inside the signed payload/header.
        """
        self.registry.register(identity)
        if ballot_seq is None:
            ballot_seq = self._ballot_seq.get((proposal_id, identity.peer_id), 0) + 1
        payload = {
            "proposal_id": proposal_id,
            "proposal_version": proposal_version,
            "voter_peer_id": identity.peer_id,
            "decision": decision,
            "affected_role": "peer-voter",
            "evidence_cited": [],
            "human_input_ref": None,
            "ballot_seq": ballot_seq,
            "expires_at_ms": expires_at_ms,
        }
        return build_envelope(
            kind="ballot",
            sender=identity,
            membership_epoch=self.membership.epoch,
            membership_root=self.membership.root,
            monotonic_seq=monotonic_seq,
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            correlation_id=f"{identity.peer_id}:{monotonic_seq}",
            payload=payload,
        )

    # -- verification (replay + forged rejection) --
    def submit_ballot(self, wire: bytes) -> PeerMessage:
        """Verify signature, membership, binding, and monotonic seq. Fail closed."""
        # Need sender + key BEFORE verify_message; decode untrusted first.
        from agency.peer_envelope import decode_envelope

        try:
            env = decode_envelope(wire)
        except EnvelopeError as exc:
            raise AuthorityError(str(exc)) from exc
        sender = env["sender_peer_id"]
        pub = self.registry.get(sender)
        if pub is None:
            raise AuthorityError(f"unknown peer {sender}")
        if sender not in eligible_voter_ids(self.membership):
            raise AuthorityError(f"peer {sender} not eligible in epoch {self.epoch}")

        def membership_check(e: dict[str, Any], key: Any) -> bool:
            return (
                e["membership_epoch"] == self.membership.epoch
                and e["membership_root"] == self.membership.root
                and e["sender_peer_id"] in eligible_voter_ids(self.membership)
            )

        def replay_check(e: dict[str, Any]) -> bool:
            # Global per-sender monotonic seq
            if e["monotonic_seq"] <= self.registry.last_seq(sender):
                return False
            # Per-proposal ballot_seq monotonic
            p = e["payload"]
            return int(p["ballot_seq"]) > self._ballot_seq.get((p["proposal_id"], sender), 0)

        def expiry_check(e: dict[str, Any]) -> bool:
            return bool(e["expires_at_ms"] > e["issued_at_ms"])

        try:
            msg = verify_message(
                wire,
                pub.public_key(),
                transport_check=lambda e: True,
                membership_check=membership_check,
                replay_check=replay_check,
                expiry_check=expiry_check,
            )
        except (EnvelopeError, AuthorityError) as exc:
            raise AuthorityError(str(exc)) from exc

        p = msg.payload
        if p["voter_peer_id"] != msg.sender_peer_id:
            raise AuthorityError("ballot voter does not match sender")
        if msg.membership_epoch != self.epoch:
            raise AuthorityError("stale membership epoch")

        # Persist replay state only AFTER all checks pass.
        self.registry.bump_seq(sender, msg.monotonic_seq)
        self._ballot_seq[(p["proposal_id"], sender)] = int(p["ballot_seq"])
        self._ballots.setdefault(p["proposal_id"], {})[sender] = msg
        return msg

    # -- deterministic outcome --
    def tally(self, proposal_id: str) -> dict[str, int]:
        out = {"approve": 0, "deny": 0, "abstain": 0}
        for msg in self._ballots.get(proposal_id, {}).values():
            out[str(msg.payload["decision"])] += 1
        return out

    def decide(self, proposal_id: str) -> str:
        """'passed' at 3-of-4 approve quorum, 'denied' at deny quorum, else 'open'.

        Fail closed: no outcome without quorum. No caller string can decide.
        """
        t = self.tally(proposal_id)
        q = self.quorum_required()
        if t["approve"] >= q:
            return "passed"
        if t["deny"] >= q:
            return "denied"
        return "open"

    def quorum_met(self, proposal_id: str) -> bool:
        return self.decide(proposal_id) == "passed"


__all__ = ["AuthorityError", "PeerAuthority", "PeerRegistry"]
