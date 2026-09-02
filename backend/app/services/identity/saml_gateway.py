"""ARCH-16 Step 16.3 — SAML 2.0 gateway."""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlencode

from app.services.identity._integration import get_settings, utcnow
from app.services.identity.errors import AssertionRejected

logger = logging.getLogger(__name__)

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "xenc": "http://www.w3.org/2001/04/xmlenc#",
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
}

ALLOWED_SIGNATURE_ALGORITHMS = frozenset({
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha384",
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha512",
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256",
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha384",
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha512",
    "http://www.w3.org/2007/05/xmldsig-more#sha256-rsa-MGF1",
})
ALLOWED_DIGEST_ALGORITHMS = frozenset({
    "http://www.w3.org/2001/04/xmlenc#sha256",
    "http://www.w3.org/2001/04/xmldsig-more#sha384",
    "http://www.w3.org/2001/04/xmlenc#sha512",
})


def _parse(xml_bytes: bytes):
    try:
        from defusedxml.ElementTree import fromstring
    except ImportError as exc:
        raise AssertionRejected(
            "REJECTED_UNKNOWN",
            "defusedxml is not installed. The ACS endpoint must not parse XML without it.",
        ) from exc
    try:
        return fromstring(xml_bytes)
    except Exception as exc:
        raise AssertionRejected("REJECTED_UNKNOWN", f"malformed XML: {exc}") from exc


def _text(element, path: str) -> str | None:
    if element is None:
        return None
    node = element.find(path, NS)
    return node.text.strip() if node is not None and node.text else None


def _parse_instant(raw: str | None) -> datetime | None:
    if not raw:
        return None
    value = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class SamlCryptoBackend(Protocol):
    def verify(self, xml_bytes: bytes, certificates: list[str]): ...


class SignXmlBackend:
    name = "signxml"

    def verify(self, xml_bytes: bytes, certificates: list[str]):
        try:
            from signxml import XMLVerifier
        except ImportError as exc:
            raise AssertionRejected(
                "REJECTED_UNKNOWN",
                "signxml is not installed; SAML verification cannot run.",
            ) from exc

        last_error: Exception | None = None
        for pem in certificates:
            try:
                result = XMLVerifier().verify(
                    xml_bytes,
                    x509_cert=pem,
                    expect_references=1,
                    ignore_ambiguous_key_info=True,
                )
                return result.signed_xml
            except Exception as exc:
                last_error = exc
                continue
        raise AssertionRejected(
            "REJECTED_SIGNATURE",
            f"no configured certificate verified the signature: {last_error}",
        )


def get_backend() -> SamlCryptoBackend:
    return SignXmlBackend()


@dataclass
class SamlAssertionData:
    assertion_id: str
    issuer: str
    name_id: str
    name_id_format: str | None
    email: str
    authn_instant: datetime
    session_index: str | None
    not_on_or_after: datetime
    in_response_to: str | None
    attributes: dict[str, list[str]] = field(default_factory=dict)
    payload_digest: str = ""

    def attribute(self, *names: str) -> list[str]:
        for n in names:
            if n in self.attributes:
                return self.attributes[n]
        lowered = {k.lower(): v for k, v in self.attributes.items()}
        for n in names:
            if n.lower() in lowered:
                return lowered[n.lower()]
            tail = n.rsplit("/", 1)[-1].lower()
            if tail in lowered:
                return lowered[tail]
        return []


def build_sp_metadata(*, entity_id: str, acs_url: str, slo_url: str,
                      signing_certs: list[str]) -> str:
    key_blocks = []
    for pem in signing_certs:
        body = (pem.replace("-----BEGIN CERTIFICATE-----", "")
                   .replace("-----END CERTIFICATE-----", "")
                   .replace("\n", "").strip())
        key_blocks.append(
            '    <md:KeyDescriptor use="signing">\n'
            '      <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">\n'
            f'        <ds:X509Data><ds:X509Certificate>{body}'
            "</ds:X509Certificate></ds:X509Data>\n"
            "      </ds:KeyInfo>\n"
            "    </md:KeyDescriptor>"
        )
    keys = "\n".join(key_blocks)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" '
        f'entityID="{entity_id}">\n'
        '  <md:SPSSODescriptor AuthnRequestsSigned="true" '
        'WantAssertionsSigned="true" '
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">\n'
        f"{keys}\n"
        '    <md:SingleLogoutService '
        'Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location="{slo_url}"/>\n'
        "    <md:NameIDFormat>"
        "urn:oasis:names:tc:SAML:2.0:nameid-format:emailAddress"
        "</md:NameIDFormat>\n"
        '    <md:AssertionConsumerService '
        'Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location="{acs_url}" index="0" isDefault="true"/>\n'
        "  </md:SPSSODescriptor>\n"
        "</md:EntityDescriptor>\n"
    )


def build_authn_request(*, sso_url: str, sp_entity_id: str, acs_url: str,
                        force_authn: bool = False,
                        name_id_format: str | None = None,
                        relay_state: str | None = None) -> tuple[str, str]:
    request_id = "_" + secrets.token_hex(20)
    issue_instant = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    fmt = name_id_format or "urn:oasis:names:tc:SAML:2.0:nameid-format:emailAddress"

    xml = (
        '<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="{request_id}" Version="2.0" IssueInstant="{issue_instant}" '
        f'Destination="{sso_url}" '
        'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'AssertionConsumerServiceURL="{acs_url}"'
        f'{" ForceAuthn=\"true\"" if force_authn else ""}>'
        f"<saml:Issuer>{sp_entity_id}</saml:Issuer>"
        f'<samlp:NameIDPolicy Format="{fmt}" AllowCreate="true"/>'
        "</samlp:AuthnRequest>"
    )

    deflated = zlib.compress(xml.encode("utf-8"))[2:-4]
    params = {"SAMLRequest": base64.b64encode(deflated).decode("ascii")}
    if relay_state:
        params["RelayState"] = relay_state
    joiner = "&" if "?" in sso_url else "?"
    return request_id, f"{sso_url}{joiner}{urlencode(params)}"


def verify_response(
    *,
    saml_response_b64: str,
    idp_certificates: list[str],
    sp_entity_id: str,
    acs_url: str,
    expected_in_response_to: str | None,
    allow_unsolicited: bool,
    clock_skew_s: int | None = None,
) -> SamlAssertionData:
    settings = get_settings()
    skew = timedelta(seconds=int(
        clock_skew_s if clock_skew_s is not None
        else getattr(settings, "SAML_CLOCK_SKEW_S", 120)))
    now = utcnow()

    try:
        raw = base64.b64decode(saml_response_b64, validate=True)
    except Exception as exc:
        raise AssertionRejected("REJECTED_UNKNOWN",
                                f"SAMLResponse is not valid base64: {exc}") from exc

    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    envelope = _parse(raw)

    destination = envelope.get("Destination")
    if destination and destination.rstrip("/") != acs_url.rstrip("/"):
        raise AssertionRejected(
            "REJECTED_DESTINATION",
            f"Destination {destination!r} does not match this ACS URL")

    status = envelope.find("./samlp:Status/samlp:StatusCode", NS)
    if status is not None:
        value = status.get("Value", "")
        if not value.endswith(":Success"):
            raise AssertionRejected("REJECTED_UNKNOWN", f"IdP status {value}")

    for sig_method in envelope.iter(f"{{{NS['ds']}}}SignatureMethod"):
        alg = sig_method.get("Algorithm", "")
        if alg not in ALLOWED_SIGNATURE_ALGORITHMS:
            raise AssertionRejected(
                "REJECTED_SIGNATURE",
                f"signature algorithm {alg!r} is not permitted")
    for dig_method in envelope.iter(f"{{{NS['ds']}}}DigestMethod"):
        alg = dig_method.get("Algorithm", "")
        if alg not in ALLOWED_DIGEST_ALGORITHMS:
            raise AssertionRejected(
                "REJECTED_SIGNATURE", f"digest algorithm {alg!r} is not permitted")

    if envelope.find(".//xenc:EncryptedData", NS) is not None:
        raise AssertionRejected(
            "REJECTED_UNKNOWN",
            "encrypted assertions require the xmlsec backend; set SAML_CRYPTO_BACKEND=python3-saml")

    verified_root = get_backend().verify(raw, idp_certificates)

    assertion = verified_root
    if not assertion.tag.endswith("Assertion"):
        assertion = verified_root.find("./saml:Assertion", NS)
    if assertion is None:
        raise AssertionRejected(
            "REJECTED_SIGNATURE",
            "the signature did not cover an Assertion element")

    assertion_id = assertion.get("ID")
    if not assertion_id:
        raise AssertionRejected("REJECTED_SIGNATURE", "verified assertion has no ID")

    issuer = _text(assertion, "./saml:Issuer") or ""

    audiences = [
        (node.text or "").strip()
        for node in assertion.findall(
            "./saml:Conditions/saml:AudienceRestriction/saml:Audience", NS)
    ]
    if audiences and sp_entity_id not in audiences:
        raise AssertionRejected(
            "REJECTED_AUDIENCE",
            f"audience {audiences!r} does not include {sp_entity_id!r}")

    conditions = assertion.find("./saml:Conditions", NS)
    not_before = _parse_instant(conditions.get("NotBefore")) if conditions is not None else None
    not_on_or_after = _parse_instant(conditions.get("NotOnOrAfter")) if conditions is not None else None
    if not_before and now + skew < not_before:
        raise AssertionRejected("REJECTED_EXPIRED", "assertion is not yet valid")
    if not_on_or_after and now - skew >= not_on_or_after:
        raise AssertionRejected("REJECTED_EXPIRED", "assertion has expired")

    subject_confirmation = assertion.find(
        "./saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData", NS)
    in_response_to = (subject_confirmation.get("InResponseTo")
                      if subject_confirmation is not None else None)
    if in_response_to is None:
        in_response_to = envelope.get("InResponseTo")

    if expected_in_response_to is not None:
        if in_response_to != expected_in_response_to:
            raise AssertionRejected(
                "REJECTED_UNSOLICITED",
                "InResponseTo does not match the AuthnRequest we issued")
    elif in_response_to is not None:
        raise AssertionRejected(
            "REJECTED_UNSOLICITED",
            "assertion references an AuthnRequest we have no record of")
    elif not allow_unsolicited:
        raise AssertionRejected(
            "REJECTED_UNSOLICITED",
            "IdP-initiated SSO is disabled for this configuration")

    if subject_confirmation is not None:
        recipient = subject_confirmation.get("Recipient")
        if recipient and recipient.rstrip("/") != acs_url.rstrip("/"):
            raise AssertionRejected(
                "REJECTED_DESTINATION",
                f"SubjectConfirmation Recipient {recipient!r} is not this ACS URL")

    name_id_node = assertion.find("./saml:Subject/saml:NameID", NS)
    name_id = (name_id_node.text or "").strip() if name_id_node is not None else ""
    if not name_id:
        raise AssertionRejected("REJECTED_UNKNOWN", "assertion carries no NameID")
    name_id_format = name_id_node.get("Format") if name_id_node is not None else None

    authn_statement = assertion.find("./saml:AuthnStatement", NS)
    authn_instant = _parse_instant(
        authn_statement.get("AuthnInstant") if authn_statement is not None else None)
    if authn_instant is None:
        raise AssertionRejected(
            "REJECTED_NO_AUTHN_INSTANT",
            "assertion carries no AuthnInstant.")
    if authn_instant > now + skew:
        raise AssertionRejected("REJECTED_EXPIRED", "AuthnInstant is in the future")
    session_index = (authn_statement.get("SessionIndex")
                     if authn_statement is not None else None)

    attributes: dict[str, list[str]] = {}
    for attr in assertion.findall("./saml:AttributeStatement/saml:Attribute", NS):
        key = attr.get("Name") or attr.get("FriendlyName")
        if not key:
            continue
        values = [(v.text or "").strip()
                  for v in attr.findall("./saml:AttributeValue", NS)
                  if v.text and v.text.strip()]
        if values:
            attributes.setdefault(key, []).extend(values)

    email = name_id
    if "@" not in email:
        for candidate in ("email", "emailAddress", "mail",
                          "urn:oid:0.9.2342.19200300.100.1.3",
                          "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"):
            found = attributes.get(candidate)
            if found:
                email = found[0]
                break
    if "@" not in email:
        raise AssertionRejected(
            "REJECTED_UNKNOWN",
            "no email address in NameID or attributes; cannot bind to a domain")

    return SamlAssertionData(
        assertion_id=assertion_id,
        issuer=issuer,
        name_id=name_id,
        name_id_format=name_id_format,
        email=email.strip().lower(),
        authn_instant=authn_instant,
        session_index=session_index,
        not_on_or_after=not_on_or_after or (now + timedelta(minutes=5)),
        in_response_to=in_response_to,
        attributes=attributes,
        payload_digest=digest,
    )


def guard_replay(db, *, assertion_id: str, idp_config_id,
                 not_on_or_after: datetime) -> None:
    from sqlalchemy import text as sql_text
    from sqlalchemy.exc import IntegrityError

    try:
        with db.begin_nested():
            db.execute(
                sql_text(
                    "INSERT INTO saml_assertion_replay_guard "
                    "(assertion_id, idp_config_id, not_on_or_after) "
                    "VALUES (:aid, :cid, :noa)"
                ),
                {"aid": assertion_id, "cid": str(idp_config_id),
                 "noa": not_on_or_after},
            )
    except IntegrityError as exc:
        raise AssertionRejected(
            "REJECTED_REPLAY",
            f"assertion {assertion_id} has already been consumed") from exc


def sweep_replay_guard(db, *, grace_hours: int = 24) -> int:
    from sqlalchemy import text as sql_text

    result = db.execute(
        sql_text("DELETE FROM saml_assertion_replay_guard "
                 "WHERE not_on_or_after < now() - make_interval(hours => :h)"),
        {"h": grace_hours},
    )
    db.commit()
    return result.rowcount or 0


def parse_logout_request(saml_request_b64: str) -> tuple[str | None, str | None]:
    try:
        raw = base64.b64decode(saml_request_b64, validate=True)
    except Exception:
        return None, None
    try:
        root = _parse(raw)
    except AssertionRejected:
        try:
            root = _parse(zlib.decompress(raw, -15))
        except Exception:
            return None, None
    name_id = _text(root, "./saml:NameID")
    session_index = _text(root, "./samlp:SessionIndex")
    return name_id, session_index
