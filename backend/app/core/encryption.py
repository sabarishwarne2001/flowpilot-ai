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