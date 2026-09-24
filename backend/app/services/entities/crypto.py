"""ARCH42-S1:crypto — identifier HMACs that survive key rotation.

WHY NOT app/services/redaction/token_digest
===========================================

ARCH-32's digest says, in its own docstring, that it is NOT a lookup key and
stops comparing equal after a key rotation. An entity identifier IS a lookup
key: "does this workspace already hold this PAN?" is the whole point. So:

  * the MAC key is derived per configured encryption key (HKDF, a domain
    separated info string), not from the head key alone;
  * a LOOKUP computes the digest under every configured key, so a value
    stored before a rotation is still found while the old key is configured;
  * every row records its key GENERATION, and the nightly sweep re-keys rows
    to the head generation by decrypting their stored value;
  * Aadhaar rows keep no value (HMAC + last four only), so they cannot be
    re-keyed: once the old key is removed they stop matching, which fails
    safe (a new record and a review), never towards a wrong link.

No plaintext identifier is ever stored. `display_ciphertext` is the MASKED
form, encrypted; `value_ciphertext` is the full value, encrypted, kept only so
rotation can re-key (and never for Aadhaar).
"""

from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache
from typing import Optional

from app.services.entities.vocabulary import HMAC_INFO


class IdentifierKeyError(RuntimeError):
    """No key material. Never carries the value."""


def _configured_keys() -> tuple[str, ...]:
    from app.core.config import settings

    keys = tuple(str(k) for k in (getattr(settings, "encryption_key_list", None) or []))
    if not keys:
        raise IdentifierKeyError(
            "No encryption keys are configured (EMAIL_ENCRYPTION_KEYS). An "
            "unkeyed identifier digest would be a brute-forceable hash of every "
            "PAN and Aadhaar number in the workspace."
        )
    return keys


@lru_cache(maxsize=16)
def _derive(material: str) -> tuple[str, bytes]:
    from app.services.redaction.token_digest import _hkdf_sha256

    raw = material.encode("utf-8")
    generation = hashlib.sha256(b"flowpilot/arch42/key-generation\x00" + raw).hexdigest()[:16]
    return generation, _hkdf_sha256(raw, info=HMAC_INFO)


def _mac(key: bytes, kind: str, value: str) -> str:
    return hmac.new(key, f"{kind}\x1f{value}".encode("utf-8"), hashlib.sha256).hexdigest()


def head_generation() -> str:
    return _derive(_configured_keys()[0])[0]


def head_digest(kind: str, value: str) -> tuple[str, str]:
    """(generation, hex digest) under the newest key. What a new row stores."""
    generation, key = _derive(_configured_keys()[0])
    return generation, _mac(key, kind, value)


def lookup_digests(kind: str, value: str) -> list[str]:
    """The value's digest under EVERY configured key, newest first."""
    return [_mac(_derive(material)[1], kind, value) for material in _configured_keys()]


def digest_under(generation: str, kind: str, value: str) -> Optional[str]:
    for material in _configured_keys():
        gen, key = _derive(material)
        if gen == generation:
            return _mac(key, kind, value)
    return None


def seal(text: str) -> str:
    from app.core.encryption import encrypt_secret

    return encrypt_secret(text)


def open_sealed(ciphertext: Optional[str]) -> Optional[str]:
    if not ciphertext:
        return None
    from app.core.encryption import decrypt_secret

    try:
        return decrypt_secret(ciphertext)
    except Exception:  # noqa: BLE001 - a rotated-away key loses one display, not the page
        return None
