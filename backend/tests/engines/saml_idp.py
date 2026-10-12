"""A self-signed SAML identity provider for live tests (campaign session 3).

Stands in for Okta or Entra ID: a real RSA key and self-signed certificate, the rows an owner's
setup on the identity console creates (a verified domain bound to SSO, an active SAML connection,
the provider's signing certificate), and signed SAML responses built the way a real provider
builds them (the Assertion signed with an enveloped RSA-SHA256 signature).
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import uuid
import zlib
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, urlparse

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

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
}


def keypair(common_name: str) -> tuple[str, str]:
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
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
        cert.public_bytes(serialization.Encoding.PEM).decode(),
    )


def _instant(offset_seconds: int = 0) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=offset_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def request_id_from_redirect(location: str) -> str:
    """The AuthnRequest ID inside an HTTP-Redirect binding URL (deflated, base64)."""
    raw = parse_qs(urlparse(location).query)["SAMLRequest"][0]
    xml = zlib.decompress(base64.b64decode(raw), -15)
    return etree.fromstring(xml).get("ID")


@dataclass
class TestIdp:
    domain: str
    entity_id: str
    sso_url: str
    key: str
    cert: str
    config: Optional[EnterpriseIdpConfig] = None
    sessions: list[str] = field(default_factory=list)

    @classmethod
    def create(cls, domain: Optional[str] = None) -> "TestIdp":
        domain = domain or f"sso-{uuid.uuid4().hex[:8]}.example.com"
        key, cert = keypair(f"idp.{domain}")
        return cls(
            domain=domain,
            entity_id=f"https://idp.{domain}/metadata",
            sso_url=f"https://idp.{domain}/sso",
            key=key,
            cert=cert,
        )

    def install(self, db, organization_id, *, jit_seat_cap: int = 10, **config_fields) -> EnterpriseIdpConfig:
        """The rows an owner's setup creates: verified domain, active connection, certificate."""
        now = datetime.datetime.now(datetime.timezone.utc)
        domain = VerifiedDomain(
            organization_id=organization_id,
            domain=self.domain,
            status=DomainStatus.VERIFIED,
            challenge_token=f"challenge-{uuid.uuid4().hex[:8]}",
            challenge_expires_at=now + datetime.timedelta(days=1),
            first_verified_at=now,
            last_seen_at=now,
            is_sso_binding=True,
        )
        db.add(domain)
        db.flush()
        config = EnterpriseIdpConfig(
            organization_id=organization_id,
            verified_domain_id=domain.id,
            protocol=IdpProtocol.SAML2,
            display_name=f"Test IdP ({self.domain})",
            is_active=True,
            idp_entity_id=self.entity_id,
            idp_sso_url=self.sso_url,
            jit_seat_cap=jit_seat_cap,
            **config_fields,
        )
        db.add(config)
        db.flush()
        der = x509.load_pem_x509_certificate(self.cert.encode()).public_bytes(serialization.Encoding.DER)
        db.add(
            IdpSigningCertificate(
                idp_config_id=config.id,
                side="IDP",
                certificate_pem=self.cert,
                fingerprint_sha256=hashlib.sha256(der).hexdigest(),
                is_primary=True,
            )
        )
        db.commit()
        self.config = config
        return config

    def response(
        self,
        *,
        email: str,
        acs_url: str,
        sp_entity_id: str,
        in_response_to: Optional[str],
        attributes: Optional[dict[str, list[str]]] = None,
        key: Optional[str] = None,
        cert: Optional[str] = None,
        issuer: Optional[str] = None,
        not_on_or_after: int = 300,
    ) -> str:
        """A signed SAMLResponse, base64-encoded for the HTTP-POST binding."""
        issuer = issuer or self.entity_id
        irt = f' InResponseTo="{in_response_to}"' if in_response_to else ""
        session_index = f"_sess_{uuid.uuid4().hex}"
        self.sessions.append(session_index)
        attrs = "".join(
            f'<saml:Attribute Name="{name}">'
            + "".join(f"<saml:AttributeValue>{value}</saml:AttributeValue>" for value in values)
            + "</saml:Attribute>"
            for name, values in (attributes or {}).items()
        )
        xml = (
            f'<samlp:Response xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}" '
            f'ID="_r{uuid.uuid4().hex}" Version="2.0" IssueInstant="{_instant()}" '
            f'Destination="{acs_url}"{irt}>'
            f"<saml:Issuer>{issuer}</saml:Issuer>"
            '<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
            f'<saml:Assertion ID="_a{uuid.uuid4().hex}" Version="2.0" IssueInstant="{_instant()}">'
            f"<saml:Issuer>{issuer}</saml:Issuer>"
            "<saml:Subject>"
            f'<saml:NameID Format="urn:oasis:names:tc:SAML:2.0:nameid-format:emailAddress">{email}</saml:NameID>'
            '<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
            f'<saml:SubjectConfirmationData NotOnOrAfter="{_instant(not_on_or_after)}" '
            f'Recipient="{acs_url}"{irt}/>'
            "</saml:SubjectConfirmation>"
            "</saml:Subject>"
            f'<saml:Conditions NotBefore="{_instant(-60)}" NotOnOrAfter="{_instant(not_on_or_after)}">'
            f"<saml:AudienceRestriction><saml:Audience>{sp_entity_id}</saml:Audience></saml:AudienceRestriction>"
            "</saml:Conditions>"
            f'<saml:AuthnStatement AuthnInstant="{_instant()}" SessionIndex="{session_index}">'
            "<saml:AuthnContext><saml:AuthnContextClassRef>"
            "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"
            "</saml:AuthnContextClassRef></saml:AuthnContext>"
            "</saml:AuthnStatement>"
            + (f"<saml:AttributeStatement>{attrs}</saml:AttributeStatement>" if attrs else "")
            + "</saml:Assertion></samlp:Response>"
        )
        root = etree.fromstring(xml.encode())
        assertion = root.find("./saml:Assertion", NS)
        signed = XMLSigner(method=methods.enveloped, signature_algorithm=SignatureMethod.RSA_SHA256).sign(
            assertion, key=key or self.key, cert=cert or self.cert
        )
        root.replace(assertion, signed)
        return base64.b64encode(etree.tostring(root)).decode()
