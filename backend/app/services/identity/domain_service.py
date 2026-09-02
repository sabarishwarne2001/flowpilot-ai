"""ARCH-16 Step 16.2 — domain verification."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text as sql_text

from app.models.identity import DomainStatus, VerifiedDomain
from app.services.identity import dns_service
from app.services.identity._integration import (
    IdentityPrincipal, commit_and_refresh, emit_event, get_settings,
    principal_for_system, utcnow, write_audit,
)
from app.services.identity.errors import (
    DomainPolicyRefused, DomainVerificationFailed, SsoBindingConflict,
)

logger = logging.getLogger(__name__)

_PUBLIC_SUFFIX_FLOOR = frozenset({
    "com", "net", "org", "io", "co", "ai", "dev", "app", "cloud", "info", "biz",
    "edu", "gov", "mil", "int", "eu", "us", "uk", "de", "fr", "nl", "in", "au",
    "ca", "jp", "cn", "br", "za", "sg", "ch", "se", "no", "es", "it", "pl",
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.in", "net.in", "org.in",
    "com.au", "net.au", "org.au", "co.jp", "co.za", "com.br", "com.sg",
    "github.io", "gitlab.io", "netlify.app", "vercel.app", "herokuapp.com",
    "pages.dev", "workers.dev", "web.app", "firebaseapp.com", "azurewebsites.net",
    "s3.amazonaws.com", "cloudfront.net", "blogspot.com", "wordpress.com",
})

_CONSUMER_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "yahoo.com", "yahoo.co.uk", "yahoo.co.in", "ymail.com",
    "aol.com", "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com",
    "pm.me", "gmx.com", "gmx.net", "mail.com", "zoho.com", "yandex.com",
    "yandex.ru", "qq.com", "163.com", "126.com", "naver.com", "rediffmail.com",
    "fastmail.com", "hushmail.com", "tutanota.com", "mail.ru", "inbox.com",
    "duck.com", "simplelogin.io", "guerrillamail.com", "mailinator.com",
    "10minutemail.com", "temp-mail.org", "sharklasers.com", "yopmail.com",
})


@dataclass
class DomainCheckOutcome:
    domain_id: object
    domain: str
    previous_status: str
    new_status: str
    found: bool
    resolver_failed: bool
    detail: str = ""


def normalise_domain(raw: str) -> str:
    d = (raw or "").strip().lower().rstrip(".")
    if d.startswith("http://") or d.startswith("https://"):
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0].split("@")[-1].split(":")[0]
    if not d:
        raise DomainPolicyRefused("A domain is required.")
    try:
        d = d.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise DomainPolicyRefused(f"Not a valid domain name: {raw!r}") from exc
    return d


def _public_suffix(domain: str) -> str | None:
    try:
        import publicsuffix2
        if hasattr(publicsuffix2, "get_tld"):
            return publicsuffix2.get_tld(domain)
        from publicsuffix2 import PublicSuffixList
        psl = PublicSuffixList()
        return psl.get_public_suffix(domain)
    except Exception:
        return None


def assert_claimable(domain: str) -> None:
    labels = domain.split(".")
    if len(labels) < 2 or any(not lbl for lbl in labels):
        raise DomainPolicyRefused(f"{domain!r} is not a fully qualified domain.")
    if any(len(lbl) > 63 for lbl in labels) or len(domain) > 253:
        raise DomainPolicyRefused("Domain exceeds DNS length limits.")

    if domain in _CONSUMER_MAIL_DOMAINS:
        raise DomainPolicyRefused(
            f"{domain} is a public email provider and cannot be claimed by an "
            "organization. Use a domain your organization controls."
        )

    if domain in _PUBLIC_SUFFIX_FLOOR:
        raise DomainPolicyRefused(
            f"{domain} is a public suffix and cannot be claimed."
        )

    suffix = _public_suffix(domain)
    if suffix and suffix == domain:
        raise DomainPolicyRefused(
            f"{domain} is a public suffix and cannot be claimed."
        )

    for candidate in (".".join(labels[-2:]), ".".join(labels[-3:])):
        if candidate == domain and candidate in _PUBLIC_SUFFIX_FLOOR:
            raise DomainPolicyRefused(f"{domain} is a public suffix.")


def _issue_token() -> str:
    return secrets.token_urlsafe(32)


def expected_record(token: str) -> str:
    settings = get_settings()
    prefix = getattr(settings, "DOMAIN_VERIFICATION_TXT_PREFIX",
                     "flowpilot-site-verification")
    return f"{prefix}={token}"


def claim_domain(db, *, organization_id, raw_domain: str,
                 principal: IdentityPrincipal) -> VerifiedDomain:
    settings = get_settings()
    domain = normalise_domain(raw_domain)
    assert_claimable(domain)

    existing = (
        db.query(VerifiedDomain)
        .filter(VerifiedDomain.organization_id == organization_id,
                VerifiedDomain.domain == domain)
        .one_or_none()
    )
    ttl_days = int(getattr(settings, "DOMAIN_VERIFICATION_TOKEN_TTL_DAYS", 30))
    now = utcnow()

    if existing is not None:
        existing.challenge_token = _issue_token()
        existing.challenge_issued_at = now
        existing.challenge_expires_at = now + timedelta(days=ttl_days)
        if existing.status in (DomainStatus.PENDING, DomainStatus.REVOKED):
            existing.status = DomainStatus.PENDING
        row = existing
    else:
        row = VerifiedDomain(
            organization_id=organization_id,
            domain=domain,
            status=DomainStatus.PENDING,
            challenge_token=_issue_token(),
            challenge_issued_at=now,
            challenge_expires_at=now + timedelta(days=ttl_days),
            created_by_user_id=principal.actor_id if principal else None,
        )
        db.add(row)

    db.flush()
    write_audit(db, organization_id=organization_id, action="CREATED",
                resource_type="VERIFIED_DOMAIN", resource_id=row.id,
                principal=principal, details={"domain": domain})
    return commit_and_refresh(db, row)


def verify_domain(db, *, domain_row: VerifiedDomain,
                  principal: IdentityPrincipal) -> VerifiedDomain:
    now = utcnow()
    if domain_row.challenge_expires_at and domain_row.challenge_expires_at < now:
        raise DomainVerificationFailed(
            "The verification token has expired. Generate a new one."
        )

    expected = expected_record(domain_row.challenge_token)
    settings = get_settings()
    prefix = getattr(settings, "DOMAIN_VERIFICATION_TXT_PREFIX",
                     "flowpilot-site-verification")

    apex = dns_service.lookup_txt(domain_row.domain)
    found = apex.contains(expected)
    if not found:
        sub = dns_service.lookup_txt(domain_row.domain, subdomain="_flowpilot")
        found = sub.contains(expected)
        if not (apex.resolved or sub.resolved):
            raise DomainVerificationFailed(
                "Could not reach a DNS resolver. This is our side, not yours — "
                "try again shortly."
            )

    domain_row.last_checked_at = now
    if not found:
        write_audit(db, organization_id=domain_row.organization_id,
                    action="UPDATED", resource_type="VERIFIED_DOMAIN",
                    resource_id=domain_row.id, principal=principal,
                    outcome="DENIED",
                    details={"domain": domain_row.domain, "reason": "record_absent"})
        commit_and_refresh(db, domain_row)
        raise DomainVerificationFailed(
            f"No TXT record `{prefix}=...` matching this organization was found "
            f"on {domain_row.domain}. DNS changes can take up to 48 hours."
        )

    first_time = domain_row.first_verified_at is None
    domain_row.status = DomainStatus.VERIFIED
    domain_row.last_seen_at = now
    domain_row.grace_expires_at = None
    domain_row.consecutive_failures = 0
    if first_time:
        domain_row.first_verified_at = now

    db.flush()
    write_audit(db, organization_id=domain_row.organization_id, action="UPDATED",
                resource_type="VERIFIED_DOMAIN", resource_id=domain_row.id,
                principal=principal,
                details={"domain": domain_row.domain, "status": "VERIFIED"})
    if first_time:
        emit_event(db, event_type="identity.domain_verified",
                   organization_id=domain_row.organization_id,
                   payload={"domain": domain_row.domain,
                            "verified_domain_id": str(domain_row.id)})
    return commit_and_refresh(db, domain_row)


def bind_sso(db, *, domain_row: VerifiedDomain,
             principal: IdentityPrincipal) -> VerifiedDomain:
    if not domain_row.provisioning_allowed:
        raise SsoBindingConflict(
            "This domain must be verified before SSO can be bound to it."
        )

    conflict = db.execute(
        sql_text(
            "SELECT organization_id FROM verified_domains "
            "WHERE domain = :d AND is_sso_binding AND id <> :self_id LIMIT 1"
        ),
        {"d": domain_row.domain, "self_id": str(domain_row.id)},
    ).first()
    if conflict is not None:
        raise SsoBindingConflict(
            f"Another organization already federates {domain_row.domain}. "
            "Contact support to resolve a domain ownership dispute."
        )

    domain_row.is_sso_binding = True
    db.flush()
    write_audit(db, organization_id=domain_row.organization_id, action="UPDATED",
                resource_type="VERIFIED_DOMAIN", resource_id=domain_row.id,
                principal=principal,
                details={"domain": domain_row.domain, "sso_binding": True})
    return commit_and_refresh(db, domain_row)


def recheck_domains(db, *, limit: int = 200) -> list[DomainCheckOutcome]:
    settings = get_settings()
    interval_h = int(getattr(settings, "DOMAIN_VERIFICATION_RECHECK_INTERVAL_HOURS", 24))
    grace_days = int(getattr(settings, "DOMAIN_VERIFICATION_GRACE_DAYS", 14))
    now = utcnow()
    cutoff = now - timedelta(hours=interval_h)
    principal = principal_for_system("jobs.identity.recheck_domains")

    rows = (
        db.query(VerifiedDomain)
        .filter(VerifiedDomain.status.in_([DomainStatus.VERIFIED, DomainStatus.GRACE]))
        .filter((VerifiedDomain.last_checked_at.is_(None))
                | (VerifiedDomain.last_checked_at < cutoff))
        .order_by(VerifiedDomain.last_checked_at.asc().nullsfirst())
        .limit(limit)
        .all()
    )

    outcomes: list[DomainCheckOutcome] = []
    for row in rows:
        previous = str(row.status)
        expected = expected_record(row.challenge_token)

        apex = dns_service.lookup_txt(row.domain)
        found = apex.contains(expected)
        resolved = apex.resolved
        if not found:
            sub = dns_service.lookup_txt(row.domain, subdomain="_flowpilot")
            found = sub.contains(expected)
            resolved = resolved or sub.resolved

        row.last_checked_at = now

        if not resolved:
            outcomes.append(DomainCheckOutcome(
                row.id, row.domain, previous, previous, found=False,
                resolver_failed=True, detail="resolver unavailable; no state change"))
            continue

        if found:
            row.last_seen_at = now
            row.consecutive_failures = 0
            if row.status == DomainStatus.GRACE:
                row.status = DomainStatus.VERIFIED
                row.grace_expires_at = None
                write_audit(db, organization_id=row.organization_id,
                            action="UPDATED", resource_type="VERIFIED_DOMAIN",
                            resource_id=row.id, principal=principal,
                            details={"domain": row.domain, "status": "RECOVERED"})
            outcomes.append(DomainCheckOutcome(
                row.id, row.domain, previous, str(row.status), True, False))
            continue

        row.consecutive_failures = (row.consecutive_failures or 0) + 1

        if row.status == DomainStatus.VERIFIED:
            row.status = DomainStatus.GRACE
            row.grace_expires_at = now + timedelta(days=grace_days)
            emit_event(db, event_type="identity.domain_lapsed",
                       organization_id=row.organization_id,
                       payload={"domain": row.domain, "phase": "GRACE",
                                "grace_expires_at": row.grace_expires_at.isoformat(),
                                "verified_domain_id": str(row.id)})
            write_audit(db, organization_id=row.organization_id, action="UPDATED",
                        resource_type="VERIFIED_DOMAIN", resource_id=row.id,
                        principal=principal, outcome="DENIED",
                        details={"domain": row.domain, "status": "GRACE"})
        elif row.status == DomainStatus.GRACE and row.grace_expires_at \
                and row.grace_expires_at <= now:
            row.status = DomainStatus.LAPSED
            emit_event(db, event_type="identity.domain_lapsed",
                       organization_id=row.organization_id,
                       payload={"domain": row.domain, "phase": "LAPSED",
                                "verified_domain_id": str(row.id)})
            write_audit(db, organization_id=row.organization_id, action="UPDATED",
                        resource_type="VERIFIED_DOMAIN", resource_id=row.id,
                        principal=principal, outcome="DENIED",
                        details={"domain": row.domain, "status": "LAPSED",
                                 "effect": "jit_provisioning_blocked"})

        outcomes.append(DomainCheckOutcome(
            row.id, row.domain, previous, str(row.status), False, False))

    db.commit()
    return outcomes


def purge_expired_assertion_payloads(db, *, limit: int = 1000) -> int:
    result = db.execute(
        sql_text(
            "UPDATE sso_assertions SET raw_payload = NULL "
            "WHERE id IN (SELECT id FROM sso_assertions "
            "             WHERE raw_payload IS NOT NULL AND raw_purge_after < now() "
            "             LIMIT :lim)"
        ),
        {"lim": limit},
    )
    db.commit()
    return result.rowcount or 0
