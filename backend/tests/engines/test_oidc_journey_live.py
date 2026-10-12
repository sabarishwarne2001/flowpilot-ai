"""Single sign-on through OpenID Connect, end to end over HTTP (campaign session 3).

`GET /oidc/callback` had never been driven. The provider here is a real RSA key whose public JWK
is the connection's cached key set; only the provider's token endpoint is replaced in-process (the
SSRF-safe client rightly refuses a localhost endpoint), and it hands back an ID token signed with
that key. The callback then does everything for real: the one-time state, the PKCE verifier, the
signature, issuer, audience and nonce, the verified-domain rule, and the landing redirect with the
refresh cookie.
"""

from __future__ import annotations

import json
import time
import uuid
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.cookies import REFRESH_COOKIE_NAME
from app.models.identity import (
    DomainStatus,
    EnterpriseIdpConfig,
    IdpProtocol,
    JitProvisioningMode,
    VerifiedDomain,
)
from app.services.identity import oidc_gateway
from app.services.identity._integration import encrypt_secret
from tests.engines.conftest import Engines

API = "/api/v1"


class Provider:
    def __init__(self, engines: Engines) -> None:
        from datetime import datetime, timedelta, timezone

        self.domain = f"oidc-{uuid.uuid4().hex[:8]}.example.com"
        self.issuer = f"https://login.{self.domain}"
        self.client_id = f"client-{uuid.uuid4().hex[:8]}"
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": "test-key-1", "alg": "RS256", "use": "sig"})
        now = datetime.now(timezone.utc)
        domain = VerifiedDomain(
            organization_id=engines.org, domain=self.domain, status=DomainStatus.VERIFIED,
            challenge_token=f"c-{uuid.uuid4().hex[:8]}", challenge_expires_at=now + timedelta(days=1),
            first_verified_at=now, last_seen_at=now, is_sso_binding=True,
        )
        engines.db.add(domain)
        engines.db.flush()
        self.config = EnterpriseIdpConfig(
            organization_id=engines.org, verified_domain_id=domain.id, protocol=IdpProtocol.OIDC,
            display_name="Test OIDC", is_active=True, jit_seat_cap=10,
            jit_provisioning_mode=JitProvisioningMode.OPEN,
            oidc_issuer=self.issuer, oidc_client_id=self.client_id,
            oidc_client_secret_encrypted=encrypt_secret("client-secret"),
            oidc_jwks_json={"keys": [jwk]},
            oidc_authorization_endpoint=f"{self.issuer}/authorize",
            oidc_token_endpoint=f"{self.issuer}/token",
            oidc_jwks_uri=f"{self.issuer}/jwks",
        )
        engines.db.add(self.config)
        engines.db.commit()
        self.exchanges: list[dict] = []

    def id_token(self, *, email: str, nonce: str, audience: str | None = None, issuer: str | None = None) -> str:
        now = int(time.time())
        claims = {
            "iss": issuer or self.issuer, "aud": audience or self.client_id, "sub": f"sub-{email}",
            "email": email, "email_verified": True, "nonce": nonce,
            "iat": now, "exp": now + 300, "auth_time": now,
        }
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-key-1"})


@pytest.fixture
def provider(engines: Engines, monkeypatch) -> Provider:
    engines.plan("enterprise")
    p = Provider(engines)
    p.next_token = None

    def exchange_code(**kwargs):
        p.exchanges.append(kwargs)
        return {"id_token": p.next_token, "token_type": "Bearer"}

    monkeypatch.setattr(oidc_gateway, "exchange_code", exchange_code)
    return p


def _start(engines: Engines, provider: Provider) -> tuple[str, str]:
    response = engines.client.get(
        f"{API}/sso/start", params={"domain": provider.domain, "redirect_path": "/workspaces"},
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["client_id"] == [provider.client_id]
    assert query["code_challenge_method"] == ["S256"]
    return query["state"][0], query["nonce"][0]


def _callback(engines: Engines, state: str):
    engines.client.cookies.clear()
    return engines.client.get(f"{API}/oidc/callback", params={"code": "auth-code", "state": state},
                              follow_redirects=False)


def test_a_person_signs_in_through_openid_connect(engines: Engines, provider: Provider) -> None:
    state, nonce = _start(engines, provider)
    email = f"olivia-{uuid.uuid4().hex[:6]}@{provider.domain}"
    provider.next_token = provider.id_token(email=email, nonce=nonce)
    landed = _callback(engines, state)
    assert landed.status_code in (302, 303), landed.text
    assert urlparse(landed.headers["location"]).path == "/auth/sso/complete"
    assert landed.cookies.get(REFRESH_COOKIE_NAME)
    # The PKCE verifier stored at the start was sent with the code.
    assert provider.exchanges[-1]["code_verifier"]
    assert provider.exchanges[-1]["code"] == "auth-code"

    engines.client.cookies.set(REFRESH_COOKIE_NAME, landed.cookies[REFRESH_COOKIE_NAME], path=f"{API}/auth")
    token = engines.client.post(f"{API}/auth/refresh").json()["access_token"]
    me = engines.client.get(f"{API}/me/profile", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["email"] == email


def test_the_state_is_single_use(engines: Engines, provider: Provider) -> None:
    state, nonce = _start(engines, provider)
    provider.next_token = provider.id_token(email=f"once-{uuid.uuid4().hex[:6]}@{provider.domain}", nonce=nonce)
    assert _callback(engines, state).status_code in (302, 303)
    assert _callback(engines, state).status_code == 401
    assert _callback(engines, "a-state-nobody-issued").status_code == 401


@pytest.mark.parametrize("tamper", ["nonce", "audience", "issuer", "off-domain email"])
def test_an_id_token_that_does_not_answer_this_request_is_refused(
    engines: Engines, provider: Provider, tamper: str
) -> None:
    state, nonce = _start(engines, provider)
    email = f"tamper-{uuid.uuid4().hex[:6]}@{provider.domain}"
    kwargs = {"email": email, "nonce": nonce}
    if tamper == "nonce":
        kwargs["nonce"] = "another-nonce"
    elif tamper == "audience":
        kwargs["audience"] = "someone-elses-client"
    elif tamper == "issuer":
        kwargs["issuer"] = "https://login.evil.example.com"
    else:
        kwargs["email"] = f"outsider-{uuid.uuid4().hex[:6]}@gmail.com"
    provider.next_token = provider.id_token(**kwargs)
    response = _callback(engines, state)
    assert response.status_code == 401, (tamper, response.text)
    assert REFRESH_COOKIE_NAME not in response.cookies
