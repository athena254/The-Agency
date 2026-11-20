"""Storage checkpoint tests for the per-runtime peer identity store."""

from __future__ import annotations

import json
import shutil
import stat
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from agency.peers.canonical import canonical_json
from agency.peers.identities import PeerIdentity, derive_peer_id, public_key_from_fingerprint
from agency.peers.identity_store import (
    PRIVATE_FILENAME,
    PUBLIC_FILENAME,
    IdentityStore,
    IdentityStoreError,
)


def _raw_private(identity: PeerIdentity) -> bytes:
    return identity.key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def _record(path: Path) -> dict[str, str]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_create_load_round_trip_rebuilds_the_identity(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path / "runtime")
    created = store.create()
    assert store.private_path.is_file()
    assert store.public_path.is_file()
    assert _record(store.public_path) == created.to_record()
    loaded = store.load()
    assert loaded.peer_id == created.peer_id
    assert loaded.fingerprint == created.fingerprint
    assert loaded.to_record() == created.to_record()
    assert derive_peer_id(public_key_from_fingerprint(loaded.fingerprint)) == loaded.peer_id
    assert loaded.public.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) == (
        created.public.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    )


def test_create_refuses_to_overwrite_an_existing_store(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path)
    first = store.create()
    with pytest.raises(IdentityStoreError):
        store.create()
    assert store.load().peer_id == first.peer_id


@pytest.mark.skipif(sys.platform != "posix", reason="POSIX mode bits only")
def test_private_file_is_mode_0600_and_stricter_modes_fail_closed(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path)
    store.create()
    assert stat.S_IMODE(store.private_path.stat().st_mode) == 0o600
    store.private_path.chmod(0o644)
    with pytest.raises(IdentityStoreError, match="permissions"):
        store.load()
    store.private_path.chmod(0o600)
    assert store.load().peer_id


def test_missing_files_raise_a_clear_error(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path / "empty")
    with pytest.raises(IdentityStoreError, match="public record"):
        store.load()
    identity = store.create()
    store.public_path.unlink()
    with pytest.raises(IdentityStoreError, match="public record"):
        store.load()
    store.public_path.write_bytes(canonical_json(identity.to_record()))
    store.private_path.unlink()
    with pytest.raises(IdentityStoreError, match="private key"):
        store.load()


@pytest.mark.parametrize("raw", [b"", b"\x00" * 31, b"\x00" * 33, b"not a key at all"])
def test_tampered_private_key_fails_closed(tmp_path: Path, raw: bytes) -> None:
    store = IdentityStore(tmp_path)
    store.create()
    store.private_path.write_bytes(raw)
    with pytest.raises(IdentityStoreError):
        store.load()


def test_tampered_private_key_bytes_with_valid_length_fail_closed(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path)
    created = store.create()
    raw = bytearray(_raw_private(created))
    raw[0] ^= 1
    store.private_path.write_bytes(bytes(raw))
    with pytest.raises(IdentityStoreError, match="fingerprint"):
        store.load()


def test_mismatched_public_record_fails_closed(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path)
    store.create()
    record = _record(store.public_path)
    forged = dict(record, peer_id="peer_" + "0" * 32)
    store.public_path.write_bytes(canonical_json(forged))
    with pytest.raises(IdentityStoreError, match="peer id"):
        store.load()

    store.public_path.write_bytes(canonical_json(record))
    for fingerprint in ("0" * 64, PeerIdentity.generate().fingerprint):
        forged = dict(record, fingerprint=fingerprint)
        store.public_path.write_bytes(canonical_json(forged))
        with pytest.raises(IdentityStoreError, match="peer id"):
            store.load()


def test_malformed_public_record_fails_closed(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path)
    store.create()
    for payload in (
        b"",
        b"{",
        b"[]",
        b'{"fingerprint":"' + b"a" * 64 + b'"}',
        b'{"fingerprint":"' + b"a" * 64 + b'","peer_id":"nope","extra":1}',
    ):
        store.public_path.write_bytes(payload)
        with pytest.raises(IdentityStoreError):
            store.load()


def test_no_private_material_in_the_public_record(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path)
    created = store.create()
    record = _record(store.public_path)
    assert set(record) == {"fingerprint", "peer_id"}
    assert record == created.to_record()
    text = store.public_path.read_text(encoding="utf-8")
    assert _raw_private(created).hex() not in text
    assert _raw_private(created) not in store.public_path.read_bytes()


def test_distinct_stores_hold_distinct_identities(tmp_path: Path) -> None:
    first = IdentityStore(tmp_path / "runtime-a")
    second = IdentityStore(tmp_path / "runtime-b")
    identity_a = first.create()
    identity_b = second.create()
    assert identity_a.peer_id != identity_b.peer_id
    assert first.load().peer_id == identity_a.peer_id
    assert second.load().peer_id == identity_b.peer_id
    shutil.copyfile(second.public_path, first.public_path)
    with pytest.raises(IdentityStoreError):
        first.load()
    assert {p.name for p in first.directory.iterdir()} == {PRIVATE_FILENAME, PUBLIC_FILENAME}
