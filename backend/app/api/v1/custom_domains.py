"""ARCH-25 §1, §2 — custom domain endpoints.

    GET    /organizations/{id}/custom-domains                  list    [ADMIN]
    POST   /organizations/{id}/custom-domains                  claim   [OWNER]
    GET    /organizations/{id}/custom-domains/{did}            detail  [ADMIN]
    DELETE /organizations/{id}/custom-domains/{did}            release [OWNER]
    POST   /organizations/{id}/custom-domains/{did}/verify     verify  [OWNER]
    POST   /organizations/{id}/custom-domains/{did}/challenge  reissue [OWNER]
    PUT    /organizations/{id}/custom-domains/{did}/primary    primary [OWNER]
    POST   /organizations/{id}/custom-domains/{did}/certificate TLS    [OWNER]
    DELETE /organizations/{id}/custom-domains/{did}/certificate revoke [OWNER]

WHY READS ARE ADMIN AND EVERY WRITE IS OWNER
============================================

An administrator has to be able to see which hostnames the tenant has claimed
and why one is failing verification — that is support work, and hiding it
behind OWNER means the person debugging a DNS record cannot see the record
they are debugging.

Every write is OWNER. A vanity hostname resolves to a tenant, which makes
claiming one authentication-adjacent rather than cosmetic: the person who can
add `ai.acme.com` is the person who decides which origin Acme's users type
their password into. That is an ownership decision, not an administrative one.

The same split as ARCH-22's BYOK console, for the same reason.

WHY `_assert_scope` RETURNS 404
===============================

`{organization_id}` in the path and the organization the caller's session
resolves to are two different things, and a mismatch means the caller is
asking about a tenant that is not theirs. 404, not 403 — 403 would confirm the
organization exists.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import (
    OrganizationContext,
    RequireOrgAdmin,
    RequireOrgOwner,
    get_db,
)
from app.core.client_ip import client_ip
from app.models.custom_domain import CERT_STATUS_NONE, CustomDomain
from app.schemas.custom_domain import (
    CertificateStatusResponse,
    CustomDomainCreate,
    CustomDomainDetail,
    CustomDomainPrimaryUpdate,
    CustomDomainResponse,
    DomainVerificationResult,
)
from app.services.branding import domain_service

logger = logging.getLogger("app.api.v1.custom_domains")

router = APIRouter(tags=["Custom Domains"])

BASE = "/organizations/{organization_id}/custom-domains"


# ---------------------------------------------------------------------------
# Guards and helpers
# ---------------------------------------------------------------------------


def _assert_scope(
    context: OrganizationContext, organization_id: uuid.UUID
) -> None:
    if context.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )


def _client_context(request: Request) -> dict[str, Optional[str]]:
    """Audit attribution for one request.

    `client_ip` and not `request.client.host`. Behind the ingress the latter
    is the LOAD BALANCER, identical for every tenant on the cluster, and it
    populates the field with something plausible and wrong. That was ARCH-23
    finding B-1 on the BYOK router; domain claims and certificate requests are
    the same class of security-sensitive row and get the same treatment.

    `client_ip` applies TRUSTED_PROXY_HOPS and returns None rather than an
    untrusted address. A null IP is honest; a wrong one is not.
    """
    return {
        "ip_address": client_ip(request),
        "user_agent": request.headers.get("user-agent"),
    }


def _detail(domain: CustomDomain) -> CustomDomainDetail:
    """Serialise one domain with its instructions and derived readiness.

    `may_request_certificate` is computed here and SENT. The console does not
    re-derive it from `status === "VERIFIED"`: ARCH-24's rule that the backend
    owns a threshold generalises, and a button enabled by a frontend guess
    that the server then refuses reads as a bug rather than as a policy.
    """
    base = CustomDomainResponse.model_validate(domain).model_dump()
    return CustomDomainDetail(
        **base,
        challenge=domain_service.instructions_for(domain),
        may_request_certificate=domain.may_request_certificate,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get(BASE, response_model=list[CustomDomainDetail])
def list_custom_domains(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> Any:
    _assert_scope(context, organization_id)
    return [
        _detail(row)
        for row in domain_service.list_domains(
            db, organization_id=organization_id
        )
    ]


@router.get(BASE + "/{domain_id}", response_model=CustomDomainDetail)
def get_custom_domain(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> Any:
    _assert_scope(context, organization_id)
    return _detail(
        domain_service.get_domain(
            db, organization_id=organization_id, domain_id=domain_id
        )
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


@router.post(
    BASE,
    response_model=CustomDomainDetail,
    status_code=status.HTTP_201_CREATED,
)
def claim_custom_domain(
    organization_id: uuid.UUID,
    payload: CustomDomainCreate,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    _assert_scope(context, organization_id)
    domain = domain_service.claim_domain(
        db,
        organization_id=organization_id,
        hostname=payload.hostname,
        actor_id=context.user_id,
        audit_context=_client_context(request),
    )
    db.commit()
    db.refresh(domain)
    return _detail(domain)


@router.post(
    BASE + "/{domain_id}/verify", response_model=DomainVerificationResult
)
def verify_custom_domain(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    """Check the challenge TXT record now.

    `raise_on_failure` is left at its default, so a missing record produces a
    409 body rather than a 200 with `verified: false` inside it. A person who
    clicked "Verify" and got a green response containing a quiet false is a
    person who will not read the false.

    The commit happens on the failure path too, via the exception handler's
    session teardown — `last_checked_at` and `consecutive_failures` were
    already flushed by the service, and losing them would let a caller retry
    without limit.
    """
    _assert_scope(context, organization_id)
    domain = domain_service.get_domain(
        db, organization_id=organization_id, domain_id=domain_id
    )
    try:
        result = domain_service.verify_domain(
            db,
            domain=domain,
            actor_id=context.user_id,
            audit_context=_client_context(request),
        )
    except Exception:
        db.commit()
        raise
    db.commit()
    return result


@router.post(BASE + "/{domain_id}/challenge", response_model=CustomDomainDetail)
def reissue_challenge(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    _assert_scope(context, organization_id)
    domain = domain_service.get_domain(
        db, organization_id=organization_id, domain_id=domain_id
    )
    domain_service.reissue_challenge(
        db,
        domain=domain,
        actor_id=context.user_id,
        audit_context=_client_context(request),
    )
    db.commit()
    db.refresh(domain)
    return _detail(domain)


@router.put(BASE + "/{domain_id}/primary", response_model=CustomDomainDetail)
def set_primary_domain(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    payload: CustomDomainPrimaryUpdate,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    _assert_scope(context, organization_id)
    domain = domain_service.get_domain(
        db, organization_id=organization_id, domain_id=domain_id
    )
    domain_service.set_primary(
        db,
        domain=domain,
        is_primary=payload.is_primary,
        actor_id=context.user_id,
        audit_context=_client_context(request),
    )
    db.commit()
    db.refresh(domain)
    return _detail(domain)


@router.post(
    BASE + "/{domain_id}/certificate", response_model=CertificateStatusResponse
)
def request_certificate(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    """ARCH-25 invariant 1 at the API boundary.

    The refusal lives in `domain_service.request_certificate` and in a CHECK
    constraint, not here. This endpoint deliberately performs no verification
    test of its own: a third copy of the rule is a third place for it to drift,
    and the one that matters is the one closest to the write.
    """
    _assert_scope(context, organization_id)
    domain = domain_service.get_domain(
        db, organization_id=organization_id, domain_id=domain_id
    )
    try:
        domain_service.request_certificate(
            db,
            domain=domain,
            actor_id=context.user_id,
            audit_context=_client_context(request),
        )
    except Exception:
        # The DENIED audit row the service wrote must survive the refusal.
        db.commit()
        raise
    db.commit()
    db.refresh(domain)
    return CertificateStatusResponse(
        hostname=domain.hostname,
        certificate_status=domain.certificate_status,
        certificate_issued_at=domain.certificate_issued_at,
        certificate_expires_at=domain.certificate_expires_at,
        certificate_serial=domain.certificate_serial,
        certificate_last_error=domain.certificate_last_error,
        days_until_expiry=domain_service.days_until_expiry(domain),
    )


@router.delete(
    BASE + "/{domain_id}/certificate", response_model=CustomDomainDetail
)
def revoke_domain(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    """Stop serving the hostname, keeping the claim.

    Mapped to DELETE on the CERTIFICATE sub-resource rather than on the domain
    itself, because that is what it does: the certificate goes away and the
    host stops resolving, while the row — and therefore the tenant's hold on
    the name — survives. Releasing the name is DELETE on the domain.
    """
    _assert_scope(context, organization_id)
    domain = domain_service.get_domain(
        db, organization_id=organization_id, domain_id=domain_id
    )
    domain_service.revoke_domain(
        db,
        domain=domain,
        actor_id=context.user_id,
        audit_context=_client_context(request),
    )
    db.commit()
    db.refresh(domain)
    return _detail(domain)


@router.delete(
    BASE + "/{domain_id}", status_code=status.HTTP_204_NO_CONTENT
)
def release_custom_domain(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Response:
    """Delete the row, freeing the hostname for anyone to claim.

    The only operation in this router that lets a different tenant take a name
    this one held. Audited as DELETED with an explicit
    `effect: hostname_released` so the difference from revocation is legible
    in the audit console without reading the code.
    """
    _assert_scope(context, organization_id)
    domain = domain_service.get_domain(
        db, organization_id=organization_id, domain_id=domain_id
    )
    if domain.certificate_status != CERT_STATUS_NONE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Revoke the certificate before releasing this hostname. "
                "Releasing it while a certificate is live would let another "
                "tenant claim a name we are still serving TLS for."
            ),
        )
    domain_service.release_domain(
        db,
        domain=domain,
        actor_id=context.user_id,
        audit_context=_client_context(request),
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]