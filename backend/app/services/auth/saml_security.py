"""ARCH-28 — SAML 2.0 assertion hardening and XML Signature Wrapping defence.

    from app.services.auth import saml_security

WHY THIS MODULE EXISTS SEPARATELY FROM `saml_gateway`
=====================================================

`app/services/identity/saml_gateway.py` is the ARCH-16 protocol implementation:
it parses, it verifies, it maps attributes. This module is the *adversarial*
layer that runs alongside it. Keeping them apart is deliberate — the rig in
`tests/security/test_saml_xsw_rig.py` needs to construct a DELIBERATELY WEAKENED
validator and observe the attacks succeed against it, and that is only possible
if the hardening is an object that can be switched off rather than a set of
`if` statements welded into the parser.

That is the ARCH-28 hard invariant `28-G3`: an XSW test that passes against
broken code certifies the break. The policy object below is what makes the
negative control expressible.

WHAT SIGNXML 5.1.0 ACTUALLY DEFENDS, MEASURED
==============================================

Verified empirically against `signxml==5.1.0` during the ARCH-28 audit, using
real 2048-bit RSA keys and real x509 certificates. Measured, not assumed — the
first draft of this table was wrong in three places:

    XSW-1  original moved into ds:Object, forged in its place     REJECTED
    XSW-2  forged assertion as preceding sibling                  *ACCEPTED*
    XSW-3  forged assertion wrapping the original                 *ACCEPTED*
    XSW-4  forged assertion nested inside the original            REJECTED
    XSW-5  forged at top level, original in a detached ds:Object  REJECTED
    XSW-6  original hidden in the forged assertion's saml:Advice  *ACCEPTED*
    XSW-7  forged assertion added to the original's saml:Advice   REJECTED
    XSW-8  forged assertion reusing the original's ID             REJECTED
    signature stripping                                           REJECTED
    unsigned assertion injected alongside the signed one          *ACCEPTED*

XSW-4 and XSW-7 are refused because both mutate the original assertion's own
subtree and so break its digest. XSW-8 is refused because signxml will not
resolve an ambiguous ID. Four mutations get through: XSW-2, XSW-3, XSW-6 and
plain injection, all of which leave the signed subtree byte-identical and add
material elsewhere in the document.

"ACCEPTED" here does not mean signxml returned the forged assertion — it does
not. `XMLVerifier.verify()` returns `signed_xml`, the subtree the Reference
actually covered, so the ORIGINAL assertion comes back and the forgery is
ignored. Three consequences follow, and they are why this module exists:

1.  THE DOCUMENT IS STILL ATTACKER-SHAPED. `_record_assertion` stores the raw
    bytes in `sso_assertions.raw_payload`. Every downstream reader — the SIEM,
    the compliance export, a human during an incident — sees a document
    containing an assertion the SP never consumed.

2.  ANY READ FROM THE ENVELOPE IS A READ FROM ATTACKER-CONTROLLED DATA, and
    `verify_response` performs exactly such a read today:

        if in_response_to is None:
            in_response_to = envelope.get("InResponseTo")

    The signed assertion is therefore NOT bound to the AuthnRequest this SP
    issued whenever `SubjectConfirmationData/@InResponseTo` is absent. An
    unsolicited assertion can satisfy the solicited-flow check by carrying a
    `Response/@InResponseTo` the attacker chose. `require_signed_request_binding`
    closes that.

3.  THE SAFETY IS ONE REFACTOR DEEP. It holds only because the current code
    happens to read from `verified_root`. Nothing structural prevents the next
    change from reading from `envelope`. Rejecting the wrapped document outright
    removes the dependence on that habit.

THE OUTCOME VOCABULARY IS CLOSED — AND THAT IS A ZERO-MIGRATION CONSTRAINT
==========================================================================

`arch16_step6_assertions_and_replay` puts a CHECK constraint on
`sso_assertions.outcome` enumerating eleven literal values. ARCH-28 ships NO
migrations (`28-G8`), so there is no `REJECTED_XSW`. Inventing one would make
every refusal raise `IntegrityError` inside the ACS exception handler and turn
a successful defence into a 500 with no audit row — a security control that
destroys its own evidence.

So every structural refusal is stamped `REJECTED_SIGNATURE`, which is also the
honest classification: the signature does not cover the document as presented.
The specific mutation is named in `reject_reason`, which is free text, and in
the structured log line. `REJECTION_OUTCOME` below is the single place that
decision lives, and `verify_arch28.py` check `G2` asserts it stays inside the
constrained set.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

from app.services.identity.errors import AssertionRejected

logger = logging.getLogger("app.services.auth.saml_security")

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "xenc": "http://www.w3.org/2001/04/xmlenc#",
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
}

QN_ASSERTION = f"{{{NS['saml']}}}Assertion"
QN_ADVICE = f"{{{NS['saml']}}}Advice"
QN_SIGNATURE = f"{{{NS['ds']}}}Signature"
QN_OBJECT = f"{{{NS['ds']}}}Object"
QN_REFERENCE = f"{{{NS['ds']}}}Reference"
QN_ENCRYPTED_ASSERTION = f"{{{NS['saml']}}}EncryptedAssertion"
QN_ENCRYPTED_DATA = f"{{{NS['xenc']}}}EncryptedData"

#: The only value a structural refusal may carry.
#:
#: Constrained by `ck_sso_assertion_outcome` in
#: `alembic/versions/arch16_step6_assertions_and_replay.py`. ARCH-28 adds no
#: migrations, so this set is closed. Read the module docstring before adding
#: anything here.
REJECTION_OUTCOME: str = "REJECTED_SIGNATURE"

#: Every value the CHECK constraint permits, mirrored so the gate can assert
#: that `REJECTION_OUTCOME` is a member without reaching into the migration.
PERMITTED_OUTCOMES: frozenset[str] = frozenset(
    {
        "ACCEPTED",
        "REJECTED_SIGNATURE",
        "REJECTED_AUDIENCE",
        "REJECTED_DESTINATION",
        "REJECTED_EXPIRED",
        "REJECTED_REPLAY",
        "REJECTED_NO_AUTHN_INSTANT",
        "REJECTED_UNSOLICITED",
        "REJECTED_DOMAIN",
        "REJECTED_SEAT_CAP",
        "REJECTED_UNKNOWN",
    }
)

BEARER_METHOD = "urn:oasis:names:tc:SAML:2.0:cm:bearer"


# ===========================================================================
# Policy
# ===========================================================================


@dataclass(frozen=True)
class SamlHardeningPolicy:
    """Which ARCH-28 defences are active.

    Every flag defaults to True. The ONLY supported reason to construct one
    with a flag set False is `SamlHardeningPolicy.weakened()` inside the XSW
    rig, which exists to prove the attacks land when the defence is removed.
    Production code paths call `default()` and never take a policy argument
    from a request.
    """

    reject_multiple_assertions: bool = True
    reject_multiple_signatures: bool = True
    reject_signature_objects: bool = True
    reject_duplicate_ids: bool = True
    reject_nested_assertions: bool = True
    require_reference_matches_assertion: bool = True
    require_bearer_confirmation: bool = True
    require_issuer_binding: bool = True
    require_certificate_validity: bool = True
    require_signed_request_binding: bool = True

    @classmethod
    def default(cls) -> "SamlHardeningPolicy":
        return cls()

    @classmethod
    def weakened(cls) -> "SamlHardeningPolicy":
        """The negative control for `28-G3`. Never reachable from the app.

        `verify_arch28.py` check G4 asserts this constructor has no call site
        outside `tests/`. A weakened policy that production can build is not a
        test fixture, it is a backdoor.
        """
        return cls(
            reject_multiple_assertions=False,
            reject_multiple_signatures=False,
            reject_signature_objects=False,
            reject_duplicate_ids=False,
            reject_nested_assertions=False,
            require_reference_matches_assertion=False,
            require_bearer_confirmation=False,
            require_issuer_binding=False,
            require_certificate_validity=False,
            require_signed_request_binding=False,
        )

    @property
    def is_weakened(self) -> bool:
        return not all(
            (
                self.reject_multiple_assertions,
                self.reject_multiple_signatures,
                self.reject_signature_objects,
                self.reject_duplicate_ids,
                self.reject_nested_assertions,
                self.require_reference_matches_assertion,
                self.require_bearer_confirmation,
                self.require_issuer_binding,
                self.require_certificate_validity,
                self.require_signed_request_binding,
            )
        )


@dataclass(frozen=True)
class StructuralFinding:
    """One structural anomaly, named for the log line and the reject reason."""

    code: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"{self.code}: {self.detail}"


# ===========================================================================
# Parsing
# ===========================================================================


def parse_document(xml_bytes: bytes):
    """Parse with defusedxml, refusing rather than falling back.

    Mirrors `saml_gateway._parse`. Duplicated rather than imported so this
    module stays importable in a security test run that does not want the
    identity package's transitive imports.
    """
    try:
        from defusedxml.ElementTree import fromstring
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise AssertionRejected(
            "REJECTED_UNKNOWN",
            "defusedxml is not installed. The ACS endpoint must not parse XML "
            "without it.",
        ) from exc
    try:
        return fromstring(xml_bytes)
    except Exception as exc:
        raise AssertionRejected(
            REJECTION_OUTCOME, f"malformed XML: {exc}"
        ) from exc


def _iter_all(root) -> Iterable[Any]:
    """Every element including the root, each exactly once.

    `ElementTree.iter()` already yields the root. Yielding it separately first
    counts the root twice, which made the duplicate-ID check fire on the
    Response element of every legitimate document — a false positive that
    refuses all logins. Caught by running the rig, not by reading it.
    """
    return root.iter()


def _tags(root, qname: str) -> list[Any]:
    return [node for node in root.iter(qname)]


# ===========================================================================
# Structural analysis — the XSW defence
# ===========================================================================


def analyse_structure(
    root,
    *,
    verified_assertion_id: Optional[str],
    policy: SamlHardeningPolicy,
) -> list[StructuralFinding]:
    """Return every structural anomaly in `root`. Never raises on findings.

    Separated from the raising wrapper so the rig can assert on the exact set
    of codes a mutation produces, rather than on the first one that happened
    to fire.
    """
    findings: list[StructuralFinding] = []

    assertions = _tags(root, QN_ASSERTION)
    signatures = _tags(root, QN_SIGNATURE)
    objects = _tags(root, QN_OBJECT)

    # ---- S1: exactly one Assertion in the whole document -------------------
    #
    # XSW-2, XSW-3 and plain unsigned-assertion injection all rely on a second
    # Assertion existing somewhere. A legitimate IdP response carries one.
    # Multi-assertion responses are legal in SAML 2.0 and unused by every IdP
    # this platform integrates (Entra, Okta, Google Workspace), so refusing
    # them costs nothing and removes the whole family.
    if policy.reject_multiple_assertions and len(assertions) != 1:
        findings.append(
            StructuralFinding(
                "XSW_ASSERTION_COUNT",
                f"expected exactly 1 saml:Assertion, found {len(assertions)}",
            )
        )

    # ---- S2: exactly one Signature ----------------------------------------
    if policy.reject_multiple_signatures and len(signatures) > 1:
        findings.append(
            StructuralFinding(
                "XSW_SIGNATURE_COUNT",
                f"expected at most 1 ds:Signature, found {len(signatures)}",
            )
        )

    # ---- S3: no ds:Object ---------------------------------------------------
    #
    # ds:Object is where XSW-1, XSW-5 and XSW-8 park the original signed
    # assertion so the Reference still resolves. SAML assertions have no
    # legitimate use for it.
    if policy.reject_signature_objects and objects:
        findings.append(
            StructuralFinding(
                "XSW_SIGNATURE_OBJECT",
                f"{len(objects)} ds:Object element(s) present; SAML has no use "
                "for one and XSW-1/5/8 require it",
            )
        )

    # ---- S4: no duplicate ID values ---------------------------------------
    #
    # ID-confusion: two elements sharing an ID makes reference resolution
    # implementation-defined, which is the entire premise of several wrapping
    # variants.
    if policy.reject_duplicate_ids:
        seen: dict[str, int] = {}
        for node in _iter_all(root):
            value = node.get("ID")
            if value:
                seen[value] = seen.get(value, 0) + 1
        duplicated = sorted(k for k, v in seen.items() if v > 1)
        if duplicated:
            findings.append(
                StructuralFinding(
                    "XSW_DUPLICATE_ID",
                    f"ID value(s) used more than once: {', '.join(duplicated)}",
                )
            )

    # ---- S5: the Assertion is a direct child of the root -------------------
    #
    # XSW-3 wraps the signed assertion inside a forged one; XSW-6/7 hide it in
    # saml:Advice. Requiring a top-level position collapses both.
    if policy.reject_nested_assertions:
        top_level = [child for child in list(root) if child.tag == QN_ASSERTION]
        if assertions and not top_level:
            findings.append(
                StructuralFinding(
                    "XSW_ASSERTION_NOT_TOP_LEVEL",
                    "no saml:Assertion is a direct child of the response root",
                )
            )
        for advice in _tags(root, QN_ADVICE):
            if _tags(advice, QN_ASSERTION):
                findings.append(
                    StructuralFinding(
                        "XSW_ASSERTION_IN_ADVICE",
                        "saml:Advice contains an Assertion (XSW-6/XSW-7)",
                    )
                )
                break
        for outer in assertions:
            for inner in outer.iter(QN_ASSERTION):
                if inner is not outer:
                    findings.append(
                        StructuralFinding(
                            "XSW_ASSERTION_NESTED",
                            "a saml:Assertion contains another Assertion "
                            "(XSW-3)",
                        )
                    )
                    break

    # ---- S6: the Reference URI names the assertion we verified -------------
    if policy.require_reference_matches_assertion and verified_assertion_id:
        uris = {
            (ref.get("URI") or "").lstrip("#")
            for sig in signatures
            for ref in sig.iter(QN_REFERENCE)
        }
        uris.discard("")
        if uris and verified_assertion_id not in uris:
            findings.append(
                StructuralFinding(
                    "XSW_REFERENCE_MISMATCH",
                    f"signature references {sorted(uris)} but the verified "
                    f"assertion is {verified_assertion_id!r}",
                )
            )
        if policy.reject_multiple_assertions and len(assertions) == 1:
            sole = assertions[0].get("ID")
            if sole and sole != verified_assertion_id:
                findings.append(
                    StructuralFinding(
                        "XSW_CONSUMED_MISMATCH",
                        f"the document's sole assertion is {sole!r} but the "
                        f"signature covered {verified_assertion_id!r}",
                    )
                )

    return findings


def enforce_structural_integrity(
    xml_bytes: bytes,
    *,
    verified_assertion_id: Optional[str],
    policy: Optional[SamlHardeningPolicy] = None,
) -> None:
    """Raise `AssertionRejected` if the document is structurally wrapped.

    Called from `saml_gateway.verify_response` immediately after signature
    verification, with the ID of the assertion signxml actually covered. This
    is the ARCH-28 XSW gate; `verify_arch28.py` check G3 asserts the call site
    exists, because a defence with no caller is the recurring defect class in
    this codebase (invariant I4, the orphaned guard).
    """
    active = policy or SamlHardeningPolicy.default()
    root = parse_document(xml_bytes)
    findings = analyse_structure(
        root, verified_assertion_id=verified_assertion_id, policy=active
    )
    if not findings:
        return

    codes = ",".join(f.code for f in findings)
    detail = "; ".join(str(f) for f in findings)
    logger.warning(
        "ARCH-28 SAML structural refusal: %s | verified_assertion_id=%s",
        detail,
        verified_assertion_id,
    )
    raise AssertionRejected(
        REJECTION_OUTCOME,
        f"XML signature wrapping defence refused the document [{codes}]: {detail}",
    )


# ===========================================================================
# Post-verification bindings
# ===========================================================================


def bind_issuer(
    *,
    verified_issuer: str,
    configured_entity_id: str,
    policy: Optional[SamlHardeningPolicy] = None,
) -> None:
    """The SIGNED issuer must equal the config the ACS selected.

    The ACS handler picks an `EnterpriseIdpConfig` by reading `saml:Issuer`
    from the UNVERIFIED envelope. Nothing downstream compares that choice back
    to the issuer inside the signed assertion. Today the mismatch is caught
    incidentally, because the wrong config carries the wrong certificate and
    the signature fails — but that is a property of the certificate inventory,
    not of the code. Two organizations sharing an IdP certificate (a shared
    Entra tenant, or a certificate copied during a migration) makes the
    incidental defence disappear and cross-tenant assertion acceptance
    immediate. This makes it explicit.
    """
    active = policy or SamlHardeningPolicy.default()
    if not active.require_issuer_binding:
        return
    left = (verified_issuer or "").strip()
    right = (configured_entity_id or "").strip()
    if not left:
        raise AssertionRejected(
            REJECTION_OUTCOME,
            "the signed assertion carries no Issuer; it cannot be bound to an "
            "IdP configuration",
        )
    if left != right:
        raise AssertionRejected(
            REJECTION_OUTCOME,
            f"signed assertion issuer {left!r} does not match the selected IdP "
            f"configuration {right!r} (cross-tenant assertion swap)",
        )


def require_bearer_confirmation(
    assertion,
    *,
    policy: Optional[SamlHardeningPolicy] = None,
) -> None:
    """`SubjectConfirmation/@Method` must be bearer.

    ARCH-16 reads `SubjectConfirmationData` for Recipient and InResponseTo but
    never checks the Method. holder-of-key and sender-vouches assertions carry
    entirely different security assumptions — sender-vouches in particular
    asserts that some *other* party vouches for the subject — and accepting one
    as if it were bearer is a confusion this SP has no way to resolve.
    """
    active = policy or SamlHardeningPolicy.default()
    if not active.require_bearer_confirmation:
        return
    confirmations = assertion.findall("./saml:Subject/saml:SubjectConfirmation", NS)
    if not confirmations:
        raise AssertionRejected(
            REJECTION_OUTCOME, "assertion carries no SubjectConfirmation"
        )
    methods = {(node.get("Method") or "").strip() for node in confirmations}
    if BEARER_METHOD not in methods:
        raise AssertionRejected(
            REJECTION_OUTCOME,
            f"no bearer SubjectConfirmation; methods offered: {sorted(methods)}",
        )


def require_signed_request_binding(
    assertion,
    *,
    expected_in_response_to: Optional[str],
    policy: Optional[SamlHardeningPolicy] = None,
) -> Optional[str]:
    """Resolve InResponseTo from SIGNED data only, and return it.

    `saml_gateway.verify_response` currently falls back to
    `envelope.get("InResponseTo")` when `SubjectConfirmationData` omits it.
    The envelope is outside the signature. An attacker replaying an
    IdP-initiated assertion at an SP that forbids unsolicited SSO can therefore
    satisfy the solicited-flow check by stamping a `Response/@InResponseTo`
    they chose — the assertion is never actually bound to the AuthnRequest this
    SP issued.

    This returns the value from inside the signed subtree, or None. The caller
    must not substitute an envelope value for a None.
    """
    active = policy or SamlHardeningPolicy.default()
    node = assertion.find(
        "./saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData", NS
    )
    signed_value = node.get("InResponseTo") if node is not None else None

    if not active.require_signed_request_binding:
        return signed_value

    if expected_in_response_to is not None and signed_value != expected_in_response_to:
        raise AssertionRejected(
            "REJECTED_UNSOLICITED",
            "the signed assertion is not bound to the AuthnRequest this "
            "service provider issued; InResponseTo must appear inside "
            "SubjectConfirmationData, not on the unsigned Response element",
        )
    return signed_value


# ===========================================================================
# Certificate validity
# ===========================================================================


def certificate_window(pem: str) -> tuple[datetime, datetime, str]:
    """Return `(not_before, not_after, subject)` for a PEM certificate."""
    from cryptography import x509

    cert = x509.load_pem_x509_certificate(pem.strip().encode("utf-8"))
    try:
        not_before = cert.not_valid_before_utc
        not_after = cert.not_valid_after_utc
    except AttributeError:  # pragma: no cover - cryptography < 42
        not_before = cert.not_valid_before.replace(tzinfo=timezone.utc)
        not_after = cert.not_valid_after.replace(tzinfo=timezone.utc)
    return not_before, not_after, cert.subject.rfc4514_string()


def verify_certificate_validity(
    certificates: Sequence[str],
    *,
    now: Optional[datetime] = None,
    skew: timedelta = timedelta(minutes=5),
    policy: Optional[SamlHardeningPolicy] = None,
) -> list[str]:
    """Return the subset of `certificates` inside their validity window.

    A correction to an earlier draft of this module, which claimed signxml
    ignores the validity window. Measured: signxml 5.1.0 DOES enforce it and
    raises `InvalidCertificate` with the expiry date. So this is not a missing
    control.

    What it is, is a legible one. `SignXmlBackend.verify` catches every
    exception from every configured certificate and re-raises a single
    `REJECTED_SIGNATURE: no configured certificate verified the signature`.
    An expired IdP certificate therefore reaches the operator as "signature
    failed" — indistinguishable from a genuine tampering attempt, at the exact
    moment when telling those two apart is the whole job. Running this first
    turns a 3am forensic exercise into a dated sentence.

    `certificate_expiry_report` is the same logic without the raise, and its
    `EXPIRING_SOON` band is what stops the outage happening at all.
    """
    active = policy or SamlHardeningPolicy.default()
    if not active.require_certificate_validity:
        return list(certificates)

    moment = now or datetime.now(timezone.utc)
    usable: list[str] = []
    reasons: list[str] = []
    for pem in certificates:
        try:
            not_before, not_after, subject = certificate_window(pem)
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"unparseable certificate: {exc}")
            continue
        if moment + skew < not_before:
            reasons.append(f"{subject} is not valid until {not_before.isoformat()}")
            continue
        if moment - skew >= not_after:
            reasons.append(f"{subject} expired at {not_after.isoformat()}")
            continue
        usable.append(pem)

    if not usable:
        raise AssertionRejected(
            REJECTION_OUTCOME,
            "no IdP signing certificate is currently within its validity "
            "window: " + ("; ".join(reasons) or "none configured"),
        )
    if reasons:
        logger.warning(
            "ARCH-28 SAML: %d IdP certificate(s) skipped as out of window: %s",
            len(reasons),
            "; ".join(reasons),
        )
    return usable


def certificate_expiry_report(certificates: Sequence[str],
                              *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Non-raising inventory, for the SOC 2 evidence pack and operator views."""
    moment = now or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for pem in certificates:
        try:
            not_before, not_after, subject = certificate_window(pem)
        except Exception as exc:  # noqa: BLE001
            rows.append({"subject": None, "error": str(exc), "status": "UNPARSEABLE"})
            continue
        if moment < not_before:
            status = "NOT_YET_VALID"
        elif moment >= not_after:
            status = "EXPIRED"
        elif moment + timedelta(days=30) >= not_after:
            status = "EXPIRING_SOON"
        else:
            status = "VALID"
        rows.append(
            {
                "subject": subject,
                "not_before": not_before.isoformat(),
                "not_after": not_after.isoformat(),
                "days_remaining": (not_after - moment).days,
                "status": status,
            }
        )
    return rows


# ===========================================================================
# xmlsec1 policy and operator diagnostics
# ===========================================================================

#: ARCH-28 tranche 3. The decision, recorded here rather than in a wiki.
#:
#: FlowPilot AI does NOT ship `xmlsec1` in the runtime image and does NOT
#: support encrypted SAML assertions. The reasons, in order of weight:
#:
#:   1. `xmlsec1` is a C library reached through `python3-saml`/`pyXMLSec`. It
#:      adds a native attack surface to the one endpoint that processes
#:      unauthenticated attacker-controlled XML. Every phase of this platform
#:      has traded capability for a smaller surface on that endpoint.
#:   2. The assertion already travels inside TLS to an ACS URL the IdP
#:      validates. Assertion encryption protects against a compromised browser
#:      relaying the POST — real, but narrower than the threat the dependency
#:      introduces.
#:   3. Every IdP this platform supports can be configured to sign without
#:      encrypting, in a documented and reversible way. The remedy costs an
#:      operator ten minutes; carrying xmlsec1 costs the platform forever.
#:
#: What ARCH-28 changes is NOT the answer. ARCH-16 already refused. It changes
#: the refusal from a dead end into an operator path: the previous message told
#: operators to set `SAML_CRYPTO_BACKEND=python3-saml`, an environment variable
#: that is not a declared Settings field and is therefore discarded by
#: `extra="ignore"` — a remedy that does nothing, on a setting that does not
#: exist, for a backend that is not installed.
XMLSEC1_POLICY: str = "REFUSE_ENCRYPTED_ASSERTIONS"

_IDP_REMEDIES: dict[str, str] = {
    "Microsoft Entra ID": (
        "Entra admin center > Enterprise applications > FlowPilot AI > "
        "Single sign-on > SAML Certificates > Edit > set 'Assertion "
        "encryption' to Disabled, then Save. Signing option must remain "
        "'Sign SAML assertion' or 'Sign SAML response and assertion'."
    ),
    "Okta": (
        "Okta Admin > Applications > FlowPilot AI > General > SAML Settings > "
        "Edit > Show Advanced Settings > set 'Assertion Encryption' to "
        "Unencrypted. 'Response' and 'Assertion Signature' stay Signed."
    ),
    "Google Workspace": (
        "Admin console > Apps > Web and mobile apps > FlowPilot AI > "
        "SAML attribute mapping. Google Workspace does not encrypt assertions "
        "by default; if a third-party proxy is re-encrypting them, remove it "
        "from the SSO path."
    ),
}


def xmlsec1_available() -> bool:
    """True when the `xmlsec1` binary is on PATH. Diagnostic only.

    Never used to enable encrypted assertions — `XMLSEC1_POLICY` is
    unconditional. Its presence on a host is reported so an operator who
    installed it understands that installing it changed nothing.
    """
    return shutil.which("xmlsec1") is not None


def describe_xmlsec1_policy() -> dict[str, Any]:
    """Machine-readable policy statement for the evidence pack and the gate."""
    return {
        "policy": XMLSEC1_POLICY,
        "encrypted_assertions_supported": False,
        "xmlsec1_on_path": xmlsec1_available(),
        "binary_required_for_support": "xmlsec1",
        "rationale": (
            "Encrypted assertions require a native XML security library on the "
            "one endpoint that parses unauthenticated attacker-controlled XML. "
            "FlowPilot AI refuses them and documents the IdP-side remedy."
        ),
        "idp_remedies": dict(_IDP_REMEDIES),
    }


def encrypted_assertion_diagnostic(*, idp_display_name: Optional[str] = None) -> str:
    """The operator-facing text for an encrypted assertion refusal.

    Named remedy, named product, named screen. The ARCH-16 message named an
    environment variable that does not exist.
    """
    lines = [
        "FlowPilot AI received an encrypted SAML assertion and refused it.",
        "",
        "WHY: encrypted assertions require the xmlsec1 native library, which "
        "FlowPilot AI deliberately does not ship. This is a fixed platform "
        "policy, not a configuration gap — no environment variable, feature "
        "flag or plan tier enables it, and installing xmlsec1 on the host "
        "will not change this behaviour.",
        "",
        "REMEDY: reconfigure the identity provider to send a SIGNED but "
        "UNENCRYPTED assertion. Signing stays mandatory; only encryption is "
        "removed. The assertion still travels inside TLS to the ACS URL.",
        "",
    ]
    if idp_display_name and idp_display_name in _IDP_REMEDIES:
        lines.append(f"  {idp_display_name}: {_IDP_REMEDIES[idp_display_name]}")
    else:
        for name, remedy in _IDP_REMEDIES.items():
            lines.append(f"  {name}: {remedy}")
    lines += [
        "",
        "AFTER THE CHANGE: re-run SSO. A successful assertion is recorded in "
        "sso_assertions with outcome ACCEPTED. If the refusal persists, an "
        "intermediary proxy is re-encrypting the assertion after the IdP "
        "emits it.",
    ]
    return "\n".join(lines)


def refuse_encrypted_assertion(root, *, idp_display_name: Optional[str] = None) -> None:
    """Raise the diagnostic refusal if the document carries ciphertext.

    Replaces the ARCH-16 refusal, which pointed at a nonexistent setting.
    """
    encrypted = _tags(root, QN_ENCRYPTED_ASSERTION) or _tags(root, QN_ENCRYPTED_DATA)
    if not encrypted:
        return
    logger.warning(
        "ARCH-28 SAML: encrypted assertion refused (policy=%s, xmlsec1_on_path=%s)",
        XMLSEC1_POLICY,
        xmlsec1_available(),
    )
    raise AssertionRejected(
        "REJECTED_UNKNOWN",
        encrypted_assertion_diagnostic(idp_display_name=idp_display_name),
    )


# ===========================================================================
# Settings
# ===========================================================================


def hardening_policy_from_settings(settings: Any = None) -> SamlHardeningPolicy:
    """Read the policy from Settings, defaulting to fully hardened.

    `SAML_XSW_DEFENCE_ENABLED` is a declared Settings field, added by
    `patch_arch28_wiring.py`. It is declared rather than read through
    `getattr(settings, ...)` for the reason the ARCH-25 config block already
    documents at length: `model_config` sets `extra="ignore"`, so an
    environment variable with no matching field is DISCARDED. ARCH-16 shipped
    `SAML_CLOCK_SKEW_S`, `SAML_RAW_ASSERTION_RETENTION_DAYS` and
    `SAML_CRYPTO_BACKEND` as undeclared getattr lookups, which means none of
    them has ever read configuration on any deployment — they return their
    literal defaults, always. ARCH-28 declares all four.

    The kill switch exists because a GA platform needs one when a real IdP
    turns out to emit something structurally odd at 3am. It defaults to on and
    fails to on: an unreadable setting hardens rather than opens.
    """
    if settings is None:
        try:
            from app.core.config import settings as app_settings

            settings = app_settings
        except Exception:  # noqa: BLE001 - a config failure must not open the gate
            return SamlHardeningPolicy.default()

    enabled = getattr(settings, "SAML_XSW_DEFENCE_ENABLED", True)
    if enabled is False:
        logger.error(
            "ARCH-28: SAML_XSW_DEFENCE_ENABLED is false. XML signature "
            "wrapping defences are DISABLED. This is not a supported "
            "production configuration."
        )
        return SamlHardeningPolicy(
            reject_multiple_assertions=False,
            reject_multiple_signatures=False,
            reject_signature_objects=False,
            reject_duplicate_ids=False,
            reject_nested_assertions=False,
            require_reference_matches_assertion=False,
            require_bearer_confirmation=True,
            require_issuer_binding=True,
            require_certificate_validity=True,
            require_signed_request_binding=True,
        )
    return SamlHardeningPolicy.default()


__all__ = [
    "BEARER_METHOD",
    "PERMITTED_OUTCOMES",
    "REJECTION_OUTCOME",
    "XMLSEC1_POLICY",
    "SamlHardeningPolicy",
    "StructuralFinding",
    "analyse_structure",
    "bind_issuer",
    "certificate_expiry_report",
    "certificate_window",
    "describe_xmlsec1_policy",
    "encrypted_assertion_diagnostic",
    "enforce_structural_integrity",
    "hardening_policy_from_settings",
    "parse_document",
    "refuse_encrypted_assertion",
    "require_bearer_confirmation",
    "require_signed_request_binding",
    "verify_certificate_validity",
    "xmlsec1_available",
]