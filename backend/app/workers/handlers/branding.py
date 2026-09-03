"""ARCH-25 — background jobs: DNS verification polling and TLS renewal.

TWO JOB TYPES, TWO FAILURE MODES THEY EXIST TO PREVENT
======================================================

`domain.verify_dns` polls PENDING domains. Without it a tenant publishes the
TXT record, closes the console, and nothing ever notices — verification would
only ever happen while somebody was watching.

`tls.renew_sweep` renews certificates inside the window and, separately,
ALERTS on certificates inside the dead-man threshold that have not renewed.
The second half is the important half: certificate expiry on a customer's
vanity domain is a total outage for that tenant, and the only symptom is their
users' browsers refusing to connect. Nothing appears in our logs, no request
errors, no 5xx rate. The alert is the entire detection mechanism.

BOTH ARE REGISTERED IN TWO PLACES AND MUST STAY THAT WAY
========================================================

`app/workers/handlers/__init__.py` maps the job type to a callable, and
`app/workers/profiles.py` puts it on the LIGHT profile.

A handler registered without a profile entry is worse than one registered in
neither: `assert_imports_match_profile()` runs `uncovered_job_types()` at every
worker's startup and raises `ProfileError`, so the whole fleet stops booting.
That is the failure ARCH-16 shipped, and the comment recording it is still in
profiles.py. verify_arch25.py G4 asserts both registrations exist.

WHY NEITHER JOB RAISES ON ONE TENANT'S FAILURE
==============================================

Both process a batch. A missing TXT record on one domain is an ordinary,
expected outcome — DNS propagation takes hours — and letting it abort the
sweep would mean one tenant's slow registrar blocked every other tenant's
verification. Both call their service with `raise_on_failure=False` and
return counts.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.branding import domain_service

logger = logging.getLogger("app.workers.handlers.branding")


def handle_domain_verify_dns(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Poll PENDING custom domains whose next check is due.

    `raise_on_failure=False` throughout. A domain whose record has not
    propagated is not an error; it is the normal state for the first several
    hours after a tenant edits their zone.

    Resolver failures are counted separately from record misses and reported
    separately, because they mean different things: a spike in `resolver_failed`
    is our DNS breaking, a spike in `unverified` is a lot of tenants mid-setup.
    Merging the two into one "failures" number would hide our own outage inside
    a metric that looks like customer behaviour.
    """
    limit = int(payload.get("limit") or 200)
    due = domain_service.domains_due_for_check(db, limit=limit)

    verified = 0
    unverified = 0
    resolver_failed = 0

    for domain in due:
        try:
            result = domain_service.verify_domain(
                db, domain=domain, raise_on_failure=False
            )
        except Exception:  # noqa: BLE001 - one tenant must not stop the sweep
            logger.exception(
                "branding.verify_sweep_row_failed",
                extra={"hostname": domain.hostname},
            )
            db.rollback()
            continue

        if result.verified:
            verified += 1
        elif result.resolver_failed:
            resolver_failed += 1
        else:
            unverified += 1

    db.commit()

    summary = {
        "checked": len(due),
        "verified": verified,
        "unverified": unverified,
        "resolver_failed": resolver_failed,
    }
    logger.info("branding.verify_sweep", extra=summary)
    return summary


def handle_tls_renew_sweep(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Renew certificates in the window and alert on the dead-man threshold.

    The renewal half is retryable and unremarkable. The alert half is the
    reason this job exists: `dead_man_certificates` returns certificates close
    enough to expiry that the retries have demonstrably not worked, and each
    one is logged at ERROR with the hostname and the days remaining so that a
    log-based alert can fire on it.

    A certificate that expires produces no error anywhere in our logs — the
    failure is entirely on the client side, in browsers we never see. If this
    ERROR line is not wired to an alert, the feature has no expiry detection
    at all, and that is worth saying out loud in the place where the line is
    emitted.
    """
    renewed = 0
    failed = 0

    due = domain_service.certificates_due_for_renewal(db)
    for domain in due:
        try:
            domain_service.request_certificate(db, domain=domain)
            renewed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning(
                "branding.renewal_request_failed",
                extra={"hostname": domain.hostname, "error": str(exc)[:200]},
            )
            db.rollback()

    db.commit()

    alerts: list[dict[str, Any]] = []
    for domain in domain_service.dead_man_certificates(db):
        remaining = domain_service.days_until_expiry(domain)
        alerts.append(
            {"hostname": domain.hostname, "days_until_expiry": remaining}
        )
        logger.error(
            "branding.certificate_dead_man",
            extra={
                "hostname": domain.hostname,
                "organization_id": str(domain.organization_id),
                "days_until_expiry": remaining,
                "threshold_days": int(
                    getattr(settings, "TLS_DEAD_MAN_DAYS", 7)
                ),
            },
        )

    summary = {
        "due": len(due),
        "renewal_requested": renewed,
        "renewal_failed": failed,
        "dead_man_alerts": len(alerts),
        "alerts": alerts,
    }
    logger.info(
        "branding.tls_sweep",
        extra={k: v for k, v in summary.items() if k != "alerts"},
    )
    return summary


__all__ = ["handle_domain_verify_dns", "handle_tls_renew_sweep"]