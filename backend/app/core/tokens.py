from __future__ import annotations

import hashlib
import secrets

DEFAULT_TOKEN_BYTES = 32

#: Length of a hex-encoded SHA-256 digest. Every token_hash column in the
#: schema is String(64) to match.
TOKEN_HASH_LENGTH = 64


def generate_secure_token(token_bytes: int = DEFAULT_TOKEN_BYTES) -> str:
    """
    Generates a cryptographically secure, URL-safe random token.

    This is the single, project-wide entry point for secure token
    generation. Services should call this instead of using `secrets`
    directly, so token generation stays consistent and swappable in
    one place.

    Generic by design: knows nothing about invitations, users,
    workspaces, emails, or APIs — it simply returns a secure string.

    Args:
        token_bytes: Number of random bytes to use as input. The
            resulting string is longer than this value once base64
            URL-safe encoded. Defaults to DEFAULT_TOKEN_BYTES.

    Returns:
        A URL-safe secure random token string.
    """
    return secrets.token_urlsafe(token_bytes)


def hash_token(token: str) -> str:
    """
    Computes the stored form of a secure token.

    Every token in FlowPilot is persisted as the SHA-256 of its UTF-8 bytes,
    hex encoded, and never in plaintext: invitations, email verification,
    password reset, and refresh sessions all use this one function. A read of
    any token table therefore yields nothing usable.

    SHA-256 rather than bcrypt or argon2, deliberately. Those exist to make
    guessing expensive, and guessing is only a threat against a value drawn
    from a small space. The input here is 256 bits from
    secrets.token_urlsafe — it is not guessable at any work factor, so a slow
    KDF would buy no security while adding latency to every verification
    click and every token refresh.

    The equivalent SQL expression is encode(sha256(token::bytea), 'hex'). The
    two agree for the ASCII output of generate_secure_token, and the ARCH-03
    MIGRATE revision asserts that agreement against live data before writing
    anything.

    Args:
        token: The plaintext token, as delivered to the user.

    Returns:
        A 64-character lowercase hex digest.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
