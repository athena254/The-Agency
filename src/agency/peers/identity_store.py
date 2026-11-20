"""Protected per-runtime storage for one peer signing identity.

A store binds a single directory to a single Ed25519 identity. The private key
is written only to a protected local file (mode ``0o600`` on POSIX; on Windows
the same stdlib call is best-effort because ACL semantics differ), while the
public record holds only a fingerprint and its derived peer id. Separate
directories hold separate identities and cannot be cross-loaded.

Nothing here grants authority. A loaded identity is a signing key, not a
membership, a ballot, or an admission decision.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from agency.peers.canonical import (
    canonical_json,
    validate_closed,
    validate_digest,
    validate_peer_id,
)
from agency.peers.identities import PeerIdentity, derive_peer_id, public_key_from_fingerprint

__all__ = ["PRIVATE_FILENAME", "PUBLIC_FILENAME", "IdentityStore", "IdentityStoreError"]

PRIVATE_FILENAME: Final = "private.key"
PUBLIC_FILENAME: Final = "public.json"

_PRIVATE_BYTES: Final = 32
_RECORD_KEYS: Final = frozenset({"fingerprint", "peer_id"})
_REPARSE_POINT: Final = 0x400


class IdentityStoreError(Exception):
    """A stored identity is missing, malformed, inconsistent, or unprotected."""


def _check_private_file(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise IdentityStoreError("private key file is missing") from exc
    if sys.platform == "win32":
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
            raise IdentityStoreError("private key file must not be a link")
    elif stat.S_IMODE(info.st_mode) & 0o077:
        raise IdentityStoreError("private key file permissions are too broad")
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise IdentityStoreError("private key file must be a regular file")


def _write_private(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    os.chmod(path, 0o600)


def _write_public(path: Path, record: dict[str, str]) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(canonical_json(record))
    except OSError as exc:
        raise IdentityStoreError("cannot write public record") from exc


def _read_public(path: Path) -> tuple[str, str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise IdentityStoreError("public record is missing or unreadable") from exc
    if not data:
        raise IdentityStoreError("public record is empty")
    try:
        parsed: Any = json.loads(data.decode("utf-8"))
        if canonical_json(parsed) != data:
            raise IdentityStoreError("public record is not canonical")
        record = validate_closed(parsed, _RECORD_KEYS, "public record")
        fingerprint = validate_digest(record["fingerprint"], "fingerprint")
        peer_id = validate_peer_id(record["peer_id"], "peer_id")
    except IdentityStoreError:
        raise
    except (ValueError, TypeError) as exc:
        raise IdentityStoreError("public record is malformed") from exc
    return fingerprint, peer_id


def _read_private(path: Path) -> Ed25519PrivateKey:
    _check_private_file(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IdentityStoreError("private key file is unreadable") from exc
    if len(raw) != _PRIVATE_BYTES:
        raise IdentityStoreError("private key file is truncated or malformed")
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (ValueError, TypeError) as exc:
        raise IdentityStoreError("malformed private key") from exc


@dataclass(frozen=True, slots=True)
class IdentityStore:
    """The identity files of one peer runtime inside a single directory."""

    directory: Path

    @property
    def private_path(self) -> Path:
        return self.directory / PRIVATE_FILENAME

    @property
    def public_path(self) -> Path:
        return self.directory / PUBLIC_FILENAME

    def create(self) -> PeerIdentity:
        """Generate an identity and persist its private key and public record."""

        if self.directory.exists() and not self.directory.is_dir():
            raise IdentityStoreError("store path is not a directory")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise IdentityStoreError("cannot create store directory") from exc
        identity = PeerIdentity.generate()
        raw = identity.key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        try:
            _write_private(self.private_path, raw)
        except OSError as exc:
            raise IdentityStoreError("cannot write private key") from exc
        try:
            _write_public(self.public_path, identity.to_record())
        except BaseException:
            self.private_path.unlink(missing_ok=True)
            raise
        return identity

    def load(self) -> PeerIdentity:
        """Rebuild the identity, rejecting any key/record inconsistency."""

        fingerprint, peer_id = _read_public(self.public_path)
        key = _read_private(self.private_path)
        identity = PeerIdentity.from_private_key(key)
        try:
            derived = derive_peer_id(public_key_from_fingerprint(fingerprint))
        except (ValueError, TypeError) as exc:
            raise IdentityStoreError("stored fingerprint is not a valid public key") from exc
        if derived != peer_id:
            raise IdentityStoreError("stored peer id does not match its fingerprint")
        if identity.fingerprint != fingerprint:
            raise IdentityStoreError("private key does not match the stored fingerprint")
        if identity.peer_id != peer_id:
            raise IdentityStoreError("private key does not match the stored peer id")
        return identity
