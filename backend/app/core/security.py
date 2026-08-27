"""
Cryptographic primitives for FlowPilot AI.

Password hashing (Argon2id, with bcrypt still accepted) and access-token
issuance and verification.

Refresh tokens are deliberately absent from this module. They are opaque
256-bit secrets stored hashed in the sessions table, not JWTs — see
app/services/session_service.py. Nothing about a refresh token is signed, so
nothing about it belongs here.

ACCESS TOKEN CLAIMS (ARCH-03 §B.6, extended by SEC-1)
-----------------------------------------------------
    sub        user id
    jti        unique token id, so one issuance is distinguishable from
               another in logs and in any future denylist
    iat        issued-at, load-bearing: an access token is rejected when its
               iat predates users.sessions_revoked_at, which is what makes
               password reset and sign-out-everywhere take effect immediately
               instead of at the end of the access TTL
    auth_time  when the user last actually presented a credential (SEC-1)
    exp        expiry
    type       always "access"
    sid        the session this token was minted from, absent only for tokens
               issued before Step 7 wires login to session creation

WHY `auth_time` IS NOT `iat`
----------------------------
They look interchangeable and are not, and the difference was a live hole.

`iat` is when *this token* was minted. Rotation mints a new access token every
few minutes from a refresh token that may be months old, so `iat` on an ancient
session is always fresh. ARCH-15's F6 gate — "you must have authenticated
within BILLING_REAUTH_WINDOW_S to mint a Customer Portal URL" — read `iat`, and
therefore admitted any session that had simply stayed alive. The window was not
short, it was unreachable, and no test caught it because the mechanism was
correct and nothing in production ever presents a stale token.

`auth_time` is when a human last proved they hold the credential. It is stamped
at login, copied forward unchanged through every rotation in the family, and
moved only by genuine re-authentication. It is the one fact about a session
that rotation cannot launder.

PASSWORD HASHING (SEC-1 Tranche 2)
----------------------------------
`argon2` first, `bcrypt` retained, `deprecated="auto"`. Listing bcrypt second
is not politeness toward old code: it is the only way an existing user can log
in at all, and because the sole moment a password can be rehashed is during a
successful login, **the bcrypt entry has no removal date**. A dormant account
keeps its bcrypt hash until its owner returns, which may be never. Anybody
planning a "drop bcrypt" milestone should read that sentence twice.

Parameters come from Settings so they are tunable per environment, and default
to the OWASP / RFC 9106 floor for memory-constrained backends: 19 MiB, t=2,
p=1. They are a latency decision as much as a security one — `memory_cost`
multiplies by concurrency, so 19 MiB at 20 concurrent logins is ~380 MB and
fits a 1-2 GB container, while the same setting raised "for safety" to 64 MiB
is 1.3 GB and an OOM kill under a credential-stuffing burst. A floor, not a
dial to be maximised.

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

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Union

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

logger = logging.getLogger("app.core.security")

#: Argon2id first, bcrypt retained for verification and silent upgrade.
#:
#: `argon2__type="ID"` is explicit rather than left to the handler default.
#: Argon2i and Argon2d exist, the passlib default has moved across versions,
#: and "which variant is this deployment actually using" is not a question
#: anybody should have to answer by reading a dependency's changelog.
pwd_context = CryptContext(
    schemes=["argon2", "bcrypt"],
    deprecated="auto",
    argon2__type="ID",
    argon2__memory_cost=settings.ARGON2_MEMORY_COST,
    argon2__time_cost=settings.ARGON2_TIME_COST,
    argon2__parallelism=settings.ARGON2_PARALLELISM,
)

#: The only token type this module issues or accepts.
ACCESS_TOKEN_TYPE = "access"

#: The claim carrying the authentication moment. Named for the OIDC claim of
#: the same meaning, so that if this service ever fronts an OIDC provider the
#: value maps across without a translation layer.
AUTH_TIME_CLAIM = "auth_time"


# ===========================================================================
# Passwords
# ===========================================================================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies a plaintext password against its stored hash.

    Accepts both Argon2id and bcrypt hashes; passlib selects the handler from
    the hash prefix. Callers that can persist an upgrade should prefer
    verify_and_upgrade_password.
    """
    return pwd_context.verify(plain_password, hashed_password)


def verify_and_upgrade_password(
    plain_password: str, hashed_password: str
) -> tuple[bool, str | None]:
    """
    Verifies, and reports a replacement hash when the stored one is outdated.

    Returns `(verified, new_hash_or_None)`. A non-None second element means the
    stored hash is bcrypt, or Argon2id at superseded parameters, and the caller
    should persist the replacement — see
    auth_service._persist_password_upgrade for why that write must never be
    allowed to fail the login.

    This is `CryptContext.verify_and_update` rather than
    verify-then-needs_update-then-hash because the plaintext is only in hand
    for the duration of this call, and the three-step form invites somebody to
    later move the rehash outside the block where it exists.
    """
    try:
        verified, new_hash = pwd_context.verify_and_update(
            plain_password, hashed_password
        )
    except ValueError:
        # An unrecognised or corrupt stored hash. Treated as a failed
        # verification rather than an exception, because the auth boundary
        # answers 401 to everything, and a malformed hash must not become a
        # 500 that tells the caller their account is interesting.
        logger.error("password.unparseable_stored_hash")
        return False, None

    return bool(verified), new_hash


def get_password_hash(password: str) -> str:
    """
    Computes an Argon2id hash for storage in users.hashed_password.

    New hashes are always Argon2id: `pwd_context.hash` uses the first scheme in
    the list. bcrypt is reachable only by verifying a hash that already exists.
    """
    return pwd_context.hash(password)


def hash_needs_upgrade(hashed_password: str) -> bool:
    """
    Whether a stored hash is bcrypt, or Argon2id at superseded parameters.

    Exposed for the SEC-1 gate suite and for operational reporting: "how much
    of the population is still on bcrypt" is worth being able to answer without
    a `LIKE '$2b$%'` over the users table.
    """
    try:
        return bool(pwd_context.needs_update(hashed_password))
    except ValueError:
        return False


def hash_scheme(hashed_password: str) -> str | None:
    """The scheme name of a stored hash, or None if unrecognised."""
    try:
        return pwd_context.identify(hashed_password)
    except ValueError:
        return None


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
    auth_time: datetime | None

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

        raw_auth_time = payload.get(AUTH_TIME_CLAIM)
        auth_time: datetime | None = None
        if raw_auth_time is not None:
            try:
                auth_time = datetime.fromtimestamp(int(raw_auth_time), tz=UTC)
            except (ValueError, TypeError, OverflowError, OSError):
                return None

        return cls(
            subject=subject,
            jti=jti,
            issued_at=issued_at,
            expires_at=expires_at,
            session_id=session_id,
            auth_time=auth_time,
        )


def create_access_token(
    subject: Union[str, Any],
    *,
    session_id: uuid.UUID | None = None,
    authenticated_at: datetime | None = None,
    expires_delta: Union[timedelta, None] = None,
) -> str:
    """
    Issues a signed access token.
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
        "iat": int(issued_at.timestamp()),
        "exp": int(expire.timestamp()),
        "type": ACCESS_TOKEN_TYPE,
    }

    if session_id is not None:
        to_encode["sid"] = str(session_id)

    # Default to "authenticated right now" when the caller doesn't specify one.
    # This is correct for every direct mint that isn't a rotation -- and
    # rotation always passes the original authenticated_at forward explicitly
    # (session_service._rotate_live_session), so this default never overwrites
    # a genuine earlier authentication. It only closes the gap where a token
    # could silently carry no auth_time at all, which is what made the F6
    # reauth check unexpectedly hostile to anything minted outside the normal
    # login/refresh call sites.
    moment = authenticated_at if authenticated_at is not None else issued_at
    to_encode[AUTH_TIME_CLAIM] = int(
        (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp()
    )

    return jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> Union[dict[str, Any], None]:
    """
    Verifies an access token and returns its payload.
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

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        return None

    return payload


def decode_access_token_claims(token: str) -> AccessTokenClaims | None:
    """
    Verifies an access token and returns it as typed claims.
    """
    payload = decode_access_token(token)
    if payload is None:
        return None
    return AccessTokenClaims.from_payload(payload)