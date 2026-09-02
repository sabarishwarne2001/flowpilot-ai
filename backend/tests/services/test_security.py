"""
ARCH-03 Step 6 — access token claims and verification.

No database. These are pure functions, and the properties under test are the
ones that decide whether a forged or repurposed token authenticates.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

from jose import jwt

from app.core import security
from app.core.config import settings

KEY = settings.JWT_SECRET_KEY.get_secret_value()
ALG = settings.JWT_ALGORITHM


def _b64(data: dict) -> str:
    """Base64url-encodes a JWT segment without padding."""
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _raw(token: str) -> dict:
    """Decodes without verification, to inspect what was actually emitted."""
    return jwt.get_unverified_claims(token)


# ===========================================================================
# Claims
# ===========================================================================

def test_token_carries_every_required_claim():
    subject = uuid.uuid4()
    claims = _raw(security.create_access_token(subject))

    assert claims["sub"] == str(subject)
    assert claims["type"] == "access"
    assert uuid.UUID(claims["jti"])
    assert isinstance(claims["iat"], int)
    assert isinstance(claims["exp"], int)


def test_jti_is_unique_per_issuance():
    subject = uuid.uuid4()
    a = _raw(security.create_access_token(subject))["jti"]
    b = _raw(security.create_access_token(subject))["jti"]
    assert a != b


def test_iat_is_whole_seconds():
    # Fractional seconds would let a token issued during a revocation land a
    # hair after the sessions_revoked_at written in the same request.
    claims = _raw(security.create_access_token(uuid.uuid4()))
    assert claims["iat"] == int(claims["iat"])


def test_sid_is_omitted_when_absent_and_present_when_given():
    without = _raw(security.create_access_token(uuid.uuid4()))
    assert "sid" not in without

    session_id = uuid.uuid4()
    with_sid = _raw(
        security.create_access_token(uuid.uuid4(), session_id=session_id)
    )
    assert with_sid["sid"] == str(session_id)


# ===========================================================================
# Verification
# ===========================================================================

def test_round_trip():
    subject = uuid.uuid4()
    session_id = uuid.uuid4()
    token = security.create_access_token(subject, session_id=session_id)

    claims = security.decode_access_token_claims(token)

    assert claims is not None
    assert claims.subject == subject
    assert claims.session_id == session_id
    assert claims.issued_at <= datetime.now(UTC)
    assert claims.expires_at > datetime.now(UTC)


def test_expired_token_is_rejected():
    token = security.create_access_token(
        uuid.uuid4(), expires_delta=timedelta(seconds=-1)
    )
    assert security.decode_access_token(token) is None


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "jti": str(uuid.uuid4()),
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            "type": "access",
        },
        "a-different-secret-that-is-long-enough-to-sign",
        algorithm=ALG,
    )
    assert security.decode_access_token(forged) is None


def test_token_without_a_type_claim_is_rejected():
    """
    The pre-ARCH-03 token shape. Rejecting it is what signs everyone out once
    at deploy (R6).
    """
    legacy = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "jti": str(uuid.uuid4()),
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        KEY,
        algorithm=ALG,
    )
    assert security.decode_access_token(legacy) is None


def test_token_of_another_type_is_rejected():
    """
    A validly signed JWT of some other kind must not authenticate.

    Nothing issues a non-access JWT today. The check exists so that the first
    thing which does — a websocket ticket, a signed download URL, an SSO state
    parameter — cannot be replayed against get_current_user.
    """
    other = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "jti": str(uuid.uuid4()),
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            "type": "download",
        },
        KEY,
        algorithm=ALG,
    )
    assert security.decode_access_token(other) is None


def test_token_missing_iat_is_rejected():
    # iat is what sessions_revoked_at is compared against. A token without one
    # cannot be revoked and must not be accepted.
    no_iat = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "jti": str(uuid.uuid4()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            "type": "access",
        },
        KEY,
        algorithm=ALG,
    )
    assert security.decode_access_token(no_iat) is None


def test_unsigned_token_is_rejected():
    """
    The alg=none attack, assembled by hand.

    jose refuses to *encode* with alg=none, so this has to be built from raw
    base64 — which is exactly how an attacker would produce it, and the only
    way to prove our decoder rejects it rather than relying on the encoder's
    refusal.
    """
    header = _b64({"alg": "none", "typ": "JWT"})
    payload = _b64(
        {
            "sub": str(uuid.uuid4()),
            "jti": str(uuid.uuid4()),
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            "type": "access",
        }
    )
    assert security.decode_access_token(f"{header}.{payload}.") is None


def test_malformed_subject_is_rejected_by_the_parser():
    token = security.create_access_token("not-a-uuid")
    # The signature is genuine, so decode succeeds and the claim parser is
    # what catches it. Both paths must end in None, not an exception.
    assert security.decode_access_token(token) is not None
    assert security.decode_access_token_claims(token) is None


def test_garbage_is_rejected_without_raising():
    for value in ("", "not.a.token", "a.b.c", "x" * 500):
        assert security.decode_access_token(value) is None
        assert security.decode_access_token_claims(value) is None


# ===========================================================================
# Passwords
# ===========================================================================

def test_password_hashing_round_trip():
    hashed = security.get_password_hash("correct horse battery staple")
    assert security.verify_password("correct horse battery staple", hashed)
    assert not security.verify_password("wrong", hashed)


def test_password_hashes_are_salted():
    a = security.get_password_hash("same")
    b = security.get_password_hash("same")
    assert a != b
