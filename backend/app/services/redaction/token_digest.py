"""ARCH-32 — HMAC digests for matched tokens, keyed by the ARCH-07 lifecycle.

WHAT THIS IS FOR, AND WHAT IT IS NOT FOR
========================================

`redaction_regions.token_digest` answers exactly two questions:

  * "Are these two regions covering the same value?" — so the studio can say
    "this account number appears on four pages" without anyone reading it.
  * "Is the value under this box still the value the detector found?" — so a
    re-detect after a re-extraction can tell a moved box from a changed one.

It is NOT a reversible store and NOT a lookup key. There is no function here
that takes a digest and returns a token, and there must never be one. If a
future phase wants "show me every document containing this card number", the
answer is a search over live documents, not a rainbow table of our own making.

WHY HMAC AND NOT A BARE SHA-256
===============================

The input space is tiny. There are 10^12 Aadhaar numbers and about 10^9 valid
US SSNs; a bare SHA-256 of either is brute-forced on a laptop in minutes. A
keyed MAC makes the digest useless to anyone who has the database and not the
key — which is the entire threat model that justified not storing the token in
the first place.

The key is DERIVED from the ARCH-07 encryption key list rather than being a
new secret in `config.py`. Two reasons. A new secret is a new thing to
rotate, back up, and forget in one environment; and ARCH-07 already solved
"ordered key list, newest first, rotation without downtime" for exactly this
class of material. HKDF with a fixed info string keeps the derived MAC key
domain-separated from the Fernet keys, so a digest can never be confused with
a ciphertext or vice versa.

ROTATION, STATED PLAINLY
========================

Digests are computed under the HEAD key. When the key list rotates, digests
written before the rotation no longer compare equal to digests written after
it. This is correct and it is not a bug to be papered over:

  * Comparison is only ever meaningful WITHIN one job, and a job's regions are
    all written in one detect run under one key.
  * A cross-job comparison after a rotation returns "different", which is the
    safe answer — it produces an extra region, never a missed one.
  * `key_generation` is stored alongside so a reader can see WHY two digests
    of the same value differ, rather than concluding the value changed.

Trying to keep old digests comparable would mean either re-deriving them on
rotation (which requires the plaintext, which we do not have — by design) or
keeping every historical key alive forever (which is not rotation).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Optional

__all__ = [
    "DIGEST_INFO",
    "TokenDigestError",
    "normalize_token",
    "token_digest",
    "key_generation",
]

#: Domain separation. Changing this string invalidates every existing digest,
#: which is a schema change, not a tweak.
DIGEST_INFO: bytes = b"flowpilot/arch32/redaction-token-digest/v1"

_SEPARATORS = re.compile(r"[\s\-\u00a0\u2010-\u2015_.,/\\()\[\]]+")


class TokenDigestError(RuntimeError):
    """No key material is available. Never carries the token."""


def normalize_token(token: str) -> str:
    """Fold a token so `4111 1111 1111 1111` and `4111-1111-...` agree.

    The same fold `leakcheck.fold` applies, and for the same reason: a value
    printed two ways on two pages is one value, and two regions that do not
    compare equal would tell the studio otherwise.
    """
    return _SEPARATORS.sub("", token).casefold()


def _hkdf_sha256(material: bytes, *, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF with an empty salt. Stdlib only, no new dependency."""
    prk = hmac.new(b"\x00" * hashlib.sha256().digest_size, material, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def _head_key_material() -> bytes:
    from app.core.config import settings

    keys = getattr(settings, "encryption_key_list", None) or []
    if not keys:
        raise TokenDigestError(
            "No encryption keys are configured, so a token digest cannot be "
            "keyed. Set EMAIL_ENCRYPTION_KEYS. Storing an unkeyed digest "
            "instead would put a brute-forceable hash of every detected "
            "identifier in the database."
        )
    return str(keys[0]).encode("utf-8")


def key_generation() -> str:
    """A short, non-secret fingerprint of the key a digest was computed under.

    Stored beside the digest so that two digests of the same value differing
    across a rotation is diagnosable rather than mysterious. Twelve hex
    characters of a hash of the key — enough to distinguish generations,
    far too little to attack.
    """
    return hashlib.sha256(_head_key_material()).hexdigest()[:12]


def token_digest(token: str, *, key_material: Optional[bytes] = None) -> str:
    """HMAC-SHA256 hex digest of the normalised token, under the head key.

    `key_material` exists for the gates, which must be able to assert that
    two different keys produce two different digests of the same token
    without configuring two environments.
    """
    normalised = normalize_token(token)
    if not normalised:
        raise TokenDigestError("refusing to digest an empty token")

    material = key_material if key_material is not None else _head_key_material()
    mac_key = _hkdf_sha256(material, info=DIGEST_INFO)
    return hmac.new(mac_key, normalised.encode("utf-8"), hashlib.sha256).hexdigest()