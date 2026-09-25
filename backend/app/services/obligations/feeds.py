"""ARCH46-S1:feeds — signed, revocable calendar feed tokens. Pure (no database here).

A feed token is a bearer credential in a URL a calendar app polls, so:

  * it is SIGNED: v1.<32 random bytes, base64url>.<HMAC-SHA256 of the random
    part under a key derived (HKDF, domain-separated) from the configured
    encryption keys, 16 bytes, base64url>. A request whose signature does not
    verify under any configured key is refused before the database is asked,
    and the stored hash alone can never be turned back into a working URL;
  * only its SHA-256 is stored (calendar_feed_tokens.token_hash, hex CHECK,
    UNIQUE); the token is shown once, when it is issued, and never logged;
  * every comparison is constant-time, and a malformed, unsigned, unknown,
    revoked, expired or orphaned token gets the IDENTICAL answer (404, same
    body, same headers) -- the URL reveals nothing about why;
  * revocation is a timestamp the next fetch reads: nothing caches a feed
    (Cache-Control: no-store), so it takes effect on the next poll.

Rotating the encryption keys: a token verifies under ANY configured key, so
feeds survive a rotation while the old key stays configured and stop (safely:
404) once it is removed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache
from typing import Optional

from app.services.obligations import vocabulary as v


class FeedKeyError(RuntimeError):
    """No key material to sign with. Never carries a token."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _keys() -> tuple[str, ...]:
    from app.core.config import settings

    keys = tuple(str(k) for k in (getattr(settings, "encryption_key_list", None) or []))
    if not keys:
        raise FeedKeyError("No encryption keys are configured (EMAIL_ENCRYPTION_KEYS); a feed cannot be signed.")
    return keys


@lru_cache(maxsize=16)
def _derived(material: str) -> bytes:
    from app.services.redaction.token_digest import _hkdf_sha256

    return _hkdf_sha256(material.encode("utf-8"), info=v.FEED_HKDF_INFO)


def _mac(key: bytes, body: str) -> str:
    return _b64(hmac.new(key, f"{v.FEED_TOKEN_VERSION}.{body}".encode("ascii"), hashlib.sha256).digest()[:16])


def issue() -> str:
    """A new token (shown once): v1.<random>.<signature under the head key>."""
    body = secrets.token_urlsafe(32)
    return f"{v.FEED_TOKEN_VERSION}.{body}.{_mac(_derived(_keys()[0]), body)}"


def signature_valid(token: str) -> bool:
    """Constant-time signature check under every configured key (rotation-safe)."""
    try:
        version, body, mac = str(token or "").split(".")
    except ValueError:
        return False
    if version != v.FEED_TOKEN_VERSION or len(mac) != v.FEED_MAC_CHARS or not 40 <= len(body) <= 64:
        return False
    try:
        keys = _keys()
    except FeedKeyError:
        return False
    ok = False
    for material in keys:
        # No early exit: every key is tried, so the time does not say which one matched.
        ok = hmac.compare_digest(_mac(_derived(material), body), mac) or ok
    return ok


def digest(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def feed_path(token: str) -> str:
    return f"/api/v1/public/calendar-feeds/{token}.ics"


def token_from_path_segment(segment: str) -> Optional[str]:
    """'<token>.ics' -> token (the route's path parameter carries the suffix-less token)."""
    seg = str(segment or "")
    return seg[:-4] if seg.endswith(".ics") else seg or None


__all__ = ["FeedKeyError", "digest", "feed_path", "issue", "signature_valid", "token_from_path_segment"]
