# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# LICENSE: Personal Use Only
# This source code is made available for personal, non-commercial use only.
# You may read, study, and run this code for personal purposes.
# You may NOT:
#   - Use this code, in whole or in part, in any commercial product or service
#   - Sell, sublicense, or otherwise monetize this code or derivatives of it
#   - Redistribute this code without explicit written permission from ZombieTech LLC
#
# No warranty is provided, express or implied. Use at your own risk.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Generate a private CA and a Redis server certificate for the production stack.

    python scripts/generate_redis_tls.py [--out deploy/secrets/redis-tls] [--host redis]

Writes ca.crt, redis.crt and redis.key. The certificate is valid for the given
host name(s), which must match the name clients use (the compose service name,
`redis`). ca.crt is what the app and workers verify against (REDIS_TLS_CA_FILE);
redis.key never leaves the Redis container's mount. The CA key is not kept: to
renew, generate a new set and roll it out. Refuses to overwrite existing files."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "deploy" / "secrets" / "redis-tls"
_CA_DAYS = 3650
_SERVER_DAYS = 825


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


# ai: claude-sonnet-5 | 2026-09-26
def generate(out_dir: Path, hosts: Sequence[str]) -> list[Path]:
    """Create ca.crt / redis.crt / redis.key in *out_dir* and return their paths."""
    targets = [out_dir / "ca.crt", out_dir / "redis.crt", out_dir / "redis.key"]
    existing = [path for path in targets if path.exists()]
    if existing:
        raise FileExistsError(f"Refusing to overwrite: {', '.join(str(p) for p in existing)}")
    out_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(_name("Job Hunt Assistant Redis CA"))
        .issuer_name(_name("Job Hunt Assistant Redis CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=_CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    server_key = ec.generate_private_key(ec.SECP256R1())
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(_name(hosts[0]))
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=_SERVER_DAYS))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host) for host in hosts]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    targets[0].write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    targets[1].write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    targets[2].write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    _set_permissions(*targets)
    return targets


# The redis image's unprivileged user (uid 999 in redis:*-alpine).
REDIS_UID = 999


def _set_permissions(ca: Path, cert: Path, key: Path) -> None:
    """The Redis container reads these through a bind mount as an unprivileged user,
    so the private key must be readable by that user. As root we can give it to that
    user alone (0600); otherwise fall back to 0644 and say how to tighten it."""
    try:
        os.chmod(ca, 0o644)
        os.chmod(cert, 0o644)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(key, REDIS_UID, REDIS_UID)
            os.chmod(key, 0o600)
        else:
            os.chmod(key, 0o644)
            if hasattr(os, "geteuid"):
                print(
                    f"note: {key.name} is world-readable so the container can read it. "
                    f"To restrict it, run:\n"
                    f"  sudo chown {REDIS_UID}:{REDIS_UID} {key} && sudo chmod 600 {key}",
                    file=sys.stderr,
                )
    except OSError:
        pass  # not supported on every filesystem (e.g. some Windows mounts)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--host", action="append", dest="hosts", help="DNS name clients use (default: redis)")
    args = parser.parse_args(argv)
    try:
        paths = generate(args.out, args.hosts or ["redis"])
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in paths:
        print(f"wrote {path}")
    print("Set REDIS_TLS_CA_FILE to the ca.crt path for the app and workers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
