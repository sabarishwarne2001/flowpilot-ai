"""Time-based one-time passwords (RFC 6238 over RFC 4226), for two-factor sign-in.

Standard library only: HMAC-SHA1, 30-second steps, 6 digits - what every authenticator app
(Google Authenticator, Microsoft Authenticator, 1Password, Authy) expects from an
`otpauth://totp/...` URI. A code is accepted for the current step and one step either side, so
a phone clock up to ~30 seconds off still works; `verify` returns the step that matched, so the
caller can refuse the same code twice (replay).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
WINDOW_STEPS = 1
SECRET_BYTES = 20  # 160 bits, RFC 4226's recommendation


def new_secret() -> str:
    """A fresh shared secret, base32 without padding (what authenticator apps accept)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _key(secret_b32: str) -> bytes:
    cleaned = secret_b32.strip().replace(" ", "").upper()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def hotp(key: bytes, counter: int, digits: int = DIGITS) -> str:
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def step_at(moment: Optional[float] = None) -> int:
    return int((time.time() if moment is None else moment) // STEP_SECONDS)


def code_at(secret_b32: str, moment: Optional[float] = None) -> str:
    return hotp(_key(secret_b32), step_at(moment))


def verify(secret_b32: str, code: str, *, moment: Optional[float] = None,
           not_before_step: Optional[int] = None) -> Optional[int]:
    """The matching step for `code`, or None. Steps at or before `not_before_step` never match."""
    candidate = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(candidate) != DIGITS:
        return None
    key = _key(secret_b32)
    now = step_at(moment)
    for step in range(now - WINDOW_STEPS, now + WINDOW_STEPS + 1):
        if not_before_step is not None and step <= not_before_step:
            continue
        if hmac.compare_digest(hotp(key, step), candidate):
            return step
    return None


def provisioning_uri(secret_b32: str, *, account: str, issuer: str) -> str:
    """The `otpauth://` URI an authenticator app reads from a QR code."""
    label = quote(f"{issuer}:{account}", safe="@:")
    params = urlencode({"secret": secret_b32, "issuer": issuer, "algorithm": "SHA1",
                        "digits": DIGITS, "period": STEP_SECONDS})
    return f"otpauth://totp/{label}?{params}"


__all__ = ["DIGITS", "STEP_SECONDS", "code_at", "hotp", "new_secret", "provisioning_uri", "step_at", "verify"]
