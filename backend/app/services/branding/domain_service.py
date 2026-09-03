"""ARCH-25 §1, §2 — custom domain lifecycle, DNS ownership, TLS gating.

WHAT THIS MODULE REUSES RATHER THAN REIMPLEMENTS
================================================

`app.services.identity.dns_service.lookup_txt` resolves the TXT record.
`app.services.identity.domain_service.assert_claimable` refuses public
suffixes and consumer mail domains via the public suffix list.

Both were written for ARCH-16's SSO email domains. The mechanics of proving
control of a DNS zone are identical, and a second implementation would drift:
the day someone hardens the resolver against a rebinding trick in one file,
the other keeps the hole. What ARCH-25 adds on top is the part that is
genuinely different — a GLOBAL uniqueness constraint, a reserved-host refusal,
and a certificate gate.

THE THREE REFUSALS THAT MATTER
==============================

1.  `assert_claimable_hostname` refuses any hostname at or under a
    PLATFORM_RESERVED_HOSTS entry. A tenant who claimed `app.flowpilot.ai`
    would be served the platform's own origin from a page they control, and
    every session cookie scoped to that domain would be delivered to them.
    An EMPTY reserved list refuses every claim rather than allowing all of
    them — an unset allowlist that means "allow everything" is precisely the
    default-open failure this phase exists to avoid.

2.  `claim_domain` lets the database's global unique index decide, and
    translates the IntegrityError. It does NOT pre-check with a SELECT and
    then insert: between the two, another request can claim the name, and the
    check-then-act would report success for a row that was never written.

3.  `request_certificate` refuses unless `status == VERIFIED`. Mirrored by
    `ck_custom_domains_certificate_requires_verification`. A certificate
    issued for an unverified hostname is a certificate issued for someone
    else's hostname.

RESOLVER FAILURE IS NOT VERIFICATION FAILURE
============================================

`verify_domain` raises `ResolverUnavailableError` — 503, our fault — when no
resolver answered, and `DomainVerificationError` — 409, their DNS — when a
resolver answered and the record was absent. Only the second increments
`consecutive_failures` or moves a domain to FAILED.

Collapsing them produces a console that tells a customer to check their DNS
during our own outage, and a failure counter that trips a tenant offline
because our resolver was unreachable for an afternoon.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.custom_domain import (
    CERT_STATUS_FAILED,
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
from app.schemas.custom_domain import (
    DnsChallengeInstructions,
    DomainVerificationResult,
    normalise_hostname,
)
from app.services import audit_service
from app.services.branding.errors import (
    CertificateProvisioningError,
    CertificateRefusedError,
    CustomDomainsDisabledError,
    DomainAlreadyClaimedError,
    DomainLimitExceededError,
    DomainNotFoundError,
    DomainPolicyError,
    DomainVerificationError,
    ResolverUnavailableError,
)
from app.services.identity import dns_service
from app.services.identity.domain_service import assert_claimable
from app.services.identity.errors import DomainPolicyRefused

logger = logging.getLogger("app.services.branding.domain")

#: 32 bytes of urlsafe entropy. The column CHECK requires >= 22 characters,
#: which `token_urlsafe(32)` (43 chars) clears with room to spare. The token
#: is published in public DNS and is therefore not a secret — it is a nonce,
#: and its only job is to be unguessable at the moment of the check.
CHALLENGE_TOKEN_BYTES: int = 32


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def reserved_hosts() -> frozenset[str]:
    """The platform's own hostnames, normalised.

    Normalisation happens here rather than in a Settings validator so that the
    field stays a plain declared list and an operator's stray whitespace or
    capitalisation cannot produce a reserved entry that silently never matches.
    """
    raw = getattr(settings, "PLATFORM_RESERVED_HOSTS", None) or []
    return frozenset(
        str(entry).strip().lower().rstrip(".")
        for entry in raw
        if str(entry).strip()
    )


def _is_reserved(hostname: str) -> bool:
    """True if `hostname` is, or sits beneath, a reserved host.

    Suffix matching, not equality. Reserving `flowpilot.ai` must also reserve
    `login.flowpilot.ai` and `anything.else.flowpilot.ai`, because a subdomain
    of the platform origin inherits cookies scoped to the parent.

    The check is on label boundaries: `notflowpilot.ai` is not beneath
    `flowpilot.ai`, and a naive `endswith` would say it was.
    """
    reserved = reserved_hosts()
    if hostname in reserved:
        return True
    return any(hostname.endswith(f".{entry}") for entry in reserved)


def assert_custom_domains_enabled() -> None:
    if not getattr(settings, "CUSTOM_DOMAINS_ENABLED", False):
        raise CustomDomainsDisabledError(
            "Custom domains are not enabled on this deployment. A verified "
            "domain with no ingress and no certificate would return errors "
            "for the tenant with no way to tell why, so the claim is refused "
            "rather than accepted and left unserved."
        )


def assert_claimable_hostname(hostname: str) -> None:
    """Every policy refusal for a hostname, in one place.

    Shape has already been validated by `normalise_hostname` in the schema.
    This is the layer that needs settings and the public suffix list.
    """
    if not reserved_hosts():
        raise DomainPolicyError(
            "PLATFORM_RESERVED_HOSTS is empty. Custom domain claims are "
            "refused until the platform's own hostnames are listed: without "
            "that list a tenant could claim the platform origin itself and be "
            "served every session cookie scoped to it. This is a deployment "
            "configuration error, not a problem with the hostname you entered."
        )

    if _is_reserved(hostname):
        raise DomainPolicyError(
            f"{hostname} is a FlowPilot platform hostname and cannot be "
            "claimed."
        )

    try:
        assert_claimable(hostname)
    except DomainPolicyRefused as exc:
        # Re-raised rather than propagated so that callers of this module
        # handle exactly one error family. The message is ARCH-16's, which is
        # already written for a tenant rather than an operator.
        raise DomainPolicyError(str(exc)) from exc


def issue_challenge_token() -> str:
    return secrets.token_urlsafe(CHALLENGE_TOKEN_BYTES)


def challenge_record_name(hostname: str) -> str:
    """The one place `CHALLENGE_LABEL` and a hostname are joined."""
    return f"{CHALLENGE_LABEL}.{hostname}"


def _challenge_ttl() -> timedelta:
    hours = int(getattr(settings, "CUSTOM_DOMAIN_CHALLENGE_TTL_HOURS", 168))
    return timedelta(hours=max(1, hours))


def instructions_for(domain: CustomDomain) -> DnsChallengeInstructions:
    return DnsChallengeInstructions.build(
        hostname=domain.hostname,
        token=domain.challenge_token,
        expires_at=domain.challenge_expires_at,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_domain(
    db: Session, *, organization_id: uuid.UUID, domain_id: uuid.UUID
) -> CustomDomain:
    """Fetch one domain, scoped to the tenant.

    The organization filter is in the WHERE clause, not asserted after the
    load. A `db.get()` followed by an ownership check is one early return away
    from leaking another tenant's row, and the two forms look identical in
    review.
    """
    row = db.execute(
        select(CustomDomain).where(
            CustomDomain.id == domain_id,
            CustomDomain.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise DomainNotFoundError("Custom domain not found.")
    return row


def list_domains(
    db: Session, *, organization_id: uuid.UUID
) -> list[CustomDomain]:
    return list(
        db.execute(
            select(CustomDomain)
            .where(CustomDomain.organization_id == organization_id)
            .order_by(
                CustomDomain.is_primary.desc(),
                CustomDomain.created_at.asc(),
            )
        ).scalars()
    )


def resolve_verified_host(
    db: Session, *, hostname: str
) -> Optional[CustomDomain]:
    """THE host resolution query. Exact match, verified only, no fallback.

    Every clause is load-bearing:

      * equality on `hostname`, never LIKE and never a suffix comparison — a
        pattern match would let `evil-acme.com` reach a row for `acme.com`;
      * `status IN RESOLVABLE_DOMAIN_STATUSES`, which is exactly ('VERIFIED',)
        — a PENDING domain is a hostname somebody typed, not one they proved;
      * `scalar_one_or_none`, so an impossible duplicate raises instead of
        silently picking the first row. `uq_custom_domains_hostname` makes
        that unreachable; if it ever fires, refusing loudly beats resolving
        arbitrarily.

    Returns None for an unknown host. The middleware turns None into a refusal.
    There is no branch here that returns a default tenant, and adding one is
    what verify_arch25.py G5 exists to prevent.
    """
    normalised = (hostname or "").strip().lower().rstrip(".")
    if not normalised:
        return None
    return db.execute(
        select(CustomDomain).where(
            CustomDomain.hostname == normalised,
            CustomDomain.status.in_(RESOLVABLE_DOMAIN_STATUSES),
        )
    ).scalar_one_or_none()


def domains_due_for_check(db: Session, *, limit: int = 200) -> list[CustomDomain]:
    """PENDING domains whose next poll is due."""
    interval = timedelta(
        minutes=int(
            getattr(settings, "CUSTOM_DOMAIN_VERIFY_INTERVAL_MINUTES", 30)
        )
    )
    cutoff = utcnow() - interval
    return list(
        db.execute(
            select(CustomDomain)
            .where(
                CustomDomain.status == DOMAIN_STATUS_PENDING,
                CustomDomain.challenge_expires_at > utcnow(),
                (CustomDomain.last_checked_at.is_(None))
                | (CustomDomain.last_checked_at <= cutoff),
            )
            .order_by(CustomDomain.last_checked_at.asc().nullsfirst())
            .limit(limit)
        ).scalars()
    )


def certificates_due_for_renewal(
    db: Session, *, limit: int = 200
) -> list[CustomDomain]:
    window = timedelta(
        days=int(getattr(settings, "TLS_RENEWAL_WINDOW_DAYS", 30))
    )
    return list(
        db.execute(
            select(CustomDomain)
            .where(
                CustomDomain.certificate_status == CERT_STATUS_ISSUED,
                CustomDomain.certificate_expires_at.isnot(None),
                CustomDomain.certificate_expires_at <= utcnow() + window,
            )
            .order_by(CustomDomain.certificate_expires_at.asc())
            .limit(limit)
        ).scalars()
    )


def dead_man_certificates(db: Session) -> list[CustomDomain]:
    """Issued certificates inside the alert threshold.

    Separate from `certificates_due_for_renewal` because the response differs.
    A certificate in the renewal window is retried. A certificate this close
    to expiry that has not renewed despite the retries is an ALERT: expiry on
    a customer's vanity domain is a total outage for that tenant, and the only
    signal is their users' browsers refusing to connect. Nothing in our logs
    says anything at all.
    """
    threshold = timedelta(days=int(getattr(settings, "TLS_DEAD_MAN_DAYS", 7)))
    return list(
        db.execute(
            select(CustomDomain)
            .where(
                CustomDomain.certificate_status == CERT_STATUS_ISSUED,
                CustomDomain.certificate_expires_at.isnot(None),
                CustomDomain.certificate_expires_at <= utcnow() + threshold,
            )
            .order_by(CustomDomain.certificate_expires_at.asc())
        ).scalars()
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def claim_domain(
    db: Session,
    *,
    organization_id: uuid.UUID,
    hostname: str,
    actor_id: Optional[uuid.UUID] = None,
    audit_context: Optional[dict[str, Any]] = None,
) -> CustomDomain:
    """Claim a hostname for a tenant. Always lands PENDING."""
    assert_custom_domains_enabled()

    normalised = normalise_hostname(hostname)
    assert_claimable_hostname(normalised)

    limit = int(getattr(settings, "CUSTOM_DOMAIN_MAX_PER_ORG", 10))
    existing = len(list_domains(db, organization_id=organization_id))
    if existing >= limit:
        raise DomainLimitExceededError(
            f"This organization already holds {existing} custom domains, "
            f"which is the configured maximum of {limit}. Release one before "
            "claiming another."
        )

    now = utcnow()
    row = CustomDomain(
        organization_id=organization_id,
        hostname=normalised,
        status=DOMAIN_STATUS_PENDING,
        challenge_token=issue_challenge_token(),
        challenge_issued_at=now,
        challenge_expires_at=now + _challenge_ttl(),
        created_by_user_id=actor_id,
    )
    db.add(row)

    # Let the global unique index decide. A SELECT-then-INSERT would race:
    # between the two statements another tenant can take the name, and the
    # pre-check would have reported success for a row that never landed.
    try:
        db.flush([row])
    except IntegrityError as exc:
        db.rollback()
        raise DomainAlreadyClaimedError(
            f"{normalised} is already claimed. A hostname resolves to exactly "
            "one organization, so it cannot be held by two. If you believe "
            "this is your domain, contact support."
        ) from exc

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.CUSTOM_DOMAIN,
        resource_id=row.id,
        action=AuditAction.CREATED,
        details={"hostname": normalised},
        **(audit_context or {}),
    )
    logger.info(
        "branding.domain_claimed",
        extra={"organization_id": str(organization_id), "hostname": normalised},
    )
    return row


def reissue_challenge(
    db: Session,
    *,
    domain: CustomDomain,
    actor_id: Optional[uuid.UUID] = None,
    audit_context: Optional[dict[str, Any]] = None,
) -> CustomDomain:
    """Mint a fresh nonce and restart the challenge window.

    A VERIFIED domain is NOT demoted to PENDING by this. Reissuing a challenge
    on a live vanity host would take the tenant offline for as long as it took
    them to notice and republish, and the only reason to reissue on a verified
    domain is a token they lost — a display problem, not a trust problem.
    """
    now = utcnow()
    domain.challenge_token = issue_challenge_token()
    domain.challenge_issued_at = now
    domain.challenge_expires_at = now + _challenge_ttl()
    domain.last_failure_reason = None
    if domain.status in (DOMAIN_STATUS_FAILED, DOMAIN_STATUS_REVOKED):
        domain.status = DOMAIN_STATUS_PENDING
        domain.revoked_at = None
        domain.consecutive_failures = 0
    db.flush([domain])

    audit_service.record(
        db,
        organization_id=domain.organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.CUSTOM_DOMAIN,
        resource_id=domain.id,
        action=AuditAction.UPDATED,
        details={"hostname": domain.hostname, "change": "challenge_reissued"},
        **(audit_context or {}),
    )
    return domain


def verify_domain(
    db: Session,
    *,
    domain: CustomDomain,
    actor_id: Optional[uuid.UUID] = None,
    audit_context: Optional[dict[str, Any]] = None,
    raise_on_failure: bool = True,
) -> DomainVerificationResult:
    """Resolve the challenge TXT record and settle the domain's status.

    `raise_on_failure=False` is for the poller, which processes a batch and
    must not abort it on one tenant's missing record. The endpoint uses the
    default so a person clicking "Verify" gets an error body rather than a
    200 with `verified: false` buried in it.
    """
    now = utcnow()
    record_name = challenge_record_name(domain.hostname)

    if domain.challenge_expires_at <= now:
        detail = (
            "The verification token expired. Generate a new one and republish "
            "the TXT record."
        )
        domain.last_checked_at = now
        domain.last_failure_reason = detail
        db.flush([domain])
        if raise_on_failure:
            raise DomainVerificationError(detail)
        return DomainVerificationResult(
            hostname=domain.hostname,
            verified=False,
            status=domain.status,
            checked_at=now,
            detail=detail,
        )

    # `lookup_txt` takes the base name plus an optional subdomain and joins
    # them itself. Passing the already-joined FQDN as `domain` keeps the
    # joining in `challenge_record_name`, which is the one place that knows
    # the label.
    lookup = dns_service.lookup_txt(record_name)

    if lookup.error and not lookup.resolved:
        # Our failure. No counter moves, no status changes, and the tenant is
        # not told to check their DNS.
        domain.last_checked_at = now
        db.flush([domain])
        logger.warning(
            "branding.resolver_unavailable",
            extra={"hostname": domain.hostname, "error": lookup.error},
        )
        if raise_on_failure:
            raise ResolverUnavailableError(
                "We could not reach a DNS resolver to check this record. That "
                "is on our side, not yours — try again shortly."
            )
        return DomainVerificationResult(
            hostname=domain.hostname,
            verified=False,
            status=domain.status,
            resolver_failed=True,
            checked_at=now,
            detail=lookup.error or "resolver unavailable",
        )

    found = lookup.contains(domain.challenge_token)
    domain.last_checked_at = now

    if not found:
        domain.consecutive_failures = int(domain.consecutive_failures or 0) + 1
        seen = len(lookup.records)
        detail = (
            f"No TXT record matching this challenge was found at "
            f"{record_name}."
            + (
                " DNS changes can take up to 48 hours to propagate."
                if seen == 0
                else f" {seen} TXT record(s) were present, none matching."
            )
        )
        domain.last_failure_reason = detail

        max_failures = int(
            getattr(settings, "CUSTOM_DOMAIN_MAX_VERIFY_FAILURES", 20)
        )
        if (
            domain.status == DOMAIN_STATUS_PENDING
            and domain.consecutive_failures >= max_failures
        ):
            domain.status = DOMAIN_STATUS_FAILED

        db.flush([domain])
        audit_service.record(
            db,
            organization_id=domain.organization_id,
            actor_id=actor_id,
            resource_type=AuditResourceType.CUSTOM_DOMAIN,
            resource_id=domain.id,
            action=AuditAction.UPDATED,
            outcome="DENIED",
            details={
                "hostname": domain.hostname,
                "reason": "challenge_record_absent",
                "records_seen": seen,
            },
            **(audit_context or {}),
        )
        if raise_on_failure:
            raise DomainVerificationError(detail)
        return DomainVerificationResult(
            hostname=domain.hostname,
            verified=False,
            status=domain.status,
            checked_at=now,
            detail=detail,
            records_seen=seen,
        )

    first_time = domain.verified_at is None
    domain.status = DOMAIN_STATUS_VERIFIED
    domain.verified_at = domain.verified_at or now
    domain.consecutive_failures = 0
    domain.last_failure_reason = None
    db.flush([domain])

    audit_service.record(
        db,
        organization_id=domain.organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.CUSTOM_DOMAIN,
        resource_id=domain.id,
        action=AuditAction.DOMAIN_VERIFIED,
        details={"hostname": domain.hostname, "first_time": first_time},
        **(audit_context or {}),
    )
    logger.info(
        "branding.domain_verified",
        extra={"hostname": domain.hostname, "first_time": first_time},
    )
    return DomainVerificationResult(
        hostname=domain.hostname,
        verified=True,
        status=DOMAIN_STATUS_VERIFIED,
        checked_at=now,
        detail="Ownership confirmed.",
        records_seen=len(lookup.records),
    )


def set_primary(
    db: Session,
    *,
    domain: CustomDomain,
    is_primary: bool,
    actor_id: Optional[uuid.UUID] = None,
    audit_context: Optional[dict[str, Any]] = None,
) -> CustomDomain:
    """Designate the hostname the platform builds absolute links with."""
    if is_primary and domain.status != DOMAIN_STATUS_VERIFIED:
        raise DomainVerificationError(
            "Only a verified domain can be the primary hostname. Links built "
            "against an unverified name would not resolve."
        )

    if is_primary:
        # Clear the flag on siblings BEFORE setting it here, in one statement
        # each, inside the caller's transaction. Setting first would violate
        # `uq_custom_domains_org_primary` mid-transaction.
        for sibling in list_domains(db, organization_id=domain.organization_id):
            if sibling.id != domain.id and sibling.is_primary:
                sibling.is_primary = False
        db.flush()

    domain.is_primary = bool(is_primary)
    db.flush([domain])

    audit_service.record(
        db,
        organization_id=domain.organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.CUSTOM_DOMAIN,
        resource_id=domain.id,
        action=AuditAction.UPDATED,
        details={"hostname": domain.hostname, "is_primary": bool(is_primary)},
        **(audit_context or {}),
    )
    return domain


def revoke_domain(
    db: Session,
    *,
    domain: CustomDomain,
    actor_id: Optional[uuid.UUID] = None,
    audit_context: Optional[dict[str, Any]] = None,
) -> CustomDomain:
    """Stop serving a hostname, keeping the claim.

    The row survives. `uq_custom_domains_hostname` is a full unique index, so
    as long as the row exists no other tenant can take the name — which is the
    point. Releasing a hostname is `release_domain`, a separate and explicit
    destructive act.

    Certificate state is reset to NONE and the primary flag cleared, because
    `ck_custom_domains_revoked_is_inert` refuses a revoked row that still
    holds either.
    """
    now = utcnow()
    domain.status = DOMAIN_STATUS_REVOKED
    domain.revoked_at = now
    domain.is_primary = False
    domain.certificate_status = CERT_STATUS_NONE
    domain.certificate_issued_at = None
    domain.certificate_expires_at = None
    domain.certificate_serial = None
    db.flush([domain])

    audit_service.record(
        db,
        organization_id=domain.organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.CUSTOM_DOMAIN,
        resource_id=domain.id,
        action=AuditAction.DOMAIN_REVOKED,
        details={"hostname": domain.hostname},
        **(audit_context or {}),
    )
    logger.info("branding.domain_revoked", extra={"hostname": domain.hostname})
    return domain


def release_domain(
    db: Session,
    *,
    domain: CustomDomain,
    actor_id: Optional[uuid.UUID] = None,
    audit_context: Optional[dict[str, Any]] = None,
) -> None:
    """Delete the row, freeing the hostname for anyone to claim.

    Deliberately harder to reach than revocation, and audited as DELETED
    rather than DOMAIN_REVOKED, because it is the only operation in this
    module that lets a different tenant take a name this one held.
    """
    hostname = domain.hostname
    organization_id = domain.organization_id
    domain_id = domain.id

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.CUSTOM_DOMAIN,
        resource_id=domain_id,
        action=AuditAction.DELETED,
        details={"hostname": hostname, "effect": "hostname_released"},
        **(audit_context or {}),
    )
    db.delete(domain)
    db.flush()
    logger.info("branding.domain_released", extra={"hostname": hostname})


# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------


def request_certificate(
    db: Session,
    *,
    domain: CustomDomain,
    actor_id: Optional[uuid.UUID] = None,
    audit_context: Optional[dict[str, Any]] = None,
) -> CustomDomain:
    """ARCH-25 hardening invariant 1.

    The refusal below is the whole point of this function. Everything else is
    bookkeeping around it.

    `may_request_certificate` mirrors
    `ck_custom_domains_certificate_requires_verification` exactly, so the
    service and the schema agree about what "verified" means and neither can
    be relaxed without the other failing.
    """
    assert_custom_domains_enabled()

    if not domain.may_request_certificate:
        # DENIED audit row before the raise: an attempt to obtain a
        # certificate for an unverified hostname is exactly the event a
        # security review wants to find, and a refusal that leaves no trace
        # is indistinguishable from an attempt that never happened.
        audit_service.record(
            db,
            organization_id=domain.organization_id,
            actor_id=actor_id,
            resource_type=AuditResourceType.CUSTOM_DOMAIN,
            resource_id=domain.id,
            action=AuditAction.TLS_ISSUED,
            outcome="DENIED",
            details={
                "hostname": domain.hostname,
                "reason": "domain_not_verified",
                "status": domain.status,
            },
            **(audit_context or {}),
        )
        raise CertificateRefusedError(
            f"{domain.hostname} has not completed DNS verification. A "
            "certificate is never requested for an unverified hostname — "
            "that would be a certificate issued for a name we have no "
            "evidence you control."
        )

    agent = str(getattr(settings, "ACME_AGENT_URL", "") or "").strip()
    if not agent:
        raise CertificateProvisioningError(
            "No ACME agent is configured (ACME_AGENT_URL is empty), so no "
            "certificate can be issued. The domain stays verified and "
            "unserved rather than being marked as covered."
        )

    domain.certificate_status = CERT_STATUS_PENDING
    domain.certificate_last_error = None
    db.flush([domain])

    logger.info(
        "branding.certificate_requested",
        extra={"hostname": domain.hostname, "agent": agent},
    )
    return domain


def record_certificate_issued(
    db: Session,
    *,
    domain: CustomDomain,
    issued_at: datetime,
    expires_at: datetime,
    serial: Optional[str] = None,
    audit_context: Optional[dict[str, Any]] = None,
) -> CustomDomain:
    """Record a successful issuance reported by the ACME agent.

    Re-checks the verification gate. This function is called from a background
    job, and a domain can be revoked between the request and the callback;
    writing ISSUED onto a revoked row would violate
    `ck_custom_domains_revoked_is_inert` at flush, but failing here with a
    readable error beats failing there with a constraint name.
    """
    if not domain.may_request_certificate:
        raise CertificateRefusedError(
            f"{domain.hostname} is no longer verified. Refusing to record an "
            "issued certificate against it."
        )
    if expires_at <= issued_at:
        raise CertificateProvisioningError(
            "The ACME agent reported an expiry at or before issuance. "
            "Refusing to record a certificate whose renewal window cannot be "
            "computed — an unknown expiry is an outage with no warning."
        )

    domain.certificate_status = CERT_STATUS_ISSUED
    domain.certificate_issued_at = issued_at
    domain.certificate_expires_at = expires_at
    domain.certificate_serial = serial
    domain.certificate_last_error = None
    db.flush([domain])

    audit_service.record(
        db,
        organization_id=domain.organization_id,
        resource_type=AuditResourceType.CUSTOM_DOMAIN,
        resource_id=domain.id,
        action=AuditAction.TLS_ISSUED,
        details={
            "hostname": domain.hostname,
            "expires_at": expires_at.isoformat(),
            "serial": serial,
        },
        **(audit_context or {}),
    )
    logger.info(
        "branding.certificate_issued",
        extra={
            "hostname": domain.hostname,
            "expires_at": expires_at.isoformat(),
        },
    )
    return domain


def record_certificate_failure(
    db: Session, *, domain: CustomDomain, error: str
) -> CustomDomain:
    domain.certificate_status = CERT_STATUS_FAILED
    domain.certificate_last_error = (error or "")[:512]
    db.flush([domain])
    logger.error(
        "branding.certificate_failed",
        extra={"hostname": domain.hostname, "error": domain.certificate_last_error},
    )
    return domain


def days_until_expiry(domain: CustomDomain) -> Optional[int]:
    if domain.certificate_expires_at is None:
        return None
    delta = domain.certificate_expires_at - utcnow()
    return int(delta.total_seconds() // 86400)


__all__ = [
    "CHALLENGE_TOKEN_BYTES",
    "assert_claimable_hostname",
    "assert_custom_domains_enabled",
    "certificates_due_for_renewal",
    "challenge_record_name",
    "claim_domain",
    "days_until_expiry",
    "dead_man_certificates",
    "domains_due_for_check",
    "get_domain",
    "instructions_for",
    "issue_challenge_token",
    "list_domains",
    "record_certificate_failure",
    "record_certificate_issued",
    "reissue_challenge",
    "release_domain",
    "request_certificate",
    "reserved_hosts",
    "resolve_verified_host",
    "revoke_domain",
    "set_primary",
    "verify_domain",
]