"""Workload identities: one Ed25519 key per identity, nothing shared.

Key custody is NOT demonstrated here. The keys under demo-keys/ are published
on purpose so anyone can run the build and the attack pack. A real deployment
holds each key in a KMS or HSM behind a workload identity. See docs/limits.md.
"""

from __future__ import annotations

from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def generate(keydir: Path, name: str) -> str:
    """Create a key for an identity and return the public key as hex."""
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    keydir.mkdir(parents=True, exist_ok=True)
    (keydir / f"{name}.key").write_text(seed.hex() + "\n")
    return public_hex(key)


def public_hex(key: Ed25519PrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )


def load_private(keydir: Path, name: str) -> Ed25519PrivateKey:
    seed = bytes.fromhex((keydir / f"{name}.key").read_text().strip())
    return Ed25519PrivateKey.from_private_bytes(seed)


def sign(keydir: Path, name: str, data: bytes) -> str:
    return load_private(keydir, name).sign(data).hex()


def verify(public_key_hex: str, data: bytes, signature_hex: str) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        key.verify(bytes.fromhex(signature_hex), data)
        return True
    except (InvalidSignature, ValueError):
        return False
