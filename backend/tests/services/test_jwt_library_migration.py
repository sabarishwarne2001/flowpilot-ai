"""F-039 — the two RS256 paths that moved from python-jose to PyJWT.

The session tokens (HS256) are covered by the auth suites. These two had no
test at all, so the library swap would have been unverified there:

* OIDC sign-in: an identity provider's ID token is verified against its JWK
  (signature, audience, issuer, expiry with clock-skew leeway) and its nonce.
* Snowflake warehouse sync: a key-pair JWT is minted with the tenant's private
  key. python-jose was handed the PEM text and could not open a
  passphrase-protected key, although the connector's own error message says
  those are supported; PyJWT is given the loaded key.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services.analytics.connectors import snowflake
from app.services.identity.errors import AssertionRejected
from app.services.identity.oidc_gateway import validate_id_token

pytestmark = pytest.mark.no_db

ISSUER = "https://idp.example.com"
AUDIENCE = "flowpilot-client"
NONCE = "n-" + uuid.uuid4().hex


@pytest.fixture(scope="module")
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"alg": "RS256", "kid": "k1", "use": "sig"})
    return private, jwk


def _id_token(private, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "email": "Person@Example.com",
        "email_verified": True,
        "nonce": NONCE,
        "iat": now,
        "exp": now + 300,
        "auth_time": now - 5,
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "k1"})


def _validate(token: str, jwk: dict):
    return validate_id_token(
        id_token=token, jwks_key=jwk, issuer=ISSUER, audience=AUDIENCE, expected_nonce=NONCE
    )


def test_a_valid_id_token_is_accepted(keys) -> None:
    private, jwk = keys
    claims = _validate(_id_token(private), jwk)
    assert claims.subject == "user-123"
    assert claims.email == "person@example.com"
    assert claims.nonce == NONCE


def test_an_expired_token_inside_the_clock_skew_is_accepted(keys) -> None:
    private, jwk = keys
    now = int(time.time())
    assert _validate(_id_token(private, exp=now - 60, iat=now - 360), jwk).subject == "user-123"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-else"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 3600},
    ],
    ids=["wrong-audience", "wrong-issuer", "expired"],
)
def test_wrong_audience_issuer_or_expiry_is_refused(keys, overrides) -> None:
    private, jwk = keys
    with pytest.raises(AssertionRejected):
        _validate(_id_token(private, **overrides), jwk)


def test_a_token_signed_by_another_key_is_refused(keys) -> None:
    _, jwk = keys
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AssertionRejected):
        _validate(_id_token(stranger), jwk)


def test_an_unsigned_token_is_refused(keys) -> None:
    _, jwk = keys
    unsigned = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "nonce": NONCE, "exp": int(time.time()) + 60},
        key=None,
        algorithm="none",
    )
    with pytest.raises(AssertionRejected):
        _validate(unsigned, jwk)


def test_a_nonce_mismatch_is_refused(keys) -> None:
    private, jwk = keys
    with pytest.raises(AssertionRejected):
        _validate(_id_token(private, nonce="replayed"), jwk)


@pytest.mark.parametrize("passphrase", [None, "correct horse battery"])
def test_the_snowflake_jwt_is_signed_with_the_tenants_key(passphrase) -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encryption = (
        serialization.BestAvailableEncryption(passphrase.encode())
        if passphrase
        else serialization.NoEncryption()
    )
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    ).decode()

    token = snowflake._mint_jwt(account="xy12345.eu-west-1", user="loader", private_key_pem=pem, passphrase=passphrase)

    claims = jwt.decode(token, private.public_key(), algorithms=["RS256"])
    assert claims["sub"] == "XY12345.LOADER"
    assert claims["iss"].startswith("XY12345.LOADER.SHA256:")
    assert claims["exp"] - claims["iat"] == snowflake.JWT_LIFETIME_SECONDS
