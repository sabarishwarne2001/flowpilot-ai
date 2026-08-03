from __future__ import annotations

import secrets

DEFAULT_TOKEN_BYTES = 32


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