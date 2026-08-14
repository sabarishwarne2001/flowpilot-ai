"""API Key secret format and HMAC-SHA256 hashing (ARCH-08 §B.4, §9.2).

Token Format:
fp_live_<key_id_b32>_<secret_b64url>
   │        │             └── 32 random bytes, urlsafe-b64, unpadded (43 chars)
   │        └── the key's UUID, base32 lowercase, unpadded (26 chars)
   └── environment tag: live | test
"""

from __future__ import annotations

import base64
import hmac
import os
import secrets
import uuid
from typing import NamedTuple, Optional

from app.core.config import settings

ENV_PREFIX_LIVE = "fp_live_"
ENV_PREFIX_TEST = "fp_test_"


class ParsedApiKeyToken(NamedTuple):
    key_id: uuid.UUID
    secret: str
    prefix: str


def current_env_prefix() -> str:
    return ENV_PREFIX_LIVE if settings.ENVIRONMENT == "production" else ENV_PREFIX_TEST


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


def uuid_to_base32(key_id: uuid.UUID) -> str:
    raw_bytes = key_id.bytes
    b32 = base64.b32encode(raw_bytes).decode("ascii").lower().rstrip("=")
    return b32


def base32_to_uuid(b32: str) -> Optional[uuid.UUID]:
    try:
        padded = b32.upper() + "=" * (-len(b32) % 8)
        raw_bytes = base64.b32decode(padded.encode("ascii"))
        return uuid.UUID(bytes=raw_bytes)
    except Exception:
        return None


def mint_api_key_token(key_id: uuid.UUID, secret: str) -> str:
    prefix = current_env_prefix()
    b32_id = uuid_to_base32(key_id)
    return f"{prefix}{b32_id}_{secret}"


def parse_api_key_token(token: str) -> Optional[ParsedApiKeyToken]:
    if not token:
        return None

    prefix = current_env_prefix()
    if not token.startswith(prefix):
        return None

    raw_payload = token[len(prefix) :]
    parts = raw_payload.split("_", 1)
    if len(parts) != 2:
        return None

    b32_id, secret = parts
    key_id = base32_to_uuid(b32_id)
    if key_id is None or not secret:
        return None

    return ParsedApiKeyToken(key_id=key_id, secret=secret, prefix=prefix)


def hash_secret(secret: str) -> str:
    """Peppered HMAC-SHA256 hash using API_KEY_PEPPER."""
    pepper = settings.API_KEY_PEPPER.get_secret_value().encode("utf-8")
    return hmac.new(pepper, secret.encode("utf-8"), digestmod="sha256").hexdigest()


def verify_secret(candidate_secret: str, stored_hash: str) -> bool:
    candidate_hash = hash_secret(candidate_secret)
    return hmac.compare_digest(candidate_hash, stored_hash)