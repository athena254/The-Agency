"""Durable hash-chained per-peer log tests (§16).

The local chain detects edits, gaps, and torn writes by anyone other than the
store's owner. The two honest-limitation tests at the end record what it cannot
do: a truncated tail with no expected head, and a wholesale rewrite by the
owner, both verify locally. Those are the cases head anchoring exists to catch,
and anchoring is not part of this slice.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.peer_envelope import peer_fingerprint, peer_id
from agency.peers.canonical import SchemaError, digest_hex, digest_json
from agency.peers.log import (
    LOG_SCHEMA_VERSION,
    ZERO_DIGEST,
    LogIntegrityError,
    PeerLog,
    build_record,
    chain_head_record,
    derive_record_digest,
    genesis_record,
    scan_records,
    verify_chain,
)
from agency.peers.membership import derive_genesis_membership

POLICY = "peer-policy/1"
PEER = "peer_" + "a" * 32
TABLE = "peer_log"


def make_keys(count: int = 4) -> list[Ed25519PrivateKey]:
    return [Ed25519PrivateKey.generate() for _ in range(count)]


def make_genesis():
    keys = make_keys()
    members = [
        {
            "peer_id": peer_id(key.public_key()),
            "public_key": peer_fingerprint(key.public_key()),
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for key in keys
    ]
    return derive_genesis_membership(members, policy_version=POLICY)


def genesis_payload(genesis) -> dict[str, Any]:
    return {
        "epoch": genesis.epoch,
        "membership_root": genesis.root,
        "policy_version": genesis.policy_version,
    }


def open_log(path: Path, genesis, *, expected_root: str | None = None) -> PeerLog:
    log = PeerLog(
        path, peer_id=PEER, genesis=genesis_payload(genesis), expected_genesis_root=expected_root
    )
    log.open()
    return log


def append_records(log: PeerLog, genesis, count: int = 3) -> list[Any]:
    records = []
    for index in range(count):
        record = log.append(
            "proposal" if index == 0 else "ballot",
            {"index": index},
            membership_epoch=genesis.epoch,
            membership_root=genesis.root,
            envelope_digest=None,
            ts=1000 + index,
        )
        records.append(record)
    return records


def raw_rows(path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(f"SELECT * FROM {TABLE} ORDER BY seq ASC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def update_row(path: Path, seq: int, **changes: Any) -> None:
    if not changes:
        return
    assignments = ", ".join(f"{key} = ?" for key in changes)
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"UPDATE {TABLE} SET {assignments} WHERE seq = ?", (*changes.values(), seq))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------- #
# Derivation and pure verification
# --------------------------------------------------------------------- #


def test_genesis_record_pins_the_membership_root_and_verifies() -> None:
    genesis = make_genesis()
    record = genesis_record(
        membership_epoch=genesis.epoch,
        membership_root=genesis.root,
        policy_version=genesis.policy_version,
        ts=1000,
    )
    assert record.seq == 0
    assert record.prev == ZERO_DIGEST
    assert record.kind == "genesis"
    assert record.payload["membership_root"] == genesis.root
    result = verify_chain([record])
    assert result.ok
    assert result.records_checked == 1
    assert result.head_digest == record.digest
    assert result.reason is None


def test_chain_binds_each_record_to_the_previous_digest() -> None:
    genesis = make_genesis()
    records = [
        genesis_record(
            membership_epoch=genesis.epoch,
            membership_root=genesis.root,
            policy_version=genesis.policy_version,
            ts=1000,
        )
    ]
    previous = records[0].digest
    for index, kind in enumerate(("proposal", "ballot", "certificate"), start=1):
        record = build_record(
            seq=index,
            prev=previous,
            kind=kind,
            membership_epoch=genesis.epoch,
            membership_root=genesis.root,
            payload={"index": index},
            ts=1000 + index,
        )
        records.append(record)
        previous = record.digest
    result = verify_chain(records)
    assert result.ok
    assert result.records_checked == 4
    assert result.head_seq == 3
    for index, record in enumerate(records[1:], start=1):
        assert record.prev == records[index - 1].digest


def test_record_digest_changes_with_payload_and_timestamp() -> None:
    genesis = make_genesis()
    common = {
        "seq": 1,
        "prev": ZERO_DIGEST,
        "kind": "proposal",
        "membership_epoch": genesis.epoch,
        "membership_root": genesis.root,
    }
    first = build_record(**common, payload={"n": 1}, ts=1)
    second = build_record(**common, payload={"n": 2}, ts=1)
    third = build_record(**common, payload={"n": 1}, ts=2)
    assert len({first.digest, second.digest, third.digest}) == 3


def test_build_record_rejects_unknown_kind_and_malformed_links() -> None:
    genesis = make_genesis()
    with pytest.raises(SchemaError):
        build_record(
            seq=1,
            prev="not-a-digest",
            kind="proposal",
            membership_epoch=genesis.epoch,
            membership_root=genesis.root,
            payload={},
        )
    with pytest.raises(SchemaError):
        build_record(
            seq=1,
            prev=ZERO_DIGEST,
            kind="unknown",
            membership_epoch=genesis.epoch,
            membership_root=genesis.root,
            payload={},
        )


def test_empty_chain_is_unhealthy() -> None:
    result = verify_chain([])
    assert not result.ok
    assert result.reason == "empty log"


def test_scan_records_detects_a_sequence_gap() -> None:
    genesis = make_genesis()
    records = [
        genesis_record(
            membership_epoch=genesis.epoch,
            membership_root=genesis.root,
            policy_version=genesis.policy_version,
            ts=1000,
        )
    ]
    previous = records[0].digest
    for index in (1, 2, 3):
        record = build_record(
            seq=index,
            prev=previous,
            kind="proposal",
            membership_epoch=genesis.epoch,
            membership_root=genesis.root,
            payload={"index": index},
            ts=1000 + index,
        )
        records.append(record)
        previous = record.digest
    rows = [record.to_storage_row() for record in records]
    scan = scan_records(rows)
    assert scan.ok
    assert scan.records == tuple(records)

    gapped = [rows[0], rows[1], rows[3]]
    scan = scan_records(gapped)
    assert not scan.ok
    assert scan.verification.first_bad_seq == 3
    assert scan.verification.records_checked == 2
    assert not scan.verification.torn_tail


# --------------------------------------------------------------------- #
# Durable store behavior
# --------------------------------------------------------------------- #


def test_append_is_durable_and_survives_reopen(tmp_path: Path) -> None:
    genesis = make_genesis()
    path = tmp_path / "peer.db"
    log = open_log(path, genesis)
    assert log.durability == "FULL"
    first, second = append_records(log, genesis, count=2)
    assert first.seq == 1 and second.seq == 2
    assert log.verify().ok
    head = log.head()
    assert head is not None
    assert log.chain_head() == chain_head_record(PEER, head)
    log.close()

    reopened = open_log(path, genesis, expected_root=genesis.root)
    assert reopened.verify().ok
    assert [record.seq for record in reopened.records()] == [0, 1, 2]
    assert reopened.records()[1].payload == {"index": 0}
    assert reopened.records()[2].payload == {"index": 1}
    assert reopened.durability == "FULL"
    reopened.close()


def test_a_new_log_requires_a_genesis_record(tmp_path: Path) -> None:
    with pytest.raises(LogIntegrityError):
        PeerLog(tmp_path / "empty.db", peer_id=PEER).open()


def test_wrong_pinned_genesis_root_is_rejected(tmp_path: Path) -> None:
    genesis = make_genesis()
    path = tmp_path / "peer.db"
    log = open_log(path, genesis)
    log.close()
    with pytest.raises(LogIntegrityError):
        PeerLog(path, peer_id=PEER, expected_genesis_root="f" * 64).open()


def test_new_log_rejects_a_genesis_payload_that_disagrees_with_the_pinned_root(
    tmp_path: Path,
) -> None:
    genesis = make_genesis()
    path = tmp_path / "peer.db"
    with pytest.raises(LogIntegrityError):
        PeerLog(
            path,
            peer_id=PEER,
            genesis=genesis_payload(genesis),
            expected_genesis_root="f" * 64,
        ).open()


def test_log_meta_records_schema_peer_and_genesis(tmp_path: Path) -> None:
    genesis = make_genesis()
    path = tmp_path / "peer.db"
    log = open_log(path, genesis)
    log.close()
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        meta = {row["key"]: row["value"] for row in conn.execute("SELECT * FROM peer_log_meta")}
    finally:
        conn.close()
    assert meta["schema_version"] == str(LOG_SCHEMA_VERSION)
    assert meta["peer_id"] == PEER
    assert meta["genesis_root"] == genesis.root


# --------------------------------------------------------------------- #
# Tamper evidence
# --------------------------------------------------------------------- #


def test_tampered_payload_is_detected_and_blocks_append(tmp_path: Path) -> None:
    genesis = make_genesis()
    path = tmp_path / "peer.db"
    log = open_log(path, genesis)
    append_records(log, genesis, count=2)
    log.close()

    update_row(path, 1, payload=json.dumps({"index": 999}))
    log = open_log(path, genesis, expected_root=genesis.root)
    assert not log.is_healthy
    result = log.verify()
    assert not result.ok
    assert result.first_bad_seq == 1
    assert not result.torn_tail
    with pytest.raises(LogIntegrityError):
        log.append(
            "certificate",
            {"index": 3},
            membership_epoch=genesis.epoch,
            membership_root=genesis.root,
        )
    log.close()


def test_torn_tail_is_reported_as_the_last_record(tmp_path: Path) -> None:
    genesis = make_genesis()
    path = tmp_path / "peer.db"
    log = open_log(path, genesis)
    append_records(log, genesis, count=2)
    log.close()

    update_row(path, 2, digest="a" * 64)
    log = open_log(path, genesis, expected_root=genesis.root)
    result = log.verify()
    assert not result.ok
    assert result.torn_tail
    assert result.first_bad_seq == 2
    assert result.head_seq == 1
    scan = log.recover()
    assert not scan.ok
    # The corrupt row cannot be decoded into a record, so it is retained as a
    # raw torn row rather than a record object.
    assert scan.torn_record is None
    assert scan.torn_row is not None
    assert scan.torn_row["seq"] == 2
    log.close()


def test_tampered_raw_bytes_are_detected(tmp_path: Path) -> None:
    genesis = make_genesis()
    path = tmp_path / "peer.db"
    log = open_log(path, genesis)
    raw = b"exact-signed-wire-bytes"
    record = log.append(
        "ballot",
        {"voter": "peer_" + "b" * 32},
        membership_epoch=genesis.epoch,
        membership_root=genesis.root,
        raw=raw,
    )
    assert record.raw_digest == digest_hex(raw)
    log.close()

    update_row(path, record.seq, raw=b"different-bytes")
    log = open_log(path, genesis, expected_root=genesis.root)
    assert not log.is_healthy
    assert not log.verify().ok
    log.close()


def test_scan_records_is_a_pure_function_of_rows(tmp_path: Path) -> None:
    genesis = make_genesis()
    path = tmp_path / "peer.db"
    log = open_log(path, genesis)
    append_records(log, genesis, count=2)
    log.close()
    rows = raw_rows(path)
    scan = scan_records(rows)
    assert scan.ok
    assert scan.row_count == 3
    assert [record.seq for record in scan.records] == [0, 1, 2]
    # The scan result is independent of the store: mutating rows changes it.
    rows[1]["payload"] = json.dumps({"index": 42})
    assert not scan_records(rows).ok


# --------------------------------------------------------------------- #
# Honest limitations (§16): what the local chain cannot detect
# --------------------------------------------------------------------- #


def test_truncated_tail_verifies_locally_without_an_anchored_head(tmp_path: Path) -> None:
    genesis = make_genesis()
    path = tmp_path / "peer.db"
    log = open_log(path, genesis)
    append_records(log, genesis, count=2)
    log.close()

    conn = sqlite3.connect(path)
    try:
        conn.execute(f"DELETE FROM {TABLE} WHERE seq = 2")
        conn.commit()
    finally:
        conn.close()

    log = open_log(path, genesis, expected_root=genesis.root)
    # Removing the tail leaves a shorter but internally consistent chain. Only
    # an independently held signed head can prove the truncation happened.
    assert log.verify().ok
    log.close()


def test_wholesale_rewrite_by_the_store_owner_verifies_locally(tmp_path: Path) -> None:
    genesis = make_genesis()
    path = tmp_path / "peer.db"
    log = open_log(path, genesis)
    append_records(log, genesis, count=2)
    log.close()

    rows = raw_rows(path)
    edited = dict(rows[1])
    edited["payload"] = json.dumps({"index": 999})
    edited["payload_digest"] = digest_json({"index": 999})
    projection = {
        "seq": edited["seq"],
        "prev": edited["prev"],
        "kind": edited["kind"],
        "membership_epoch": edited["membership_epoch"],
        "membership_root": edited["membership_root"],
        "payload_digest": edited["payload_digest"],
        "envelope_digest": edited["envelope_digest"],
        "raw_digest": edited["raw_digest"],
        "ts": edited["ts"],
    }
    edited["digest"] = derive_record_digest(projection)
    rows[1] = edited
    following = dict(rows[2])
    following["prev"] = edited["digest"]
    following_projection = {
        "seq": following["seq"],
        "prev": following["prev"],
        "kind": following["kind"],
        "membership_epoch": following["membership_epoch"],
        "membership_root": following["membership_root"],
        "payload_digest": following["payload_digest"],
        "envelope_digest": following["envelope_digest"],
        "raw_digest": following["raw_digest"],
        "ts": following["ts"],
    }
    following["digest"] = derive_record_digest(following_projection)
    rows[2] = following

    conn = sqlite3.connect(path)
    try:
        for row in rows:
            conn.execute(
                f"UPDATE {TABLE} SET payload = ?, payload_digest = ?, digest = ?, prev = ?"
                " WHERE seq = ?",
                (row["payload"], row["payload_digest"], row["digest"], row["prev"], row["seq"]),
            )
        conn.commit()
    finally:
        conn.close()

    log = open_log(path, genesis, expected_root=genesis.root)
    # A peer that controls its own store recomputes every digest from the edit
    # forward, so the local chain verifies. This is precisely why §16 requires
    # independently held signed chain heads before any tamper-evidence claim.
    assert log.verify().ok
    log.close()
