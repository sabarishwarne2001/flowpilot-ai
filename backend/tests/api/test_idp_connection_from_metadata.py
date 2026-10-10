"""F-211 — a new SSO connection with only a metadata URL was a 500.

    pytest tests/api/test_idp_connection_from_metadata.py -q

The connection builder offers "Metadata URL — easiest path, the server fetches
and parses it" and lets the owner create a SAML connection with nothing else.
The server stored the URL and never read it, so the row had no entity ID and no
SSO URL, broke the `ck_idp_saml_fields` check and came back as a 500. An OIDC
connection without issuer or client ID did the same against `ck_idp_oidc_fields`.

Now the server reads the metadata (through the SSRF-safe identity client) and
fills the entity ID, SSO and logout URLs and the signing certificates; a
connection that still lacks what its protocol needs is a 422 that says what.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.identity import EnterpriseIdpConfig, IdpSigningCertificate, VerifiedDomain
from app.services.identity import saml_gateway, scim_service
from tests.conftest import Fixture
from tests.security.plans import put_on_plan

API = "/api/v1"
METADATA_URL = "https://idp.acme.test/app/metadata"


def _certificate_body() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp.acme.test")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()


def _metadata(cert_body: str) -> bytes:
    return f"""<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
    xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID="https://idp.acme.test/entity">
  <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:KeyDescriptor use="encryption">
      <ds:KeyInfo><ds:X509Data><ds:X509Certificate>{cert_body}</ds:X509Certificate></ds:X509Data></ds:KeyInfo>
    </md:KeyDescriptor>
    <md:KeyDescriptor use="signing">
      <ds:KeyInfo><ds:X509Data><ds:X509Certificate>
        {cert_body[:40]}
        {cert_body[40:]}
      </ds:X509Certificate></ds:X509Data></ds:KeyInfo>
    </md:KeyDescriptor>
    <md:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location="https://idp.acme.test/slo"/>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
        Location="https://idp.acme.test/sso/post"/>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location="https://idp.acme.test/sso/redirect"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>""".encode()


@pytest.fixture()
def bound_domain(db_session: Session, tenant: Fixture) -> VerifiedDomain:
    put_on_plan(db_session, tenant.organization, "enterprise")
    now = scim_service.utcnow()
    domain = VerifiedDomain(
        organization_id=tenant.organization.id,
        domain=f"{tenant.organization.slug}.test",
        status="VERIFIED",
        challenge_token="token",
        challenge_issued_at=now,
        challenge_expires_at=now + timedelta(days=30),
        first_verified_at=now,
        is_sso_binding=True,
    )
    db_session.add(domain)
    db_session.commit()
    return domain


def _create(client: TestClient, tenant: Fixture, body: dict):
    return client.post(
        f"{API}/organizations/{tenant.organization.id}/identity/idp-configs",
        json=body,
        headers=tenant.owner.headers,
    )


def test_a_saml_connection_from_its_metadata_url_is_filled_from_the_metadata(
    client: TestClient, db_session: Session, tenant: Fixture, bound_domain, monkeypatch
) -> None:
    cert_body = _certificate_body()
    fetched: list[str] = []

    def fake_get(url, *, timeout, max_bytes=1_048_576, accept="application/json"):
        fetched.append(url)
        return _metadata(cert_body)

    monkeypatch.setattr(saml_gateway, "safe_get", fake_get)
    response = _create(client, tenant, {
        "verified_domain_id": str(bound_domain.id),
        "protocol": "SAML2",
        "display_name": "Okta",
        "jit_provisioning_mode": "INVITE_ONLY",
        "metadata_url": METADATA_URL,
        "idp_entity_id": None,
        "idp_sso_url": None,
    })
    assert response.status_code == 201, response.text
    assert fetched == [METADATA_URL]

    db_session.expire_all()
    config = db_session.get(EnterpriseIdpConfig, response.json()["id"])
    assert config.idp_entity_id == "https://idp.acme.test/entity"
    assert config.idp_sso_url == "https://idp.acme.test/sso/redirect"
    assert config.idp_slo_url == "https://idp.acme.test/slo"
    assert config.metadata_url == METADATA_URL
    certs = db_session.query(IdpSigningCertificate).filter_by(idp_config_id=config.id).all()
    # The same certificate is listed for signing and for encryption: one row, the primary.
    assert len(certs) == 1
    assert certs[0].side == "IDP" and certs[0].is_primary
    assert "BEGIN CERTIFICATE" in certs[0].certificate_pem


def test_fields_entered_by_hand_win_over_the_metadata(
    client: TestClient, db_session: Session, tenant: Fixture, bound_domain, monkeypatch
) -> None:
    monkeypatch.setattr(
        saml_gateway, "safe_get", lambda url, **kw: _metadata(_certificate_body())
    )
    response = _create(client, tenant, {
        "verified_domain_id": str(bound_domain.id),
        "protocol": "SAML2",
        "metadata_url": METADATA_URL,
        "idp_entity_id": "https://override.test/entity",
        "idp_sso_url": "https://override.test/sso",
    })
    assert response.status_code == 201, response.text
    db_session.expire_all()
    config = db_session.get(EnterpriseIdpConfig, response.json()["id"])
    assert config.idp_entity_id == "https://override.test/entity"
    assert config.idp_sso_url == "https://override.test/sso"


@pytest.mark.parametrize(
    "body",
    [b"<html>not metadata</html>", b"not xml at all", b"<md:EntityDescriptor xmlns:md='urn:oasis:names:tc:SAML:2.0:metadata'/>"],
)
def test_metadata_that_is_not_identity_provider_metadata_is_refused(
    client: TestClient, tenant: Fixture, bound_domain, monkeypatch, body: bytes
) -> None:
    monkeypatch.setattr(saml_gateway, "safe_get", lambda url, **kw: body)
    response = _create(client, tenant, {
        "verified_domain_id": str(bound_domain.id),
        "protocol": "SAML2",
        "metadata_url": METADATA_URL,
    })
    assert response.status_code == 422, response.text
    assert "metadata" in response.json()["detail"].lower()


def test_metadata_that_cannot_be_fetched_is_refused(
    client: TestClient, tenant: Fixture, bound_domain, monkeypatch
) -> None:
    def unreachable(url, **kw):
        raise RuntimeError(f"{url} returned HTTP 404")

    monkeypatch.setattr(saml_gateway, "safe_get", unreachable)
    response = _create(client, tenant, {
        "verified_domain_id": str(bound_domain.id),
        "protocol": "SAML2",
        "metadata_url": METADATA_URL,
    })
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "body",
    [
        {"protocol": "SAML2"},
        {"protocol": "SAML2", "idp_entity_id": "https://idp.test/e"},
        {"protocol": "OIDC"},
        {"protocol": "OIDC", "oidc_issuer": "https://idp.test"},
    ],
)
def test_a_connection_missing_what_its_protocol_needs_is_refused(
    client: TestClient, tenant: Fixture, bound_domain, body: dict
) -> None:
    response = _create(client, tenant, {"verified_domain_id": str(bound_domain.id), **body})
    assert response.status_code == 422, response.text
