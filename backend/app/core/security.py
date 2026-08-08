"""
Cryptographic primitives for FlowPilot AI.

Password hashing (bcrypt) and access-token issuance and verification.

Refresh tokens are deliberately absent from this module. They are opaque
256-bit secrets stored hashed in the sessions table, not JWTs — see
app/services/session_service.py. Nothing about a refresh token is signed, so
nothing about it belongs here.

ACCESS TOKEN CLAIMS (ARCH-03 §B.6)
----------------------------------
    sub   user id
    jti   unique token id, so one issuance is distinguishable from another in
          logs and in any future denylist
    iat   issued-at, load-bearing: an access token is rejected when its iat
          predates users.sessions_revoked_at, which is what makes password
          reset and sign-out-everywhere take effect immediately instead of at
          the end of the access TTL
    exp   expiry
    type  always "access"
    sid   the session this token was minted from, absent only for tokens
          issued before Step 7 wires login to session creation

WHY THE type CLAIM, HONESTLY
----------------------------
The plan justified `type` as preventing a refresh token from being replayed as
an access token. That specific risk does not exist here: refresh tokens are
opaque and never pass through jwt.decode, so there is nothing to confuse.

The claim is still worth having, for a different and more durable reason. This
key will eventually sign more than one kind of artifact — a websocket ticket, a
signed download URL, an SSO state parameter — and each of those is a JWT that
would otherwise satisfy get_current_user on nothing more than a valid
signature. The type check is what stops a future token from authenticating as
this one. Enforcing it now, while there is exactly one type, costs nothing;
retrofitting it after a second type exists means a migration window in which
both shapes must be accepted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Union

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

#: The only token type this module issues or accepts.
ACCESS_TOKEN_TYPE = "access"


# ===========================================================================
# Passwords
# ===========================================================================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies a plaintext password against its stored bcrypt hash.
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Computes a salted bcrypt hash for storage in users.hashed_password.
    """
    return pwd_context.hash(password)


# ===========================================================================
# Access tokens
# ===========================================================================

@dataclass(frozen=True)
class AccessTokenClaims:
    """
    A decoded, validated access token.

    Constructed only by from_payload, which is reached only after
    decode_access_token has verified the signature, the expiry, and the type.
    An instance therefore means the token was genuine at the moment it was
    parsed — it does not mean the account is still active or that the session
    still exists, both of which are checked in app/api/deps.py.
    """

    subject: uuid.UUID
    jti: uuid.UUID
    issued_at: datetime
    expires_at: datetime
    session_id: uuid.UUID | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AccessTokenClaims | None":
        """
        Parses a verified payload into typed claims.

        Returns None rather than raising on a malformed claim. A token that
        survived signature verification but carries a non-UUID `sub` is either
        a bug in issuance or a token from a different system that happens to
        share the key; in both cases the correct response at the auth boundary
        is a 401, and returning None keeps every failure on one path.
        """
        try:
            subject = uuid.UUID(str(payload["sub"]))
            jti = uuid.UUID(str(payload["jti"]))
            issued_at = datetime.fromtimestamp(int(payload["iat"]), tz=UTC)
            expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
        except (KeyError, ValueError, TypeError, OverflowError):
            return None

        raw_sid = payload.get("sid")
        session_id: uuid.UUID | None = None
        if raw_sid is not None:
            try:
                session_id = uuid.UUID(str(raw_sid))
            except (ValueError, TypeError):
                return None

        return cls(
            subject=subject,
            jti=jti,
            issued_at=issued_at,
            expires_at=expires_at,
            session_id=session_id,
        )


def create_access_token(
    subject: Union[str, Any],
    *,
    session_id: uuid.UUID | None = None,
    expires_delta: Union[timedelta, None] = None,
) -> str:
    """
    Issues a signed access token.

    session_id is keyword-only and optional. Optional because login does not
    create a session until Step 7, and a required parameter here would break
    the running login endpoint the moment this module is deployed. Keyword-only
    because the previous signature took a single positional argument, and a
    second positional would let an existing call site pass an expires_delta
    into the session slot with no error at all.

    From Step 7 onward every token issued by login carries a sid. A token
    without one is a legacy shape, and app/api/deps.py decides what to do about
    that; this function's job is to record what was true at issuance, not to
    enforce policy.

    Args:
        subject: The user id. Stringified into `sub`.
        session_id: The refresh session this token was minted from.
        expires_delta: Overrides ACCESS_TOKEN_EXPIRE_MINUTES.

    Returns:
        The encoded JWT.
    """
    issued_at = datetime.now(UTC)
    expire = issued_at + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    to_encode: dict[str, Any] = {
        "sub": str(subject),
        "jti": str(uuid.uuid4()),
        # Truncated to whole seconds explicitly. JWT numeric dates are integer
        # seconds, and letting the encoder round means iat can land a fraction
        # of a second later than the sessions_revoked_at written in the same
        # request — which would let a token issued during a revocation survive
        # it.
        "iat": int(issued_at.timestamp()),
        "exp": int(expire.timestamp()),
        "type": ACCESS_TOKEN_TYPE,
    }

    if session_id is not None:
        to_encode["sid"] = str(session_id)

    return jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> Union[dict[str, Any], None]:
    """
    Verifies an access token and returns its payload.

    Returns None for every failure — bad signature, expired, missing required
    claim, wrong type. The caller cannot distinguish them, which is
    intentional: the auth boundary answers 401 to all of them, and a caller
    that could tell "expired" from "forged" would eventually branch on it.

    The algorithm is pinned to the configured one. Passing a list containing
    only settings.JWT_ALGORITHM is what prevents the `alg: none` and
    HS256/RS256 confusion attacks — jose will not honour an `alg` header whose
    value is not in this list.

    Required claims are enforced by the decoder rather than checked afterwards,
    so a token missing `iat` fails verification instead of arriving at
    from_payload as a None-shaped hole.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.JWT_SECRET_KEY.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],
            options={
                "require_sub": True,
                "require_exp": True,
                "require_iat": True,
            },
        )
    except (jwt.JWTError, ValueError):
        return None

    # Checked after decoding rather than as a required claim, because "type is
    # absent" and "type is wrong" must both fail and jose can only enforce
    # presence. Tokens issued before this module was deployed have no type and
    # are rejected here — those users log in again once (R6).
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        return None

    return payload


def decode_access_token_claims(token: str) -> AccessTokenClaims | None:
    """
    Verifies an access token and returns it as typed claims.

    The form used from Step 7 onward, where deps needs `iat` for the
    sessions_revoked_at comparison and `sid` for the session lookup. Reading
    those out of a raw dict at the auth boundary would mean parsing timestamps
    and UUIDs inline in the one function that must never raise unexpectedly.
    """
    payload = decode_access_token(token)
    if payload is None:
        return None
    return AccessTokenClaims.from_payload(payload)