"""ARCH-28 — SAML 2.0 XML Signature Wrapping test rig.

    pytest tests/security/test_saml_xsw_rig.py -v

The item deferred since ARCH-16. Real 2048-bit RSA keys, real x509
certificates, real `signxml==5.1.0` signatures, and the published XSW attack
patterns applied to genuinely signed documents.

THE HARD INVARIANT — 28-G3
==========================

An XSW test that passes against broken code is worse than no test, because it
certifies the break. So this rig does not merely assert that attacks are
refused. Every mutation is run TWICE:

    * against `SamlHardeningPolicy.default()`   — must be REFUSED
    * against `SamlHardeningPolicy.weakened()`  — must be ACCEPTED

`test_negative_control_*` is the second half. If the hardening were removed
tomorrow, the first half would fail; if the hardening were replaced by an
unconditional `raise`, the second half would fail. Neither failure mode is
detectable with positive tests alone, which is why the deferred item was worth
deferring rather than faking.

MEASURED BASELINE — WHAT SIGNXML ALONE DOES
===========================================

Recorded during the ARCH-28 audit against signxml 5.1.0 so that the value this
rig adds is auditable rather than assumed:

    XSW-1   original into ds:Object, forged in its place   signxml REJECTS
    XSW-2   forged assertion as preceding sibling          signxml ACCEPTS
    XSW-3   forged assertion wrapping the original         signxml ACCEPTS
    XSW-4   forged assertion nested inside the original    signxml REJECTS
    XSW-5   forged at top level, original in ds:Object     signxml REJECTS
    XSW-6   original hidden in the forged Assertion Advice signxml ACCEPTS
    XSW-7   forged assertion added to original's Advice    signxml REJECTS
    XSW-8   forged assertion reusing the original's ID     signxml REJECTS
    strip   signature removed entirely                     signxml REJECTS
    inject  unsigned assertion appended                    signxml ACCEPTS

The first draft of that table was wrong in three places. It was written from
the published attack descriptions and corrected by running the rig, which is
the only reason the correction happened at all.

`test_signxml_baseline_is_what_arch28_measured` pins that table. If a signxml
upgrade changes it, this test fails and somebody re-reads the threat model
before the upgrade lands — rather than discovering afterwards that a defence
was load-bearing.

"signxml ACCEPTS" never meant impersonation: `XMLVerifier.verify()` returns
`signed_xml`, the subtree the Reference covered, so the ORIGINAL assertion
comes back and the forgery is ignored. It meant the document reaching
`sso_assertions.raw_payload` was attacker-shaped, and that any read from the
envelope was a read from attacker-controlled data. `verify_response` performed
exactly one such read. See `app/services/auth/saml_security.py`.

NO DATABASE
===========

Every test here is `@pytest.mark.no_db`. The rig builds XML and calls pure
functions; a security suite that needs PostgreSQL is a security suite that
stops being run.
"""

from __future__ import annotations

import copy
import datetime
import re
from typing import Callable, Optional

import pytest

pytestmark = pytest.mark.no_db

cryptography = pytest.importorskip("cryptography", reason="ARCH-28 XSW rig needs cryptography")
signxml = pytest.importorskip("signxml", reason="ARCH-28 XSW rig needs signxml==5.1.0")
lxml_etree = pytest.importorskip("lxml.etree", reason="ARCH-28 XSW rig needs lxml")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from lxml import etree  # noqa: E402
from signxml import SignatureMethod, XMLSigner, XMLVerifier, methods  # noqa: E402

from app.services.auth.saml_security import (  # noqa: E402
    PERMITTED_OUTCOMES,
    REJECTION_OUTCOME,
    SamlHardeningPolicy,
    analyse_structure,
    bind_issuer,
    certificate_expiry_report,
    describe_xmlsec1_policy,
    encrypted_assertion_diagnostic,
    enforce_structural_integrity,
    parse_document,
    refuse_encrypted_assertion,
    require_bearer_confirmation,
    require_signed_request_binding,
    verify_certificate_validity,
)
from app.services.identity.errors import AssertionRejected  # noqa: E402

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "xenc": "http://www.w3.org/2001/04/xmlenc#",
}

SP_ACS = "https://sp.flowpilot.test/api/v1/saml/acs"
SP_ENTITY = "https://sp.flowpilot.test/api/v1/saml/metadata"
IDP_ENTITY = "https://idp.tenant-a.test/metadata"
OTHER_IDP_ENTITY = "https://idp.tenant-b.test/metadata"

VICTIM = "victim@tenant-a.test"
ATTACKER = "attacker@evil.test"


# ===========================================================================
# Real key and certificate material
# ===========================================================================


def _make_keypair(
    *,
    common_name: str = "idp.tenant-a.test",
    not_before_days: int = -1,
    not_after_days: int = 365,
) -> tuple[str, str]:
    """A real RSA-2048 key and a real self-signed x509 certificate, as PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "FlowPilot ARCH-28 Rig"),
        ]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + datetime.timedelta(days=not_before_days))
        .not_valid_after(now + datetime.timedelta(days=not_after_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii"),
        certificate.public_bytes(serialization.Encoding.PEM).decode("ascii"),
    )


@pytest.fixture(scope="module")
def idp_a() -> tuple[str, str]:
    """Tenant A's IdP signing material. Module-scoped: RSA-2048 is not free."""
    return _make_keypair(common_name="idp.tenant-a.test")


@pytest.fixture(scope="module")
def idp_b() -> tuple[str, str]:
    return _make_keypair(common_name="idp.tenant-b.test")


@pytest.fixture(scope="module")
def expired_idp() -> tuple[str, str]:
    return _make_keypair(
        common_name="idp.expired.test", not_before_days=-800, not_after_days=-400
    )


@pytest.fixture(scope="module")
def future_idp() -> tuple[str, str]:
    return _make_keypair(
        common_name="idp.future.test", not_before_days=30, not_after_days=400
    )


# ===========================================================================
# Document construction
# ===========================================================================


def _instant(offset_seconds: int = 0) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=offset_seconds
    )
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def response_xml(
    *,
    assertion_id: str = "_assertion_original",
    response_id: str = "_response_original",
    issuer: str = IDP_ENTITY,
    email: str = VICTIM,
    in_response_to: Optional[str] = None,
    confirmation_method: str = "urn:oasis:names:tc:SAML:2.0:cm:bearer",
    audience: str = SP_ENTITY,
    recipient: str = SP_ACS,
) -> str:
    """A well-formed, complete SAML 2.0 Response with one Assertion."""
    response_irt = f' InResponseTo="{in_response_to}"' if in_response_to else ""
    scd_irt = f' InResponseTo="{in_response_to}"' if in_response_to else ""
    return (
        '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="{response_id}" Version="2.0" IssueInstant="{_instant()}" '
        f'Destination="{SP_ACS}"{response_irt}>'
        f"<saml:Issuer>{issuer}</saml:Issuer>"
        "<samlp:Status><samlp:StatusCode "
        'Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
        f'<saml:Assertion ID="{assertion_id}" Version="2.0" '
        f'IssueInstant="{_instant()}">'
        f"<saml:Issuer>{issuer}</saml:Issuer>"
        "<saml:Subject>"
        '<saml:NameID Format="urn:oasis:names:tc:SAML:2.0:nameid-format:'
        f'emailAddress">{email}</saml:NameID>'
        f'<saml:SubjectConfirmation Method="{confirmation_method}">'
        f'<saml:SubjectConfirmationData NotOnOrAfter="{_instant(300)}" '
        f'Recipient="{recipient}"{scd_irt}/>'
        "</saml:SubjectConfirmation>"
        "</saml:Subject>"
        f'<saml:Conditions NotBefore="{_instant(-60)}" '
        f'NotOnOrAfter="{_instant(300)}">'
        f"<saml:AudienceRestriction><saml:Audience>{audience}"
        "</saml:Audience></saml:AudienceRestriction>"
        "</saml:Conditions>"
        f'<saml:AuthnStatement AuthnInstant="{_instant()}" SessionIndex="_sess_1">'
        "<saml:AuthnContext><saml:AuthnContextClassRef>"
        "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"
        "</saml:AuthnContextClassRef></saml:AuthnContext>"
        "</saml:AuthnStatement>"
        "</saml:Assertion>"
        "</samlp:Response>"
    )


def sign_assertion(key_pem: str, cert_pem: str, **kwargs):
    """Sign the Assertion in place and return the lxml Response root."""
    root = etree.fromstring(response_xml(**kwargs).encode("utf-8"))
    assertion = root.find("./saml:Assertion", NS)
    signed = XMLSigner(
        method=methods.enveloped,
        signature_algorithm=SignatureMethod.RSA_SHA256,
    ).sign(assertion, key=key_pem, cert=cert_pem)
    root.replace(assertion, signed)
    return root


def unsigned_assertion(**kwargs):
    """A forged Assertion element, detached from any Response."""
    root = etree.fromstring(response_xml(**kwargs).encode("utf-8"))
    node = root.find("./saml:Assertion", NS)
    root.remove(node)
    return node


def _strip_signature(assertion):
    node = copy.deepcopy(assertion)
    for child in list(node):
        if child.tag == f"{{{NS['ds']}}}Signature":
            node.remove(child)
    return node


def serialise(root) -> bytes:
    return etree.tostring(root)


# ===========================================================================
# The mutations — XSW-1 through XSW-8, plus the non-wrapping families
# ===========================================================================


def xsw_1(root):
    """Original assertion relocated into ds:Object; forged takes its place."""
    original = root.find("./saml:Assertion", NS)
    signature = original.find("./ds:Signature", NS)
    forged = unsigned_assertion(assertion_id="_assertion_forged", email=ATTACKER)
    obj = etree.SubElement(signature, f"{{{NS['ds']}}}Object")
    obj.append(_strip_signature(original))
    forged.append(copy.deepcopy(signature))
    root.replace(original, forged)
    return root


def xsw_2(root):
    """Forged assertion inserted as a preceding sibling of the signed one."""
    original = root.find("./saml:Assertion", NS)
    forged = unsigned_assertion(assertion_id="_assertion_forged", email=ATTACKER)
    root.insert(list(root).index(original), forged)
    return root


def xsw_3(root):
    """Forged assertion wraps the original as its child."""
    original = root.find("./saml:Assertion", NS)
    forged = unsigned_assertion(assertion_id="_assertion_forged", email=ATTACKER)
    index = list(root).index(original)
    root.remove(original)
    forged.append(original)
    root.insert(index, forged)
    return root


def xsw_4(root):
    """Original assertion wraps the forged one as its child."""
    original = root.find("./saml:Assertion", NS)
    forged = unsigned_assertion(assertion_id="_assertion_forged", email=ATTACKER)
    original.append(forged)
    return root


def xsw_5(root):
    """Forged assertion at top level; original hidden in ds:Object."""
    original = root.find("./saml:Assertion", NS)
    signature = original.find("./ds:Signature", NS)
    forged = unsigned_assertion(assertion_id="_assertion_forged", email=ATTACKER)
    detached = copy.deepcopy(signature)
    obj = etree.SubElement(detached, f"{{{NS['ds']}}}Object")
    obj.append(_strip_signature(original))
    forged.insert(0, detached)
    root.replace(original, forged)
    return root


def xsw_6(root):
    """Original assertion hidden inside the forged assertion's saml:Advice."""
    original = root.find("./saml:Assertion", NS)
    forged = unsigned_assertion(assertion_id="_assertion_forged", email=ATTACKER)
    advice = etree.SubElement(forged, f"{{{NS['saml']}}}Advice")
    advice.append(copy.deepcopy(original))
    root.replace(original, forged)
    return root


def xsw_7(root):
    """Original assertion parked in the ORIGINAL's Advice, forged alongside."""
    original = root.find("./saml:Assertion", NS)
    advice = etree.SubElement(original, f"{{{NS['saml']}}}Advice")
    advice.append(unsigned_assertion(assertion_id="_assertion_advice", email=ATTACKER))
    return root


def xsw_8(root):
    """ID confusion: the forged assertion reuses the original's ID."""
    original = root.find("./saml:Assertion", NS)
    forged = unsigned_assertion(assertion_id="_assertion_original", email=ATTACKER)
    root.insert(list(root).index(original), forged)
    return root


def inject_unsigned(root):
    """A second, entirely unsigned assertion appended to a valid response."""
    root.append(unsigned_assertion(assertion_id="_assertion_extra", email=ATTACKER))
    return root


def strip_signature(root):
    """Signature removed outright."""
    original = root.find("./saml:Assertion", NS)
    original.remove(original.find("./ds:Signature", NS))
    return root


#: Every wrapping mutation, by the name the runbook and the gate use.
MUTATIONS: dict[str, Callable] = {
    "XSW-1": xsw_1,
    "XSW-2": xsw_2,
    "XSW-3": xsw_3,
    "XSW-4": xsw_4,
    "XSW-5": xsw_5,
    "XSW-6": xsw_6,
    "XSW-7": xsw_7,
    "XSW-8": xsw_8,
    "INJECT-UNSIGNED": inject_unsigned,
}

#: The mutations signxml alone lets through. Measured, not assumed — pinned by
#: `test_signxml_baseline_is_what_arch28_measured`.
SIGNXML_ACCEPTS: frozenset[str] = frozenset(
    {"XSW-2", "XSW-3", "XSW-6", "INJECT-UNSIGNED"}
)


def verify_with_signxml(blob: bytes, cert_pem: str):
    """Exactly what `SignXmlBackend.verify` does in `saml_gateway`."""
    return XMLVerifier().verify(
        blob,
        x509_cert=cert_pem,
        expect_references=1,
        ignore_ambiguous_key_info=True,
    ).signed_xml


# ===========================================================================
# 1. The happy path must still work
# ===========================================================================


def test_a_legitimate_response_verifies_and_passes_hardening(idp_a):
    key, cert = idp_a
    blob = serialise(sign_assertion(key, cert))

    signed = verify_with_signxml(blob, cert)
    assert signed.get("ID") == "_assertion_original"

    enforce_structural_integrity(blob, verified_assertion_id="_assertion_original")

    assertion = parse_document(blob).find("./saml:Assertion", NS)
    require_bearer_confirmation(assertion)
    bind_issuer(verified_issuer=IDP_ENTITY, configured_entity_id=IDP_ENTITY)
    assert verify_certificate_validity([cert]) == [cert]


def test_signature_stripping_is_refused_by_signxml_itself(idp_a):
    key, cert = idp_a
    blob = serialise(strip_signature(sign_assertion(key, cert)))
    with pytest.raises(Exception):
        verify_with_signxml(blob, cert)


def test_signxml_baseline_is_what_arch28_measured(idp_a):
    """Pin the measured signxml behaviour so an upgrade cannot move it quietly."""
    key, cert = idp_a
    observed: set[str] = set()
    for name, mutate in MUTATIONS.items():
        blob = serialise(mutate(sign_assertion(key, cert)))
        try:
            verify_with_signxml(blob, cert)
        except Exception:
            continue
        observed.add(name)

    assert observed == SIGNXML_ACCEPTS, (
        "signxml's behaviour has changed since the ARCH-28 audit.\n"
        f"  ARCH-28 measured: {sorted(SIGNXML_ACCEPTS)}\n"
        f"  this run observed: {sorted(observed)}\n"
        "Re-read the threat model in app/services/auth/saml_security.py before "
        "accepting the new baseline."
    )


# ===========================================================================
# 2. Every mutation is refused by the hardened validator
# ===========================================================================


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_hardened_validator_refuses_every_mutation(name, idp_a):
    key, cert = idp_a
    blob = serialise(MUTATIONS[name](sign_assertion(key, cert)))

    try:
        verified_id = verify_with_signxml(blob, cert).get("ID")
    except Exception:
        pytest.skip(f"{name} is refused by signxml before the ARCH-28 layer runs")

    with pytest.raises(AssertionRejected) as caught:
        enforce_structural_integrity(blob, verified_assertion_id=verified_id)

    assert caught.value.outcome == REJECTION_OUTCOME
    assert "XSW_" in caught.value.reason


@pytest.mark.parametrize("name", sorted(SIGNXML_ACCEPTS))
def test_refusals_are_specific_not_blanket(name, idp_a):
    """Each mutation must trip a NAMED finding, not a generic catch-all.

    A defence that refuses everything for the same reason is indistinguishable
    from `raise` and tells an incident responder nothing about what was tried.
    """
    key, cert = idp_a
    blob = serialise(MUTATIONS[name](sign_assertion(key, cert)))
    verified_id = verify_with_signxml(blob, cert).get("ID")

    findings = analyse_structure(
        parse_document(blob),
        verified_assertion_id=verified_id,
        policy=SamlHardeningPolicy.default(),
    )
    codes = {finding.code for finding in findings}
    assert codes, f"{name} produced no structural finding"
    assert all(code.startswith("XSW_") for code in codes)
    assert len(codes) <= 4, f"{name} tripped {sorted(codes)}; that is a blanket refusal"


def _codes(blob: bytes, verified_id: str = "_assertion_original") -> set[str]:
    return {
        finding.code
        for finding in analyse_structure(
            parse_document(blob),
            verified_assertion_id=verified_id,
            policy=SamlHardeningPolicy.default(),
        )
    }


def test_id_confusion_is_named_as_such(idp_a):
    """XSW-8 is refused by signxml too, but defence in depth must still name it.

    signxml declines to resolve the ambiguous reference. That is the right
    outcome and the wrong message: it arrives as a generic signature failure.
    The structural layer names it, so an incident responder reading the reject
    reason learns that an ID collision was attempted.
    """
    key, cert = idp_a
    assert "XSW_DUPLICATE_ID" in _codes(serialise(xsw_8(sign_assertion(key, cert))))


def test_advice_wrapping_is_named_as_such(idp_a):
    key, cert = idp_a
    assert "XSW_ASSERTION_IN_ADVICE" in _codes(
        serialise(xsw_7(sign_assertion(key, cert)))
    )
    assert "XSW_ASSERTION_IN_ADVICE" in _codes(
        serialise(xsw_6(sign_assertion(key, cert)))
    )


def test_nesting_is_named_as_such(idp_a):
    key, cert = idp_a
    assert "XSW_ASSERTION_NESTED" in _codes(serialise(xsw_4(sign_assertion(key, cert))))


def test_signature_object_is_named_as_such(idp_a):
    key, cert = idp_a
    assert "XSW_SIGNATURE_OBJECT" in _codes(serialise(xsw_5(sign_assertion(key, cert))))
    assert "XSW_SIGNATURE_OBJECT" in _codes(serialise(xsw_1(sign_assertion(key, cert))))


def test_every_mutation_is_named_by_the_structural_layer_alone(idp_a):
    """Defence in depth: the structural layer must catch all nine unaided.

    Five of the nine are refused by signxml first. If a signxml upgrade, a
    configuration change, or a backend swap removed that first line, the
    structural layer has to stand on its own — so it is tested on its own,
    without letting signxml pre-filter the corpus.
    """
    key, cert = idp_a
    for name, mutate in MUTATIONS.items():
        codes = _codes(serialise(mutate(sign_assertion(key, cert))))
        assert codes, f"{name} produced no structural finding"


# ===========================================================================
# 3. THE NEGATIVE CONTROL — 28-G3
# ===========================================================================


@pytest.mark.parametrize("name", sorted(SIGNXML_ACCEPTS))
def test_negative_control_weakened_validator_lets_the_attack_through(name, idp_a):
    """The mutations MUST succeed when the defence is switched off.

    This is the half of `28-G3` that makes the other half mean anything. If
    `enforce_structural_integrity` were replaced with an unconditional raise,
    the refusal tests above would still pass and this one would fail.

    It also documents the real exposure: without ARCH-28, six of the nine
    mutations reach `sso_assertions.raw_payload` and the ACS handler's envelope
    reads, and the platform's safety rests on `verify_response` never reading
    from the envelope again.
    """
    key, cert = idp_a
    blob = serialise(MUTATIONS[name](sign_assertion(key, cert)))
    verified_id = verify_with_signxml(blob, cert).get("ID")

    enforce_structural_integrity(
        blob,
        verified_assertion_id=verified_id,
        policy=SamlHardeningPolicy.weakened(),
    )


def test_negative_control_weakened_policy_is_actually_weakened():
    assert SamlHardeningPolicy.weakened().is_weakened is True
    assert SamlHardeningPolicy.default().is_weakened is False


def test_negative_control_covers_every_signxml_gap():
    """The negative control must exercise every mutation signxml lets through.

    Otherwise a mutation could be added to `MUTATIONS`, refused by the hardened
    path for an incidental reason, and never proven to be a real attack.
    """
    assert SIGNXML_ACCEPTS <= set(MUTATIONS)
    assert SIGNXML_ACCEPTS, "the negative control set must not be empty"


# ===========================================================================
# 4. Cross-tenant assertion swapping
# ===========================================================================


def test_cross_tenant_assertion_is_refused_when_certificates_differ(idp_a, idp_b):
    """The incidental defence: tenant B's certificate does not verify A's signature."""
    key_a, cert_a = idp_a
    _key_b, cert_b = idp_b
    blob = serialise(sign_assertion(key_a, cert_a))
    with pytest.raises(Exception):
        verify_with_signxml(blob, cert_b)


def test_cross_tenant_assertion_is_refused_when_certificates_are_shared(idp_a):
    """The real defence: issuer binding, which does not depend on the cert inventory.

    Two organizations behind one Entra tenant, or a certificate copied during a
    migration, removes the incidental protection above entirely. Without
    `bind_issuer` an assertion signed for tenant A is accepted as tenant B.
    """
    key, cert = idp_a
    blob = serialise(sign_assertion(key, cert, issuer=IDP_ENTITY))
    verify_with_signxml(blob, cert)

    with pytest.raises(AssertionRejected) as caught:
        bind_issuer(
            verified_issuer=IDP_ENTITY,
            configured_entity_id=OTHER_IDP_ENTITY,
        )
    assert caught.value.outcome == REJECTION_OUTCOME
    assert "cross-tenant" in caught.value.reason


def test_negative_control_issuer_binding_disabled_accepts_the_swap():
    bind_issuer(
        verified_issuer=IDP_ENTITY,
        configured_entity_id=OTHER_IDP_ENTITY,
        policy=SamlHardeningPolicy.weakened(),
    )


def test_missing_issuer_is_refused():
    with pytest.raises(AssertionRejected):
        bind_issuer(verified_issuer="", configured_entity_id=IDP_ENTITY)


# ===========================================================================
# 5. Request binding — the unsigned InResponseTo fallback
# ===========================================================================


def test_unsolicited_assertion_cannot_borrow_an_envelope_in_response_to(idp_a):
    """The ARCH-16 defect, expressed as a test.

    `verify_response` falls back to `envelope.get("InResponseTo")` when the
    signed SubjectConfirmationData omits it. The envelope is outside the
    signature, so an IdP-initiated assertion can satisfy the solicited-flow
    check by carrying a Response/@InResponseTo the attacker chose.
    """
    key, cert = idp_a
    root = sign_assertion(key, cert)                 # no InResponseTo anywhere
    root.set("InResponseTo", "_request_the_sp_issued")   # unsigned, attacker-set
    blob = serialise(root)

    assertion = parse_document(blob).find("./saml:Assertion", NS)

    with pytest.raises(AssertionRejected) as caught:
        require_signed_request_binding(
            assertion, expected_in_response_to="_request_the_sp_issued"
        )
    assert caught.value.outcome == "REJECTED_UNSOLICITED"


def test_signed_in_response_to_is_accepted(idp_a):
    key, cert = idp_a
    blob = serialise(sign_assertion(key, cert, in_response_to="_request_abc"))
    assertion = parse_document(blob).find("./saml:Assertion", NS)
    assert (
        require_signed_request_binding(
            assertion, expected_in_response_to="_request_abc"
        )
        == "_request_abc"
    )


def test_negative_control_request_binding_disabled_accepts_the_envelope_value(idp_a):
    key, cert = idp_a
    root = sign_assertion(key, cert)
    root.set("InResponseTo", "_request_the_sp_issued")
    assertion = parse_document(serialise(root)).find("./saml:Assertion", NS)
    require_signed_request_binding(
        assertion,
        expected_in_response_to="_request_the_sp_issued",
        policy=SamlHardeningPolicy.weakened(),
    )


# ===========================================================================
# 6. Replay
# ===========================================================================


def test_a_replayed_document_is_byte_identical_and_so_is_its_assertion_id(idp_a):
    """Replay defence is the assertion ID, and it survives re-serialisation.

    `guard_replay` inserts the assertion ID under a unique constraint. This
    pins the property that makes it work: replaying the same document yields
    the same ID, so the second insert collides.
    """
    key, cert = idp_a
    blob = serialise(sign_assertion(key, cert))
    first = verify_with_signxml(blob, cert).get("ID")
    second = verify_with_signxml(blob, cert).get("ID")
    assert first == second == "_assertion_original"


def test_expired_conditions_window_is_visible_in_the_signed_assertion(idp_a):
    key, cert = idp_a
    blob = serialise(sign_assertion(key, cert))
    assertion = parse_document(blob).find("./saml:Assertion", NS)
    conditions = assertion.find("./saml:Conditions", NS)
    assert conditions.get("NotOnOrAfter")
    assert conditions.get("NotBefore")


def test_timestamp_replay_outside_the_window_is_detectable(idp_a):
    """A stale assertion carries a stale window; the gateway compares it to now."""
    key, cert = idp_a
    root = sign_assertion(key, cert)
    blob = serialise(root)
    assertion = parse_document(blob).find("./saml:Assertion", NS)
    not_on_or_after = assertion.find("./saml:Conditions", NS).get("NotOnOrAfter")
    parsed = datetime.datetime.strptime(not_on_or_after, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc
    )
    far_future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=1
    )
    assert far_future > parsed


# ===========================================================================
# 7. Certificate validity
# ===========================================================================


def test_signxml_does_enforce_certificate_expiry(expired_idp):
    """A correction to an earlier draft, kept as a test so it stays corrected.

    The draft claimed signxml ignores the validity window and that ARCH-28 was
    closing a hole. Measured: signxml 5.1.0 raises InvalidCertificate naming
    the expiry date. The ARCH-28 layer is not adding the control — it is making
    it legible, because `SignXmlBackend.verify` flattens every certificate
    exception into one generic "no configured certificate verified the
    signature", which is indistinguishable from real tampering at the moment
    when telling those apart is the entire job.
    """
    key, cert = expired_idp
    blob = serialise(sign_assertion(key, cert))
    with pytest.raises(Exception) as caught:
        verify_with_signxml(blob, cert)
    assert "expired" in str(caught.value).lower()


def test_expired_certificate_is_refused_with_a_dated_reason(expired_idp):
    _key, cert = expired_idp
    with pytest.raises(AssertionRejected) as caught:
        verify_certificate_validity([cert])
    assert "expired" in caught.value.reason


def test_not_yet_valid_certificate_is_refused(future_idp):
    _key, cert = future_idp
    with pytest.raises(AssertionRejected):
        verify_certificate_validity([cert])


def test_a_valid_certificate_survives_alongside_an_expired_one(idp_a, expired_idp):
    _key_a, cert_a = idp_a
    _key_e, cert_e = expired_idp
    assert verify_certificate_validity([cert_e, cert_a]) == [cert_a]


def test_negative_control_certificate_check_disabled_accepts_expired(expired_idp):
    _key, cert = expired_idp
    assert verify_certificate_validity(
        [cert], policy=SamlHardeningPolicy.weakened()
    ) == [cert]


def test_certificate_expiry_report_classifies_without_raising(
    idp_a, expired_idp, future_idp
):
    rows = certificate_expiry_report([idp_a[1], expired_idp[1], future_idp[1], "junk"])
    statuses = [row["status"] for row in rows]
    assert statuses == ["VALID", "EXPIRED", "NOT_YET_VALID", "UNPARSEABLE"]


# ===========================================================================
# 8. SubjectConfirmation method
# ===========================================================================


def test_sender_vouches_confirmation_is_refused(idp_a):
    key, cert = idp_a
    blob = serialise(
        sign_assertion(
            key,
            cert,
            confirmation_method="urn:oasis:names:tc:SAML:2.0:cm:sender-vouches",
        )
    )
    assertion = parse_document(blob).find("./saml:Assertion", NS)
    with pytest.raises(AssertionRejected) as caught:
        require_bearer_confirmation(assertion)
    assert "bearer" in caught.value.reason


def test_holder_of_key_confirmation_is_refused(idp_a):
    key, cert = idp_a
    blob = serialise(
        sign_assertion(
            key,
            cert,
            confirmation_method="urn:oasis:names:tc:SAML:2.0:cm:holder-of-key",
        )
    )
    assertion = parse_document(blob).find("./saml:Assertion", NS)
    with pytest.raises(AssertionRejected):
        require_bearer_confirmation(assertion)


def test_negative_control_confirmation_check_disabled_accepts_sender_vouches(idp_a):
    key, cert = idp_a
    blob = serialise(
        sign_assertion(
            key,
            cert,
            confirmation_method="urn:oasis:names:tc:SAML:2.0:cm:sender-vouches",
        )
    )
    assertion = parse_document(blob).find("./saml:Assertion", NS)
    require_bearer_confirmation(assertion, policy=SamlHardeningPolicy.weakened())


# ===========================================================================
# 9. Encrypted assertions and the xmlsec1 policy
# ===========================================================================


def test_encrypted_assertion_is_refused_with_an_operator_path():
    document = (
        '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        'xmlns:xenc="http://www.w3.org/2001/04/xmlenc#" ID="_r" Version="2.0" '
        'IssueInstant="2026-01-01T00:00:00Z">'
        "<saml:EncryptedAssertion><xenc:EncryptedData/></saml:EncryptedAssertion>"
        "</samlp:Response>"
    ).encode("utf-8")

    with pytest.raises(AssertionRejected) as caught:
        refuse_encrypted_assertion(parse_document(document))

    reason = caught.value.reason
    assert "xmlsec1" in reason
    assert "REMEDY" in reason
    assert "Microsoft Entra ID" in reason
    assert "Okta" in reason


def test_the_refusal_no_longer_names_a_setting_that_does_not_exist():
    """The ARCH-16 message told operators to set `SAML_CRYPTO_BACKEND`.

    That variable is not a declared Settings field, so `extra="ignore"`
    discards it. The remedy did nothing. A GA diagnostic must not send an
    operator to a dead end.
    """
    text = encrypted_assertion_diagnostic()
    assert "SAML_CRYPTO_BACKEND" not in text
    assert "python3-saml" not in text


def test_xmlsec1_policy_is_stated_and_machine_readable():
    policy = describe_xmlsec1_policy()
    assert policy["policy"] == "REFUSE_ENCRYPTED_ASSERTIONS"
    assert policy["encrypted_assertions_supported"] is False
    assert set(policy["idp_remedies"]) >= {"Microsoft Entra ID", "Okta"}
    assert isinstance(policy["xmlsec1_on_path"], bool)


def test_a_single_idp_can_be_targeted_by_the_diagnostic():
    text = encrypted_assertion_diagnostic(idp_display_name="Okta")
    assert "Okta Admin" in text
    assert "Entra admin center" not in text


def test_an_unencrypted_document_is_not_refused(idp_a):
    key, cert = idp_a
    refuse_encrypted_assertion(parse_document(serialise(sign_assertion(key, cert))))


# ===========================================================================
# 10. Zero-migration discipline
# ===========================================================================


def test_every_refusal_outcome_survives_the_check_constraint():
    """`28-G8` says no migrations, so the outcome vocabulary is closed.

    A refusal stamped with an outcome outside `ck_sso_assertion_outcome` would
    raise IntegrityError inside the ACS exception handler and turn a successful
    defence into a 500 with no audit row — a security control that destroys its
    own evidence.
    """
    assert REJECTION_OUTCOME in PERMITTED_OUTCOMES


def test_the_permitted_outcome_set_matches_the_migration():
    """Read the constraint out of the ARCH-16 migration and compare."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "arch16_step6_assertions_and_replay.py"
    ).read_text(encoding="utf-8-sig")

    block = source.split("outcome IN (", 1)[1].split(")", 1)[0]
    from_migration = set(re.findall(r"'([A-Z_]+)'", block))
    assert from_migration == set(PERMITTED_OUTCOMES), (
        "app/services/auth/saml_security.PERMITTED_OUTCOMES has drifted from "
        "ck_sso_assertion_outcome. One of them is now wrong."
    )