"""ARCH-16 Step 16.4 — OIDC gateway."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from app.services.identity._integration import get_settings, safe_get, utcnow
from app.services.identity.errors import AssertionRejected, IdpConfigError

logger = logging.getLogger(__name__)

_JWKS_REFETCH_FLOOR_S = 60
_last_jwks_refetch: dict[str, float] = {}


@dataclass
class OidcClaims:
    subject: str
    email: str
    email_verified: bool
    auth_time: datetime
    issuer: str
    nonce: str | None
    raw_claims: dict = field(default_factory=dict)
    payload_digest: str = ""

    def attribute(self, *names: str) -> list[str]:
        for n in names:
            value = self.raw_claims.get(n)
            if value is None:
                continue
            if isinstance(value, list):
                return [str(v) for v in value]
            return [str(value)]
        return []


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def make_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    return verifier, challenge


def fetch_discovery(discovery_url: str) -> dict:
    settings = get_settings()
    timeout = float(getattr(settings, "OIDC_DISCOVERY_TIMEOUT_S", 10))
    try:
        body = safe_get(discovery_url, timeout=timeout, max_bytes=262_144)
        doc = json.loads(body)
    except Exception as exc:
        raise IdpConfigError(f"could not fetch OIDC discovery document: {exc}") from exc

    for required in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        if required not in doc:
            raise IdpConfigError(
                f"discovery document is missing {required!r}")
    return doc


def fetch_jwks(jwks_uri: str) -> dict:
    settings = get_settings()
    timeout = float(getattr(settings, "OIDC_DISCOVERY_TIMEOUT_S", 10))
    try:
        return json.loads(safe_get(jwks_uri, timeout=timeout, max_bytes=262_144))
    except Exception as exc:
        raise IdpConfigError(f"could not fetch JWKS: {exc}") from exc


def resolve_signing_key(*, kid: str, cached_jwks: dict | None, jwks_uri: str,
                        config_id: str) -> dict:
    def _find(jwks: dict | None):
        if not jwks:
            return None
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return key
        return None

    key = _find(cached_jwks)
    if key is not None:
        return key

    now = time.monotonic()
    last = _last_jwks_refetch.get(config_id, 0.0)
    if now - last < _JWKS_REFETCH_FLOOR_S:
        raise AssertionRejected(
            "REJECTED_SIGNATURE",
            f"unknown kid {kid!r} and a JWKS refetch was attempted within the throttle window")
    _last_jwks_refetch[config_id] = now

    key = _find(fetch_jwks(jwks_uri))
    if key is None:
        raise AssertionRejected("REJECTED_SIGNATURE",
                                f"no JWKS key matches kid {kid!r}")
    return key


def build_authorization_url(*, authorization_endpoint: str, client_id: str,
                            redirect_uri: str, state: str, nonce: str,
                            code_challenge: str, scopes: str,
                            force_authn: bool = False,
                            max_age_s: int | None = None) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes.replace(",", " "),
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    params["max_age"] = "0" if force_authn else str(max_age_s if max_age_s is not None
                                                    else 43200)
    if force_authn:
        params["prompt"] = "login"
    joiner = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{joiner}{urlencode(params)}"


def exchange_code(*, token_endpoint: str, client_id: str, client_secret: str,
                  code: str, redirect_uri: str, code_verifier: str) -> dict:
    settings = get_settings()
    timeout = float(getattr(settings, "OIDC_DISCOVERY_TIMEOUT_S", 10))
    import httpx

    response = httpx.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": code_verifier,
        },
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise AssertionRejected(
            "REJECTED_UNKNOWN",
            f"token endpoint returned {response.status_code}: {response.text[:200]}")
    return response.json()


def validate_id_token(*, id_token: str, jwks_key: dict, issuer: str,
                      audience: str, expected_nonce: str,
                      clock_skew_s: int = 120) -> OidcClaims:
    from jose import jwt
    from jose.exceptions import JWTError

    digest = "sha256:" + hashlib.sha256(id_token.encode("ascii")).hexdigest()

    try:
        claims = jwt.decode(
            id_token,
            jwks_key,
            algorithms=[jwks_key.get("alg", "RS256")],
            audience=audience,
            issuer=issuer,
            options={
                "verify_signature": True,
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
                "leeway": clock_skew_s,
            },
        )
    except JWTError as exc:
        raise AssertionRejected("REJECTED_SIGNATURE",
                                f"ID token validation failed: {exc}") from exc

    nonce = claims.get("nonce")
    if not nonce or not secrets.compare_digest(str(nonce), expected_nonce):
        raise AssertionRejected(
            "REJECTED_REPLAY",
            "ID token nonce does not match the authorization request")

    raw_auth_time = claims.get("auth_time")
    if raw_auth_time is None:
        raise AssertionRejected(
            "REJECTED_NO_AUTHN_INSTANT",
            "ID token carries no auth_time despite max_age being requested.")
    try:
        auth_time = datetime.fromtimestamp(int(raw_auth_time), tz=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise AssertionRejected("REJECTED_NO_AUTHN_INSTANT",
                                f"auth_time is not an epoch integer: {raw_auth_time!r}") from exc

    if auth_time > utcnow() + timedelta(seconds=clock_skew_s):
        raise AssertionRejected("REJECTED_EXPIRED", "auth_time is in the future")

    subject = claims.get("sub")
    if not subject:
        raise AssertionRejected("REJECTED_UNKNOWN", "ID token carries no sub")

    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if "@" not in email:
        raise AssertionRejected(
            "REJECTED_UNKNOWN",
            "no email claim; cannot bind this identity to a verified domain")

    return OidcClaims(
        subject=str(subject),
        email=email,
        email_verified=bool(claims.get("email_verified", False)),
        auth_time=auth_time,
        issuer=str(claims.get("iss", issuer)),
        nonce=str(nonce),
        raw_claims=dict(claims),
        payload_digest=digest,
    )