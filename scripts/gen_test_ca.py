"""Generate a LOCAL TEST-ONLY CA and server certificate for TLS development.

Gap 18 tail: a local test-CA harness so the Agency's HTTP surfaces can be
exercised over TLS without any production PKI. Everything produced here is
FOR LOCAL TESTS ONLY — these certificates are NOT production PKI, carry no
authority, and must NEVER be deployed, trusted outside a test run, or used
to protect real credentials. Production peer certificates from a real PKI
remain an external concern (gaps.md 18).

What it writes into the output directory (fresh keys every run, no network):

  ca.cert.pem      self-signed local CA certificate (~10-year validity)
  ca.key.pem       local CA private key (unencrypted PKCS#8 — test only)
  server.cert.pem  server certificate signed by the local CA (~2-year
                   validity), with SANs for DNS ``localhost``,
                   IP ``127.0.0.1``, and every ``--server-dns`` name
  server.key.pem   server private key (unencrypted PKCS#8 — test only)

Existing files are never overwritten unless ``--force`` is given. The key
type is Ed25519 by default; ``--key-type rsa2048`` produces RSA-2048 keys
for clients that lack Ed25519 certificate support.

Not part of the test suite inputs — tests generate their own material into
temporary directories and verify the chain with cryptography directly.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.hazmat.primitives.asymmetric.types import CertificateIssuerPublicKeyTypes
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

_CA_CERT = "ca.cert.pem"
_CA_KEY = "ca.key.pem"
_SERVER_CERT = "server.cert.pem"
_SERVER_KEY = "server.key.pem"
_CA_SUBJECT = "Agency Local Test CA (not for production)"
_SERVER_SUBJECT = "localhost"
_CA_VALIDITY_DAYS = 3650  # ~10 years; still a disposable local test artifact
_SERVER_VALIDITY_DAYS = 825
KeyTypes = ed25519.Ed25519PrivateKey | rsa.RSAPrivateKey


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _generate_key(key_type: str) -> KeyTypes:
    if key_type == "ed25519":
        return ed25519.Ed25519PrivateKey.generate()
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _sign(builder: x509.CertificateBuilder, key: KeyTypes) -> x509.Certificate:
    if isinstance(key, ed25519.Ed25519PrivateKey):
        return builder.sign(key, None)
    return builder.sign(key, hashes.SHA256())


def _builder(
    subject: x509.Name, public_key: CertificateIssuerPublicKeyTypes, days: int
) -> x509.CertificateBuilder:
    now = datetime.now(UTC) - timedelta(hours=1)  # backdate for local clock skew
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .public_key(public_key)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=days))
        .serial_number(x509.random_serial_number())
    )


def _ca_key_usage() -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=False,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=True,
        crl_sign=True,
        encipher_only=False,
        decipher_only=False,
    )


def build_ca_certificate(key: KeyTypes) -> x509.Certificate:
    """Self-signed local CA: CA:TRUE, certificate signing only, ~10-year validity."""
    subject = _name(_CA_SUBJECT)
    return _sign(
        _builder(subject, key.public_key(), _CA_VALIDITY_DAYS)
        .issuer_name(subject)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_ca_key_usage(), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False),
        key,
    )


def build_server_certificate(
    key: KeyTypes,
    ca_certificate: x509.Certificate,
    ca_key: KeyTypes,
    server_dns: list[str],
) -> x509.Certificate:
    """Server leaf signed by the local CA with loopback plus extra DNS SANs."""
    names: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        *(x509.DNSName(name) for name in server_dns if name != "localhost"),
    ]
    return _sign(
        _builder(_name(_SERVER_SUBJECT), key.public_key(), _SERVER_VALIDITY_DAYS)
        .issuer_name(ca_certificate.subject)
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=isinstance(key, rsa.RSAPrivateKey),
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        ),
        ca_key,
    )


def _write(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)  # best effort; on Windows this only toggles the read-only bit


def generate_test_ca(
    output_dir: Path, server_dns: list[str], key_type: str, *, force: bool
) -> dict[str, Path]:
    """Generate the local test CA material into ``output_dir``; fresh keys each run."""
    targets = {name: output_dir / name for name in (_CA_CERT, _CA_KEY, _SERVER_CERT, _SERVER_KEY)}
    existing = [str(path) for path in targets.values() if path.exists()]
    if existing and not force:
        print(
            "refusing to overwrite existing files (use --force): " + ", ".join(existing),
            file=sys.stderr,
        )
        raise SystemExit(2)
    output_dir.mkdir(parents=True, exist_ok=True)

    ca_key = _generate_key(key_type)
    ca_certificate = build_ca_certificate(ca_key)
    server_key = _generate_key(key_type)
    server_certificate = build_server_certificate(server_key, ca_certificate, ca_key, server_dns)

    _write(targets[_CA_CERT], ca_certificate.public_bytes(serialization.Encoding.PEM))
    _write(
        targets[_CA_KEY],
        ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    _write(targets[_SERVER_CERT], server_certificate.public_bytes(serialization.Encoding.PEM))
    _write(
        targets[_SERVER_KEY],
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    for name, path in targets.items():
        print(f"{name}: {path}")
    print("LOCAL TESTS ONLY: never deploy, trust, or ship these certificates.")
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a LOCAL TEST-ONLY CA and server certificate for TLS "
            "development. Not production PKI; never deploy or trust these "
            "certificates outside a local test run."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--server-dns",
        action="append",
        default=[],
        help="extra DNS SAN for the server certificate (repeatable)",
    )
    parser.add_argument(
        "--key-type",
        choices=("ed25519", "rsa2048"),
        default="ed25519",
        help="local CA/server key type (default: ed25519)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing files in the output directory",
    )
    args = parser.parse_args(argv)
    generate_test_ca(args.output_dir, args.server_dns, args.key_type, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
