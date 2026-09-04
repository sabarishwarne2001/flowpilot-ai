"""Symmetric encryption for stored credentials (ARCH-07 §B.5 Option A).

THE ONLY MODULE IN app/ THAT MAY INSTANTIATE Fernet OR MultiFernet.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings

logger = logging.getLogger("app.core.encryption")

MAX_CIPHERTEXT_LENGTH = 512
MAX_PLAINTEXT_LENGTH = 300

# ---------------------------------------------------------------------------
# ARCH-26 — large secrets (audit decision B1-a)
#
# The two bounds above exist because the columns they protect are String(512):
# SMTP passwords, webhook signing secrets, provider API keys. A ciphertext
# that outgrows its column is a defect worth catching at encrypt time.
#
# A warehouse credential does not fit that shape. A BigQuery service-account
# JSON is ~2.3KB and a Snowflake PKCS#8 private key ~1.7KB, so both exceed
# MAX_PLAINTEXT_LENGTH by an order of magnitude.
#
# Raising MAX_PLAINTEXT_LENGTH would have been one line and the wrong one: it
# silently widens the guard protecting every String(512) column above, and the
# first oversized SMTP password then fails at INSERT with a database error
# rather than at encrypt with a message naming the field. So the large-secret
# path is a separate pair of functions with its own ceiling, writing into
# unbounded Text columns.
# ---------------------------------------------------------------------------

#: 16KB. Comfortably above the largest real credential (a service-account JSON
#: with a 4096-bit key is ~3.2KB) and far below anything that would make a
#: single row awkward. A value this size is a credential; a value larger than
#: this is a mistake, and refusing it is more useful than storing it.
MAX_SECRET_PLAINTEXT_LENGTH = 16384


class EncryptionNotConfiguredError(RuntimeError):
    """No encryption keys are configured."""


class DecryptionError(ValueError):
    """The ciphertext could not be decrypted under any configured key."""


class CiphertextTooLongError(ValueError):
    """The resulting ciphertext would exceed storage column bounds."""


_multi: Optional[MultiFernet] = None
_singles: Optional[list[Fernet]] = None
_lock = threading.Lock()


def _build() -> tuple[MultiFernet, list[Fernet]]:
    keys = settings.encryption_key_list
    if not keys:
        raise EncryptionNotConfiguredError(
            "No encryption keys configured. Set EMAIL_ENCRYPTION_KEYS."
        )
    singles = [Fernet(key.encode()) for key in keys]
    return MultiFernet(singles), singles


def _get() -> tuple[MultiFernet, list[Fernet]]:
    global _multi, _singles
    if _multi is not None and _singles is not None:
        return _multi, _singles
    with _lock:
        if _multi is None or _singles is None:
            _multi, _singles = _build()
        return _multi, _singles


def reset_encryption() -> None:
    """Drop the cached key set. Test-support only."""
    global _multi, _singles
    with _lock:
        _multi = None
        _singles = None


def encrypt_password(plaintext: str) -> str:
    if plaintext is None:
        raise ValueError("Cannot encrypt None")
    if len(plaintext) > MAX_PLAINTEXT_LENGTH:
        raise CiphertextTooLongError(
            f"Password exceeds {MAX_PLAINTEXT_LENGTH} characters."
        )

    multi, _ = _get()
    token = multi.encrypt(plaintext.encode("utf-8")).decode("ascii")

    if len(token) > MAX_CIPHERTEXT_LENGTH:
        raise CiphertextTooLongError(
            f"Ciphertext is {len(token)} characters, exceeding maximum {MAX_CIPHERTEXT_LENGTH}."
        )
    return token


def decrypt_password(ciphertext: str) -> str:
    if not ciphertext:
        raise DecryptionError("Empty ciphertext")

    multi, _ = _get()
    try:
        return multi.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise DecryptionError(
            "Ciphertext could not be decrypted under any configured key."
        ) from exc


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a large credential for a Text column (ARCH-26).

    Same MultiFernet key set as `encrypt_password`, so a key rotation covers
    both and `rotate_ciphertext` works on either. The differences are the
    plaintext ceiling and the absence of a ciphertext ceiling: the destination
    column is Text, so there is no storage bound to enforce.
    """
    if plaintext is None:
        raise ValueError("Cannot encrypt None")
    if not isinstance(plaintext, str):
        raise ValueError("encrypt_secret expects str")
    if len(plaintext) > MAX_SECRET_PLAINTEXT_LENGTH:
        raise CiphertextTooLongError(
            f"Secret exceeds {MAX_SECRET_PLAINTEXT_LENGTH} characters. A "
            "credential this large is almost certainly a mistake."
        )

    multi, _ = _get()
    return multi.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Inverse of `encrypt_secret`.

    Distinct from `decrypt_password` only in name. Both are kept so a reader
    of a call site can tell which column class is involved without following
    the value back to its table.
    """
    if not ciphertext:
        raise DecryptionError("Empty ciphertext")

    multi, _ = _get()
    try:
        return multi.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise DecryptionError(
            "Ciphertext could not be decrypted under any configured key."
        ) from exc


def secret_fingerprint(plaintext: str) -> str:
    """First 12 hex of SHA-256 over the plaintext.

    Displayable. It lets a tenant confirm which key is installed without the
    key being readable back, and it lets an operator confirm two destinations
    share a credential without either being disclosed.

    12 hex characters is 48 bits. That is not a collision-resistant identifier
    and is not used as one — it labels a value the holder already possesses.
    """
    import hashlib

    if plaintext is None:
        raise ValueError("Cannot fingerprint None")
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()[:12]


def decrypting_key_index(ciphertext: str) -> Optional[int]:
    _, singles = _get()
    raw = ciphertext.encode("ascii")
    for index, fernet in enumerate(singles):
        try:
            fernet.decrypt(raw)
            return index
        except InvalidToken:
            continue
    return None


def rotate_ciphertext(ciphertext: str) -> str:
    multi, _ = _get()
    try:
        rotated = multi.rotate(ciphertext.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise DecryptionError(
            "Ciphertext could not be rotated: no configured key decrypts it."
        ) from exc

    if len(rotated) > MAX_CIPHERTEXT_LENGTH:
        raise CiphertextTooLongError(
            f"Rotated ciphertext exceeds {MAX_CIPHERTEXT_LENGTH} characters."
        )
    return rotated


def head_key_fingerprint() -> str:
    import hashlib

    keys = settings.encryption_key_list
    if not keys:
        raise EncryptionNotConfiguredError("No encryption keys configured.")
    return hashlib.sha256(keys[0].encode()).hexdigest()[:12]


def configured_key_count() -> int:
    return len(settings.encryption_key_list)
