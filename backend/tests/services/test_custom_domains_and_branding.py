"""ARCH-25 — service-layer tests for custom domains and tenant branding.

WHY THIS FILE LIVES IN tests/services/ AND CONTAINS NO HTTP CALL
================================================================

`tests/services/conftest.py` shadows the root `client` fixture and binds
request sessions to a database other than the one `SessionLocal()` returns.
Anything that goes through the TestClient from this directory reads a
different database than the one the test just wrote to, and the failure looks
like a missing row rather than a fixture problem.

Every HTTP-layer assertion for this phase is therefore in
`tests/api/test_arch25_endpoints.py`. This file talks to services and models
directly, through `db_session`.

WHAT IS STUBBED AND WHAT IS NOT
===============================

`dns_service.lookup_txt` is monkeypatched. Nothing else is. The point of these
tests is the state machine around DNS — which status a domain lands in, whose
failure counter moves, what the database refuses — and a real resolver would
make the suite depend on public DNS and on the network.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.custom_domain import (
    CERT_STATUS_ISSUED,
    CERT_STATUS_NONE,
    CERT_STATUS_PENDING,
    CHALLENGE_LABEL,
    DOMAIN_STATUS_FAILED,
    DOMAIN_STATUS_PENDING,
    DOMAIN_STATUS_REVOKED,
    DOMAIN_STATUS_VERIFIED,
    RESOLVABLE_DOMAIN_STATUSES,
    CustomDomain,
)
from app.models.tenant_branding import (
    SENDER_STATUS_LAPSED,
    SENDER_STATUS_PENDING,
    SENDER_STATUS_UNSET,
    SENDER_STATUS_VERIFIED,
    TenantBranding,
)
from app.schemas.custom_domain import normalise_hostname
from app.schemas.tenant_branding import (
    BrandingManifest,
    TenantBrandingUpdate,
    validate_brand_text,
    validate_hex_color,
)
from app.services.branding import branding_service, domain_service
from app.services.branding.errors import (
    CertificateRefusedError,
    DomainAlreadyClaimedError,
    DomainPolicyError,
    DomainVerificationError,
    ResolverUnavailableError,
)
from app.services.identity.dns_service import TxtLookupResult


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _enable_custom_domains(monkeypatch):
    """Turn the feature on and give the reserved list real entries."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "CUSTOM_DOMAINS_ENABLED", True, raising=False)
    monkeypatch.setattr(
        settings,
        "PLATFORM_RESERVED_HOSTS",
        ["flowpilot.ai", "app.flowpilot.ai"],
        raising=False,
    )
    monkeypatch.setattr(settings, "ACME_AGENT_URL", "http://acme-agent:2019", raising=False)


def _stub_txt(monkeypatch, result: TxtLookupResult) -> list[str]:
    """Replace the resolver and record the names it was asked for."""
    asked: list[str] = []

    def _fake(domain: str, *, subdomain: str | None = None) -> TxtLookupResult:
        target = domain if subdomain is None else f"{subdomain}.{domain}"
        asked.append(target)
        if hasattr(result, "domain"):
            result.domain = target
        return result

    monkeypatch.setattr(domain_service.dns_service, "lookup_txt", _fake)
    monkeypatch.setattr(branding_service.dns_service, "lookup_txt", _fake)
    return asked


def _claim(db: Session, org_id: uuid.UUID, hostname: str = "ai.acme.com") -> CustomDomain:
    domain = domain_service.claim_domain(
        db, organization_id=org_id, hostname=hostname
    )
    db.commit()
    return domain


# ---------------------------------------------------------------------------
# Hostname normalisation and policy
# ---------------------------------------------------------------------------


@pytest.mark.no_db
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ai.acme.com", "ai.acme.com"),
        ("  AI.Acme.Com  ", "ai.acme.com"),
        ("https://ai.acme.com/login?next=/x", "ai.acme.com"),
        ("ai.acme.com.", "ai.acme.com"),
        ("http://user:pw@ai.acme.com/", "ai.acme.com"),
    ],
)
def test_normalise_hostname_accepts_what_people_paste(raw, expected):
    assert normalise_hostname(raw) == expected


@pytest.mark.no_db
@pytest.mark.parametrize(
    "raw",
    [
        "",
        "acme",  # single label
        "ai.acme.com:8443",  # port refused, not stripped
        "*.acme.com",  # wildcard
        "10.0.0.5",  # IPv4 literal
        "ai_acme.com",  # underscore
        "-bad.acme.com",  # leading hyphen
        "ai.acme.com\n",  # trailing newline: the \Z anchor case
        "a" * 300 + ".com",
    ],
)
def test_normalise_hostname_refuses_hostile_input(raw):
    with pytest.raises(ValueError):
        normalise_hostname(raw)


@pytest.mark.no_db
def test_reserved_hostnames_and_their_subdomains_are_refused(_enable_custom_domains):
    for hostname in ("flowpilot.ai", "app.flowpilot.ai", "login.flowpilot.ai"):
        with pytest.raises(DomainPolicyError):
            domain_service.assert_claimable_hostname(hostname)


@pytest.mark.no_db
def test_reserved_match_is_on_label_boundaries(_enable_custom_domains):
    domain_service.assert_claimable_hostname("notflowpilot.ai")


@pytest.mark.no_db
def test_empty_reserved_list_refuses_every_claim(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PLATFORM_RESERVED_HOSTS", [], raising=False)
    with pytest.raises(DomainPolicyError) as exc:
        domain_service.assert_claimable_hostname("ai.acme.com")
    assert "PLATFORM_RESERVED_HOSTS" in str(exc.value)


# ---------------------------------------------------------------------------
# Challenge tokens
# ---------------------------------------------------------------------------


@pytest.mark.no_db
def test_challenge_tokens_are_unguessable_and_unique():
    tokens = {domain_service.issue_challenge_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(token) >= 22 for token in tokens)


@pytest.mark.no_db
def test_challenge_record_name_is_built_in_one_place():
    assert (
        domain_service.challenge_record_name("ai.acme.com")
        == f"{CHALLENGE_LABEL}.ai.acme.com"
    )


def test_claim_sets_pending_with_a_bounded_challenge_window(db_session, tenant):
    domain = _claim(db_session, tenant.organization.id)

    assert domain.status == DOMAIN_STATUS_PENDING
    assert domain.verified_at is None
    assert domain.certificate_status == CERT_STATUS_NONE
    assert domain.challenge_expires_at > domain.challenge_issued_at
    assert domain.consecutive_failures == 0


def test_reissue_mints_a_new_token_without_demoting_a_verified_domain(
    db_session, tenant, monkeypatch
):
    domain = _claim(db_session, tenant.organization.id)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain=domain.hostname, records=[domain.challenge_token], resolved=True),
    )
    domain_service.verify_domain(db_session, domain=domain)
    db_session.commit()
    assert domain.status == DOMAIN_STATUS_VERIFIED

    original = domain.challenge_token
    domain_service.reissue_challenge(db_session, domain=domain)
    db_session.commit()

    assert domain.challenge_token != original
    assert domain.status == DOMAIN_STATUS_VERIFIED


# ---------------------------------------------------------------------------
# Global uniqueness
# ---------------------------------------------------------------------------


def test_two_tenants_cannot_hold_one_hostname(db_session, tenant):
    _claim(db_session, tenant.organization.id, "ai.acme.com")

    other_org_id = tenant.foreign_workspace.organization_id
    with pytest.raises(DomainAlreadyClaimedError):
        domain_service.claim_domain(
            db_session, organization_id=other_org_id, hostname="ai.acme.com"
        )


def test_the_unique_index_is_global_not_per_tenant(db_session, tenant):
    _claim(db_session, tenant.organization.id, "ai.acme.com")
    other_org_id = tenant.foreign_workspace.organization_id

    db_session.add(
        CustomDomain(
            organization_id=other_org_id,
            hostname="ai.acme.com",
            status=DOMAIN_STATUS_PENDING,
            challenge_token="x" * 32,
            challenge_issued_at=utcnow(),
            challenge_expires_at=utcnow() + timedelta(days=1),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# ---------------------------------------------------------------------------
# DNS TXT verification
# ---------------------------------------------------------------------------


def test_matching_txt_record_verifies_the_domain(db_session, tenant, monkeypatch):
    domain = _claim(db_session, tenant.organization.id)
    asked = _stub_txt(
        monkeypatch,
        TxtLookupResult(
            domain=domain.hostname,
            records=["unrelated", domain.challenge_token],
            resolved=True,
        ),
    )

    result = domain_service.verify_domain(db_session, domain=domain)
    db_session.commit()

    assert result.verified is True
    assert result.resolver_failed is False
    assert domain.status == DOMAIN_STATUS_VERIFIED
    assert domain.verified_at is not None
    assert domain.consecutive_failures == 0
    assert asked == [f"{CHALLENGE_LABEL}.ai.acme.com"]


def test_absent_record_raises_and_counts_against_the_tenant(
    db_session, tenant, monkeypatch
):
    domain = _claim(db_session, tenant.organization.id)
    _stub_txt(monkeypatch, TxtLookupResult(domain=domain.hostname, records=[], resolved=True))

    with pytest.raises(DomainVerificationError):
        domain_service.verify_domain(db_session, domain=domain)
    db_session.commit()

    assert domain.status == DOMAIN_STATUS_PENDING
    assert domain.consecutive_failures == 1
    assert domain.last_failure_reason


def test_wrong_token_does_not_verify(db_session, tenant, monkeypatch):
    domain = _claim(db_session, tenant.organization.id)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain=domain.hostname, records=["some-other-token"], resolved=True),
    )

    result = domain_service.verify_domain(
        db_session, domain=domain, raise_on_failure=False
    )
    db_session.commit()

    assert result.verified is False
    assert result.records_seen == 1
    assert domain.status == DOMAIN_STATUS_PENDING


def test_resolver_failure_does_not_count_against_tenant(
    db_session, tenant, monkeypatch
):
    domain = _claim(db_session, tenant.organization.id)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(
            domain=domain.hostname,
            records=[],
            resolved=False,
            error="resolver unavailable: timeout",
        ),
    )

    with pytest.raises(ResolverUnavailableError):
        domain_service.verify_domain(db_session, domain=domain)
    db_session.commit()

    assert domain.consecutive_failures == 0
    assert domain.status == DOMAIN_STATUS_PENDING
    assert domain.last_failure_reason is None

    result = domain_service.verify_domain(
        db_session, domain=domain, raise_on_failure=False
    )
    assert result.resolver_failed is True
    assert result.verified is False


def test_repeated_misses_eventually_fail_the_domain(
    db_session, tenant, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "CUSTOM_DOMAIN_MAX_VERIFY_FAILURES", 3, raising=False
    )
    domain = _claim(db_session, tenant.organization.id)
    _stub_txt(monkeypatch, TxtLookupResult(domain=domain.hostname, records=[], resolved=True))

    for _ in range(3):
        domain_service.verify_domain(
            db_session, domain=domain, raise_on_failure=False
        )
    db_session.commit()

    assert domain.consecutive_failures == 3
    assert domain.status == DOMAIN_STATUS_FAILED


def test_expired_challenge_refuses_before_touching_the_resolver(
    db_session, tenant, monkeypatch
):
    domain = _claim(db_session, tenant.organization.id)
    domain.challenge_issued_at = utcnow() - timedelta(days=30)
    domain.challenge_expires_at = utcnow() - timedelta(days=1)
    db_session.flush()

    asked = _stub_txt(
        monkeypatch,
        TxtLookupResult(domain=domain.hostname, records=[domain.challenge_token], resolved=True),
    )

    with pytest.raises(DomainVerificationError):
        domain_service.verify_domain(db_session, domain=domain)
    db_session.commit()

    assert asked == []


# ---------------------------------------------------------------------------
# Host resolution
# ---------------------------------------------------------------------------


def test_resolve_verified_host_matches_only_the_exact_verified_hostname(
    db_session, tenant, monkeypatch
):
    domain = _claim(db_session, tenant.organization.id, "ai.acme.com")

    assert domain_service.resolve_verified_host(db_session, hostname="ai.acme.com") is None

    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain=domain.hostname, records=[domain.challenge_token], resolved=True),
    )
    domain_service.verify_domain(db_session, domain=domain)
    db_session.commit()

    found = domain_service.resolve_verified_host(db_session, hostname="ai.acme.com")
    assert found is not None
    assert found.organization_id == tenant.organization.id

    assert domain_service.resolve_verified_host(db_session, hostname="AI.Acme.Com.") is not None

    for hostile in (
        "evil-ai.acme.com",
        "ai.acme.com.evil.net",
        "iai.acme.com",
        "ai.acme.co",
        "",
    ):
        assert (
            domain_service.resolve_verified_host(db_session, hostname=hostile) is None
        ), hostile


def test_revoked_domain_stops_resolving_but_keeps_the_claim(
    db_session, tenant, monkeypatch
):
    domain = _claim(db_session, tenant.organization.id)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain=domain.hostname, records=[domain.challenge_token], resolved=True),
    )
    domain_service.verify_domain(db_session, domain=domain)
    domain_service.revoke_domain(db_session, domain=domain)
    db_session.commit()

    assert domain.status == DOMAIN_STATUS_REVOKED
    assert domain.revoked_at is not None
    assert domain.certificate_status == CERT_STATUS_NONE
    assert domain.is_primary is False
    assert domain_service.resolve_verified_host(db_session, hostname="ai.acme.com") is None

    other_org_id = tenant.foreign_workspace.organization_id
    with pytest.raises(DomainAlreadyClaimedError):
        domain_service.claim_domain(
            db_session, organization_id=other_org_id, hostname="ai.acme.com"
        )


@pytest.mark.no_db
def test_only_verified_is_resolvable():
    assert tuple(RESOLVABLE_DOMAIN_STATUSES) == ("VERIFIED",)


# ---------------------------------------------------------------------------
# Certificates — invariant 1
# ---------------------------------------------------------------------------


def test_certificate_is_refused_for_an_unverified_domain(db_session, tenant):
    domain = _claim(db_session, tenant.organization.id)

    with pytest.raises(CertificateRefusedError):
        domain_service.request_certificate(db_session, domain=domain)
    db_session.commit()

    assert domain.certificate_status == CERT_STATUS_NONE


def test_the_database_refuses_a_certificate_without_verification(db_session, tenant):
    domain = _claim(db_session, tenant.organization.id)
    domain.certificate_status = CERT_STATUS_PENDING

    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_certificate_proceeds_once_verified(db_session, tenant, monkeypatch):
    domain = _claim(db_session, tenant.organization.id)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain=domain.hostname, records=[domain.challenge_token], resolved=True),
    )
    domain_service.verify_domain(db_session, domain=domain)
    db_session.commit()

    assert domain.may_request_certificate is True
    domain_service.request_certificate(db_session, domain=domain)
    db_session.commit()
    assert domain.certificate_status == CERT_STATUS_PENDING

    issued = utcnow()
    domain_service.record_certificate_issued(
        db_session,
        domain=domain,
        issued_at=issued,
        expires_at=issued + timedelta(days=90),
        serial="abc123",
    )
    db_session.commit()

    assert domain.certificate_status == CERT_STATUS_ISSUED
    assert domain_service.days_until_expiry(domain) in (89, 90)


def test_renewal_and_dead_man_queries_select_the_right_rows(
    db_session, tenant, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "TLS_RENEWAL_WINDOW_DAYS", 30, raising=False)
    monkeypatch.setattr(settings, "TLS_DEAD_MAN_DAYS", 7, raising=False)

    domain = _claim(db_session, tenant.organization.id)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain=domain.hostname, records=[domain.challenge_token], resolved=True),
    )
    domain_service.verify_domain(db_session, domain=domain)
    domain_service.record_certificate_issued(
        db_session,
        domain=domain,
        issued_at=utcnow() - timedelta(days=80),
        expires_at=utcnow() + timedelta(days=10),
    )
    db_session.commit()

    assert domain.id in {d.id for d in domain_service.certificates_due_for_renewal(db_session)}
    assert domain.id not in {d.id for d in domain_service.dead_man_certificates(db_session)}

    domain.certificate_expires_at = utcnow() + timedelta(days=3)
    db_session.flush()
    assert domain.id in {d.id for d in domain_service.dead_man_certificates(db_session)}


# ---------------------------------------------------------------------------
# Brand tokens — invariant 4
# ---------------------------------------------------------------------------


@pytest.mark.no_db
@pytest.mark.parametrize(
    "value",
    [
        "red",
        "rgb(0,0,0)",
        "hsl(0 0% 0%)",
        "#abc",
        "#aabbccdd",
        "var(--brand)",
        "url(javascript:alert(1))",
        "#aabbcc; background:url(x)",
        "expression(alert(1))",
        "transparent",
    ],
)
def test_colour_validator_refuses_everything_that_is_not_a_hex_token(value):
    with pytest.raises(ValueError):
        validate_hex_color(value)


@pytest.mark.no_db
def test_colour_validator_normalises_a_pasted_uppercase_colour():
    assert validate_hex_color("#1A73E8") == "#1a73e8"
    assert validate_hex_color("  #1a73e8  ") == "#1a73e8"


@pytest.mark.no_db
@pytest.mark.parametrize(
    "value",
    ['<script>alert(1)</script>', 'a"onerror="x', "back\\slash", "<img src=x>"],
)
def test_brand_name_refuses_markup_characters(value):
    with pytest.raises(ValueError):
        validate_brand_text(value)


@pytest.mark.no_db
@pytest.mark.parametrize("value", ["Barnes & Noble", "O'Reilly", "Acme Corp"])
def test_brand_name_permits_real_company_names(value):
    assert validate_brand_text(value) == value


def test_database_refuses_a_colour_the_validator_would_have_caught(db_session, tenant):
    branding = branding_service.get_or_create_branding(
        db_session, organization_id=tenant.organization.id
    )
    branding.primary_color = "red"
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_partial_update_leaves_omitted_fields_alone(db_session, tenant):
    org_id = tenant.organization.id
    branding = branding_service.get_or_create_branding(db_session, organization_id=org_id)

    branding_service.update_branding(
        db_session,
        branding=branding,
        payload=TenantBrandingUpdate(
            brand_name="Acme", primary_color="#1a73e8", accent_color="#7c3aed"
        ),
    )
    db_session.commit()

    branding_service.update_branding(
        db_session,
        branding=branding,
        payload=TenantBrandingUpdate(primary_color="#000000"),
    )
    db_session.commit()

    assert branding.primary_color == "#000000"
    assert branding.accent_color == "#7c3aed"
    assert branding.brand_name == "Acme"

    branding_service.update_branding(
        db_session,
        branding=branding,
        payload=TenantBrandingUpdate(accent_color=None),
    )
    db_session.commit()
    assert branding.accent_color is None


# ---------------------------------------------------------------------------
# Manifest — the unauthenticated surface
# ---------------------------------------------------------------------------


@pytest.mark.no_db
def test_manifest_carries_no_tenant_identifier():
    fields = set(BrandingManifest.model_fields)
    for banned in ("organization_id", "organization_slug", "slug", "plan", "tenant_id"):
        assert banned not in fields


def test_manifest_is_the_platform_default_until_branding_is_enabled(db_session, tenant):
    org_id = tenant.organization.id
    branding = branding_service.get_or_create_branding(db_session, organization_id=org_id)
    branding_service.update_branding(
        db_session,
        branding=branding,
        payload=TenantBrandingUpdate(brand_name="Acme", primary_color="#1a73e8"),
    )
    db_session.commit()

    manifest = branding_service.build_manifest(branding)
    assert manifest.has_custom_branding is False
    assert manifest.brand_name is None

    branding_service.update_branding(
        db_session, branding=branding, payload=TenantBrandingUpdate(is_enabled=True)
    )
    db_session.commit()

    manifest = branding_service.build_manifest(branding)
    assert manifest.has_custom_branding is True
    assert manifest.brand_name == "Acme"
    assert manifest.primary_color == "#1a73e8"


@pytest.mark.no_db
def test_manifest_for_an_unknown_host_is_the_platform_default():
    manifest = branding_service.build_manifest(None)
    assert manifest.has_custom_branding is False
    assert manifest.brand_name is None
    assert manifest.logo_url is None


# ---------------------------------------------------------------------------
# Sender domain — invariant 5
# ---------------------------------------------------------------------------


def _sender_lookup(monkeypatch, *, spf: bool, dkim: bool, resolved: bool = True):
    def _fake(domain: str, *, subdomain: str | None = None) -> TxtLookupResult:
        target = domain if subdomain is None else f"{subdomain}.{domain}"
        if not resolved:
            return TxtLookupResult(domain=target, records=[], resolved=False, error="timeout")
        if "_domainkey" in target:
            return TxtLookupResult(
                domain=target,
                records=["v=DKIM1; k=rsa; p=AAAA"] if dkim else [],
                resolved=True,
            )
        return TxtLookupResult(
            domain=target,
            records=["v=spf1 include:mail.flowpilot.ai ~all"] if spf else [],
            resolved=True,
        )

    monkeypatch.setattr(branding_service.dns_service, "lookup_txt", _fake)


def test_a_new_sender_domain_lands_pending_not_verified(db_session, tenant):
    branding = branding_service.get_or_create_branding(
        db_session, organization_id=tenant.organization.id
    )
    branding_service.set_sender_domain(
        db_session, branding=branding, sender_domain="mail.acme.com"
    )
    db_session.commit()

    assert branding.sender_domain_status == SENDER_STATUS_PENDING
    assert branding.may_send_as_tenant is False


def test_sender_status_is_coherent_with_the_domain(db_session, tenant):
    branding = branding_service.get_or_create_branding(
        db_session, organization_id=tenant.organization.id
    )
    branding.sender_domain = None
    branding.sender_domain_status = SENDER_STATUS_VERIFIED
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_verified_sender_that_stops_resolving_becomes_lapsed_not_unset(
    db_session, tenant, monkeypatch
):
    branding = branding_service.get_or_create_branding(
        db_session, organization_id=tenant.organization.id
    )
    branding_service.set_sender_domain(
        db_session, branding=branding, sender_domain="mail.acme.com"
    )
    db_session.commit()

    _sender_lookup(monkeypatch, spf=True, dkim=True)
    branding_service.verify_sender_domain(db_session, branding=branding)
    db_session.commit()
    assert branding.sender_domain_status == SENDER_STATUS_VERIFIED
    assert branding.may_send_as_tenant is True
    assert branding.sender_degradation_reason is None

    _sender_lookup(monkeypatch, spf=True, dkim=False)
    status = branding_service.verify_sender_domain(
        db_session, branding=branding, raise_on_failure=False
    )
    db_session.commit()

    assert branding.sender_domain_status == SENDER_STATUS_LAPSED
    assert branding.sender_domain_status != SENDER_STATUS_UNSET
    assert branding.may_send_as_tenant is False
    assert status.degradation_reason is not None
    assert "mail.acme.com" in status.degradation_reason


def test_sender_resolver_outage_leaves_a_working_tenant_alone(
    db_session, tenant, monkeypatch
):
    branding = branding_service.get_or_create_branding(
        db_session, organization_id=tenant.organization.id
    )
    branding_service.set_sender_domain(
        db_session, branding=branding, sender_domain="mail.acme.com"
    )
    _sender_lookup(monkeypatch, spf=True, dkim=True)
    branding_service.verify_sender_domain(db_session, branding=branding)
    db_session.commit()

    _sender_lookup(monkeypatch, spf=False, dkim=False, resolved=False)
    branding_service.verify_sender_domain(
        db_session, branding=branding, raise_on_failure=False
    )
    db_session.commit()

    assert branding.sender_domain_status == SENDER_STATUS_VERIFIED


def test_clearing_the_sender_domain_returns_to_unset(db_session, tenant):
    branding = branding_service.get_or_create_branding(
        db_session, organization_id=tenant.organization.id
    )
    branding_service.set_sender_domain(
        db_session, branding=branding, sender_domain="mail.acme.com"
    )
    db_session.commit()
    branding_service.set_sender_domain(
        db_session, branding=branding, sender_domain=None
    )
    db_session.commit()

    assert branding.sender_domain is None
    assert branding.sender_domain_status == SENDER_STATUS_UNSET
    assert branding.sender_degradation_reason is None


# ---------------------------------------------------------------------------
# Primary hostname
# ---------------------------------------------------------------------------


def test_at_most_one_primary_hostname_per_organization(
    db_session, tenant, monkeypatch
):
    org_id = tenant.organization.id
    first = _claim(db_session, org_id, "ai.acme.com")
    second = _claim(db_session, org_id, "app.acme.com")

    def _verify(domain):
        _stub_txt(
            monkeypatch,
            TxtLookupResult(domain=domain.hostname, records=[domain.challenge_token], resolved=True),
        )
        domain_service.verify_domain(db_session, domain=domain)

    _verify(first)
    _verify(second)
    db_session.commit()

    domain_service.set_primary(db_session, domain=first, is_primary=True)
    db_session.commit()
    assert first.is_primary is True

    domain_service.set_primary(db_session, domain=second, is_primary=True)
    db_session.commit()
    db_session.refresh(first)

    assert second.is_primary is True
    assert first.is_primary is False


def test_an_unverified_domain_cannot_be_primary(db_session, tenant):
    domain = _claim(db_session, tenant.organization.id)
    with pytest.raises(DomainVerificationError):
        domain_service.set_primary(db_session, domain=domain, is_primary=True)