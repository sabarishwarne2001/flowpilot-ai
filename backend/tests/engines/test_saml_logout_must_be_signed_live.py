"""A SAML logout request must be signed by the identity provider that issued the session (F-205).

POST /api/v1/saml/slo is public, as single logout requires. It read the SessionIndex from the
request and revoked every session carrying it without checking a signature, so anyone who knew a
session index (it travels in the SAML assertion through the user's browser) could sign that
person out of FlowPilot, as often as they liked. The request is now accepted only when its
signature verifies against a live signing certificate of the identity provider named in its
Issuer, and only that provider's sessions are revoked.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import SignatureMethod, XMLSigner, methods

from app.models.identity import (
    DomainStatus,
    EnterpriseIdpConfig,
    IdpProtocol,
    IdpSigningCertificate,
    VerifiedDomain,
)
from app.models.user_session import UserSession
from app.services import session_service
from tests.engines.conftest import Engines

SLO = "/api/v1/saml/slo"
ENTITY = "http://idp.f205.example.com/metadata"


def _keypair(common_name: str) -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return (
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode(),
        cert.public_bytes(serialization.Encoding.PEM).decode(),
    )


@pytest.fixture(scope="module")
def idp_keys() -> tuple[str, str]:
    return _keypair("idp.f205.example.com")


@pytest.fixture(scope="module")
def attacker_keys() -> tuple[str, str]:
    return _keypair("attacker.example.com")


def _logout_request(session_index: str, *, issuer: str = ENTITY) -> str:
    instant = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        '<samlp:LogoutRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="_{uuid.uuid4().hex}" Version="2.0" IssueInstant="{instant}">'
        f"<saml:Issuer>{issuer}</saml:Issuer>"
        "<saml:NameID>f205@sso-f205.example.com</saml:NameID>"
        f"<samlp:SessionIndex>{session_index}</samlp:SessionIndex>"
        "</samlp:LogoutRequest>"
    )


def _signed(xml: str, keys: tuple[str, str]) -> str:
    key, cert = keys
    root = etree.fromstring(xml.encode())
    signed = XMLSigner(method=methods.enveloped,
                       signature_algorithm=SignatureMethod.RSA_SHA256).sign(root, key=key, cert=cert)
    # The enveloped signature goes after the Issuer, as the SAML schema orders it.
    signature = signed.find("{http://www.w3.org/2000/09/xmldsig#}Signature")
    signed.remove(signature)
    signed.insert(1, signature)
    return base64.b64encode(etree.tostring(signed)).decode()


def _plain(xml: str) -> str:
    return base64.b64encode(xml.encode()).decode()


@pytest.fixture
def sso_session(engines: Engines, idp_keys):
    """A live session a member opened through the organization's identity provider."""
    db = engines.db
    domain = VerifiedDomain(
        organization_id=engines.org,
        domain="sso-f205.example.com",
        status=DomainStatus.VERIFIED,
        challenge_token="f205-challenge",
        challenge_expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1),
        first_verified_at=datetime.datetime.now(datetime.timezone.utc),
        last_seen_at=datetime.datetime.now(datetime.timezone.utc),
        is_sso_binding=True,
    )
    db.add(domain)
    db.flush()
    config = EnterpriseIdpConfig(
        organization_id=engines.org,
        verified_domain_id=domain.id,
        protocol=IdpProtocol.SAML2,
        display_name="IdP (F-205)",
        is_active=True,
        idp_entity_id=ENTITY,
        idp_sso_url="https://idp.f205.example.com/sso",
        jit_seat_cap=10,
    )
    db.add(config)
    db.flush()
    cert_pem = idp_keys[1]
    der = x509.load_pem_x509_certificate(cert_pem.encode()).public_bytes(serialization.Encoding.DER)
    db.add(IdpSigningCertificate(idp_config_id=config.id, side="IDP", certificate_pem=cert_pem,
                                 fingerprint_sha256=hashlib.sha256(der).hexdigest(), is_primary=True))
    session_index = f"_f205-{uuid.uuid4().hex}"
    issued = session_service.create_session(
        db, user_id=engines.tenant.contributor.user.id, auth_method="SAML2",
        idp_config_id=config.id, idp_session_index=session_index,
    )
    db.commit()
    return issued.session.id, session_index


def _revoked(engines: Engines, session_id) -> bool:
    engines.db.expire_all()
    return engines.db.get(UserSession, session_id).revoked_at is not None


def test_an_unsigned_logout_request_signs_nobody_out(engines: Engines, sso_session) -> None:
    session_id, session_index = sso_session

    response = engines.client.post(SLO, data={"SAMLRequest": _plain(_logout_request(session_index))})

    assert response.status_code == 403, response.text
    assert not _revoked(engines, session_id)


def test_a_logout_request_signed_by_another_key_signs_nobody_out(
    engines: Engines, sso_session, attacker_keys,
) -> None:
    session_id, session_index = sso_session

    forged = _signed(_logout_request(session_index), attacker_keys)
    response = engines.client.post(SLO, data={"SAMLRequest": forged})

    assert response.status_code == 403, response.text
    assert not _revoked(engines, session_id)


def test_the_identity_provider_signs_its_user_out(engines: Engines, sso_session, idp_keys) -> None:
    session_id, session_index = sso_session

    response = engines.client.post(SLO, data={"SAMLRequest": _signed(_logout_request(session_index), idp_keys)})

    assert response.status_code == 200, response.text
    assert response.json()["revoked_sessions"] == 1
    assert _revoked(engines, session_id)
