"""ARCH-15 Steps 15.6 / 15.7 — the tenant billing API."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import OrganizationContext, RequireOrgOwner, RequireOrgRole, get_db
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.organization import OrganizationRole
from app.schemas.invoice import (
    BillingAccessResponse,
    CheckoutSessionRequest,
    EphemeralSessionResponse,
    InvoiceDetailResponse,
    InvoiceListResponse,
    InvoiceReproductionResponse,
    InvoiceSummary,
    PortalSessionRequest,
    SeatSyncRequest,
    SubscriptionStateResponse,
)
from app.services import audit_service
from app.services.billing import (
    account_service,
    dunning_service,
    invoice_service,
    portal_service,
    seat_service,
    subscription_service,
)
from app.services.billing.portal_service import (
    CheckoutConfigurationError,
    ReauthenticationRequiredError,
)

logger = logging.getLogger("app.api.v1.billing")

router = APIRouter(tags=["Billing"])

RequireOrgBillingReader = RequireOrgRole(
    [OrganizationRole.OWNER, OrganizationRole.ADMIN, OrganizationRole.BILLING]
)

_NOT_FOUND = "Invoice not found."


def _invoice_or_404(
    db: Session, *, organization_id: uuid.UUID, invoice_id: uuid.UUID
):
    invoice = invoice_service.get_for_organization(
        db, organization_id=organization_id, invoice_id=invoice_id
    )
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND
        )
    return invoice


# ============================================================================
# Reads
# ============================================================================


@router.get(
    "/organizations/{organization_id}/billing/subscription",
    response_model=SubscriptionStateResponse,
    summary="Current subscription and seat state",
)
def get_subscription_state(
    organization_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgBillingReader),
    db: Session = Depends(get_db),
) -> SubscriptionStateResponse:
    account = account_service.get_for_organization(
        db, organization_id=context.organization_id
    )
    subscription = subscription_service.live_subscription_for_organization(
        db, organization_id=context.organization_id
    )
    drift = seat_service.detect_drift(db, organization_id=context.organization_id)

    return SubscriptionStateResponse(
        organization_id=context.organization_id,
        has_billing_account=account is not None,
        currency=(account.currency if account else None),
        billing_email=(account.billing_email if account else None),
        delinquent_since=(account.delinquent_since if account else None),
        subscription=(
            InvoiceSummary.subscription_view(subscription) if subscription else None
        ),
        seats_billable=seat_service.billable_seats(
            db, organization_id=context.organization_id
        ),
        seats_purchased=(int(subscription.seats_purchased) if subscription else 0),
        seat_drift_delta=(drift.delta if drift else 0),
        access_state=dunning_service.access_state(
            db, organization_id=context.organization_id
        ).value,
    )


@router.get(
    "/organizations/{organization_id}/billing/access",
    response_model=BillingAccessResponse,
    summary="What this organization may currently do",
)
def get_billing_access(
    organization_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgBillingReader),
    db: Session = Depends(get_db),
) -> BillingAccessResponse:
    state = dunning_service.access_state(db, organization_id=context.organization_id)
    position = dunning_service.position(db, organization_id=context.organization_id)

    return BillingAccessResponse(
        organization_id=context.organization_id,
        access_state=state.value,
        writes_allowed=state.writes_allowed,
        reads_allowed=state.reads_allowed,
        export_allowed=state.export_allowed,
        data_retained=True,
        dunning_steps_applied=[step.value for step in position.steps_applied],
        next_dunning_step=(
            position.next_step.value if position.next_step else None
        ),
    )


@router.get(
    "/organizations/{organization_id}/invoices",
    response_model=InvoiceListResponse,
    summary="List invoices",
)
def list_invoices(
    organization_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: OrganizationContext = Depends(RequireOrgBillingReader),
    db: Session = Depends(get_db),
) -> InvoiceListResponse:
    invoices = invoice_service.list_for_organization(
        db, organization_id=context.organization_id, limit=limit, offset=offset
    )
    return InvoiceListResponse(
        organization_id=context.organization_id,
        invoices=[InvoiceSummary.model_validate(inv) for inv in invoices],
        count=len(invoices),
    )


@router.get(
    "/organizations/{organization_id}/invoices/{invoice_id}",
    response_model=InvoiceDetailResponse,
    summary="One invoice with its frozen line items",
)
def get_invoice(
    organization_id: uuid.UUID,
    invoice_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgBillingReader),
    db: Session = Depends(get_db),
) -> InvoiceDetailResponse:
    invoice = _invoice_or_404(
        db, organization_id=context.organization_id, invoice_id=invoice_id
    )
    matches, stored, recomputed = invoice_service.verify_digest(db, invoice)

    if not matches:
        logger.error(
            "invoice.digest_mismatch_on_read",
            extra={
                "number": invoice.number,
                "stored_digest": stored,
                "recomputed_digest": recomputed,
            },
        )

    return InvoiceDetailResponse.build(
        invoice=invoice, digest_matches=matches
    )


@router.get(
    "/organizations/{organization_id}/invoices/{invoice_id}/reproduction",
    response_model=InvoiceReproductionResponse,
    summary="Reproduce an invoice from its frozen provenance (A9)",
)
def reproduce_invoice(
    organization_id: uuid.UUID,
    invoice_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgBillingReader),
    db: Session = Depends(get_db),
) -> InvoiceReproductionResponse:
    invoice = _invoice_or_404(
        db, organization_id=context.organization_id, invoice_id=invoice_id
    )
    report = invoice_service.reproduce(db, invoice=invoice)

    audit_service.record(
        db,
        organization_id=context.organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.INVOICE,
        resource_id=invoice.id,
        action=AuditAction.ACCESSED,
        details={
            "invoice_number": invoice.number,
            "reproducible": report.reproducible,
            "price_book_version": report.price_book_version,
            "quota_tier_version": report.quota_tier_version,
        },
    )
    db.commit()

    return InvoiceReproductionResponse.model_validate(report.as_dict())


# ============================================================================
# Mutations — owner only
# ============================================================================


@router.post(
    "/organizations/{organization_id}/billing/checkout-session",
    response_model=EphemeralSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a subscription checkout",
)
def create_checkout_session(
    organization_id: uuid.UUID,
    payload: CheckoutSessionRequest,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> EphemeralSessionResponse:
    try:
        session = portal_service.create_checkout_session(
            db,
            organization_id=context.organization_id,
            quota_tier_key=payload.quota_tier_key,
            seats=payload.seats,
            price_id=payload.price_id,
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
        )
    except CheckoutConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    audit_service.record(
        db,
        organization_id=context.organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.BILLING_ACCOUNT,
        action=AuditAction.CHECKOUT_STARTED,
        details={
            "quota_tier_key": payload.quota_tier_key,
            "seats": payload.seats,
            "stripe_session_id": session.stripe_session_id,
        },
    )
    db.commit()

    return EphemeralSessionResponse(
        url=session.url,
        kind=session.kind,
        expires_at=session.expires_at,
    )


@router.post(
    "/organizations/{organization_id}/billing/portal-session",
    response_model=EphemeralSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Mint a Stripe Customer Portal session (owner only, re-auth gated)",
)
def create_portal_session(
    organization_id: uuid.UUID,
    request: Request,
    payload: Optional[PortalSessionRequest] = None,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> EphemeralSessionResponse:
    try:
        session = portal_service.create_portal_session(
            db,
            organization_id=context.organization_id,
            return_url=(payload.return_url if payload else None),
            authorization_header=authorization,
        )
    except ReauthenticationRequiredError as exc:
        audit_service.record(
            db,
            organization_id=context.organization_id,
            actor_id=context.user_id,
            resource_type=AuditResourceType.BILLING_ACCOUNT,
            action=AuditAction.PORTAL_SESSION_MINTED,
            outcome="DENIED",
            details={"reason": "reauthentication_required"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
            headers={"WWW-Authenticate": 'Bearer error="reauth_required"'},
        ) from exc
    except account_service.BillingAccountNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This organization has no billing account yet.",
        ) from exc

    audit_service.record(
        db,
        organization_id=context.organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.BILLING_ACCOUNT,
        action=AuditAction.PORTAL_SESSION_MINTED,
        details={
            "stripe_session_id": session.stripe_session_id,
            "url_persisted": False,
            "ip_address": (request.client.host if request.client else None),
        },
    )
    db.commit()

    return EphemeralSessionResponse(
        url=session.url,
        kind=session.kind,
        expires_at=session.expires_at,
    )


@router.post(
    "/organizations/{organization_id}/billing/seats",
    response_model=SubscriptionStateResponse,
    summary="Re-assert the seat count at Stripe (owner only)",
)
def sync_seats(
    organization_id: uuid.UUID,
    payload: Optional[SeatSyncRequest] = None,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> SubscriptionStateResponse:
    result = seat_service.sync_seats(
        db,
        organization_id=context.organization_id,
        reason=(payload.reason if payload else "owner_requested"),
        force=bool(payload.force) if payload else False,
    )

    audit_service.record(
        db,
        organization_id=context.organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.SUBSCRIPTION,
        action=AuditAction.SEATS_CHANGED,
        details=result,
    )
    db.commit()

    return get_subscription_state(organization_id, context=context, db=db)


__all__ = ["RequireOrgBillingReader", "router"]