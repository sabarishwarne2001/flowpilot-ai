"""ARCH-50 — Ed25519 signatures for the offline licence and the release manifest.

ARCH50-S1:signing

`cryptography` (already pinned) provides Ed25519; nothing here makes a network call, so a licence and a release
verify on a machine with no route to anywhere. Keys travel as raw 32-byte values in base64. What is signed is
always BYTES: a licence's payload is serialized canonically (sorted keys, no whitespace, UTF-8) before signing
and before verifying, so the same payload always produces the same bytes on every platform.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

KEY_ALGORITHM = "ed25519"


class SigningError(ValueError):
    """A key or signature is malformed (not: the signature is wrong -- `verify` answers that with False)."""


def canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64decode(value: str, *, expected: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(str(value).strip(), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise SigningError(f"{what} is not base64") from exc
    if len(raw) != expected:
        raise SigningError(f"{what} is {len(raw)} bytes; Ed25519 needs {expected}")
    return raw


def generate_keypair() -> tuple[str, str]:
    """(private key, public key), raw 32 bytes each, base64."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    priv = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
    pub = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(priv).decode("ascii"), base64.b64encode(pub).decode("ascii")


def public_key_of(private_b64: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(_b64decode(private_b64, expected=32, what="the private key"))
    pub = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(pub).decode("ascii")


def key_id(public_b64: str) -> str:
    raw = _b64decode(public_b64, expected=32, what="the public key")
    return f"{KEY_ALGORITHM}:{hashlib.sha256(raw).hexdigest()[:16]}"


def sign(private_b64: str, data: bytes) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(_b64decode(private_b64, expected=32, what="the private key"))
    return base64.b64encode(private.sign(bytes(data))).decode("ascii")


def verify(public_b64: str, data: bytes, signature_b64: str) -> bool:
    """True only for a valid signature by this key over exactly these bytes. Malformed input is False."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        public = Ed25519PublicKey.from_public_bytes(_b64decode(public_b64, expected=32, what="the public key"))
        signature = _b64decode(signature_b64, expected=64, what="the signature")
    except SigningError:
        return False
    try:
        public.verify(signature, bytes(data))
    except InvalidSignature:
        return False
    return True


__all__ = ["KEY_ALGORITHM", "SigningError", "canonical_json", "generate_keypair", "key_id", "public_key_of", "sign",
           "verify"]
