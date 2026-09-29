"""ARCH-15 Steps 15.6 / 15.7 — the tenant billing API."""

from __future__ import annotations
from fastapi import Request

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from app.core.client_ip import client_ip as _resolve_client_ip
from app.core.config import settings

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

# ARCH30-T4F:access-summary-deps-import — A5.
from app.api.deps import (
    OrganizationContext,
    RequireOrgMember,
    RequireOrgOwner,
    RequireOrgRole,
    get_db,
)
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.organization import OrganizationRole
from app.schemas.billing import SeatPriceBookResponse
from app.schemas.invoice import (
    BillingAccessResponse,
    # ARCH30-T4F:access-summary-schema-import — A5.
    BillingAccessSummaryResponse,
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
from app.schemas.usage import (
    PlanEntitlement,
    PlanListResponse,
    PlanOption,
)
from app.services import audit_service, quota_service
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
from app.services.billing.payment_gateway import GatewayNotConfiguredError

logger = logging.getLogger("app.api.v1.billing")

router = APIRouter(tags=["Billing"])

RequireOrgBillingReader = RequireOrgRole(
    [OrganizationRole.OWNER, OrganizationRole.ADMIN, OrganizationRole.BILLING]
)

_NOT_FOUND = "Invoice not found."


@router.get(
    "/organizations/{organization_id}/billing/price-book/seat",
    response_model=SeatPriceBookResponse,
    summary="Disclose seat unit price and Stripe-sourced proration (ARCH-24)",
)
def get_seat_price_book(
    organization_id: uuid.UUID,
    additional_seats: int = Query(
        1,
        ge=1,
        le=1000,
        description="Seats the caller is about to allocate.",
    ),
    context: OrganizationContext = Depends(RequireOrgBillingReader),
    db: Session = Depends(get_db),
) -> SeatPriceBookResponse:
    """What one more seat costs, before anything allocates one.

    ARCH-24 Tranche 4. The IdP policy panel calls this so an administrator
    enabling JIT provisioning sees a real figure rather than a reassuring
    blank.

    Both money fields are nullable and both carry provenance. Nothing here is
    computed locally: the unit price is read from the subscription's pinned
    price book (the same lookup the invoice will make), and the proration is
    whatever Stripe's preview says. When Stripe is unreachable this returns
    200 with `proration_micros = null` and a stated reason, rather than 502 —
    the unit price alone still answers most of the question, and taking the
    panel down over a third-party timeout helps nobody.
    """
    disclosure = seat_service.seat_price_disclosure(
        db,
        organization_id=context.organization_id,
        additional_seats=additional_seats,
    )
    return SeatPriceBookResponse.build(disclosure)


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
    "/organizations/{organization_id}/billing/plans",
    response_model=PlanListResponse,
    summary="Plans this organization could subscribe to",
)
def list_plans(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgBillingReader),
) -> PlanListResponse:
    """Read-only projection of published quota tiers."""
    moment = datetime.now(timezone.utc)

    current = quota_service.resolve_tier(
        db, organization_id=context.organization_id, at=moment
    )
    # ARCH39-S1:current-plan — without a live subscription and without a tier
    # version covering "now", the assigned tier is still the current plan.
    current_key = (current.key if current else None) or portal_service.assigned_tier_key(
        db, organization_id=context.organization_id
    )

    tiers = quota_service.list_published_tiers(db, at=moment)
    from app.services.revops import price_books as _price_books

    book_prices = _price_books.published_prices(db)

    plans: list[PlanOption] = []
    for tier in tiers:
        entitlements = [
            PlanEntitlement(
                event_type=entry.limit_key,
                limit_quantity=int(entry.max_quantity) if entry.max_quantity is not None else None,
                limit_cost_micros=entry.max_cost_micros,
                overage_policy=entry.overage_policy,
                period=entry.period.value if hasattr(entry.period, "value") else str(entry.period),
            )
            for entry in tier.entries
        ]

        # ARCH-29 Tranche 2 (D-1, F-2).
        #
        # This block previously read
        #     price_id = getattr(settings, "BILLING_SEAT_PRICE_ID", None)
        # INSIDE this loop, which is loop-invariant: every tier received the
        # same gateway price while advertising different entitlements. Setting
        # that env var would have charged an Enterprise subscriber the one
        # global amount and granted them Enterprise limits, with nothing in
        # this system disagreeing until a provider statement arrived.
        #
        # The price now comes from the tier version being described. A tier
        # with no price is not free and is not an error: it is quoted, which
        # is what Enterprise is, and `is_priced` says so explicitly rather
        # than leaving the client to infer it from three nulls.
        #
        # Micros to minor units happens here, at the API boundary. Nothing
        # inside the application handles cents.
        unit_amount = (
            tier.unit_amount_micros // 10_000
            if tier.unit_amount_micros is not None
            else None
        )

        # ARCH50-S1:plan-prices. Every (interval, currency) the plan is sold in, from the published plan price
        # books, plus the tier version's own price when no book covers it.
        from app.schemas.usage import PlanPriceOption

        options = [PlanPriceOption(interval=p.interval, currency=p.currency, unit_amount=p.unit_amount_minor,
                                   price_id=p.gateway_price_id, source=p.source)
                   for p in book_prices if p.tier_key == tier.key]
        if tier.unit_amount_micros is not None and not any(
                (o.interval, o.currency) == (tier.billing_interval or "month", tier.currency or "USD") for o in options):
            options.append(PlanPriceOption(interval=tier.billing_interval or "month", currency=tier.currency or "USD",
                                           unit_amount=tier.unit_amount_micros // 10_000,
                                           price_id=tier.gateway_price_id, source="TIER"))

        plans.append(
            PlanOption(
                key=tier.key,
                display_name=tier.display_name,
                version=tier.version,
                is_current=(tier.key == current_key),
                price_id=tier.gateway_price_id,
                unit_amount=unit_amount,
                currency=tier.currency,
                interval=tier.billing_interval,
                is_priced=tier.is_priced,
                entitlements=entitlements,
                notes=None,
                prices=options,
            )
        )

    not_ready = portal_service.gateway_readiness()
    return PlanListResponse(
        checkout_available=not_ready is None,
        checkout_unavailable_reason=not_ready,
        organization_id=context.organization_id,
        current_tier_key=current_key,
        as_of=moment,
        plans=plans,
    )


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
    live = subscription_service.live_subscription_for_organization(
        db, organization_id=context.organization_id
    )

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
        subscription_status=(
            (live.status.value if hasattr(live.status, "value") else getattr(live.status, "value", live.status))
            if live
            else None
        ),
        grace_ends_at=live.grace_ends_at if live else None,
    )


# ARCH30-T4F:access-summary-route — A5.
@router.get(
    "/organizations/{organization_id}/billing/access-summary",
    response_model=BillingAccessSummaryResponse,
    summary="Whether this organization is read-only (any member)",
)
def get_billing_access_summary(
    organization_id: uuid.UUID,
    context: OrganizationContext = Depends(RequireOrgMember),
    db: Session = Depends(get_db),
) -> BillingAccessSummaryResponse:
    """The member-readable half of `/billing/access`.

    D-11 gave every write path a read-only gate and gave the console
    exactly one way to explain it — `DunningBanner`, which reads
    `/billing/access` and is therefore mounted only for OWNER, ADMIN and
    BILLING. An ordinary member hit a refused upload with no explanation
    anywhere on screen, because the three roles who could see the reason
    were the three least likely to be uploading.

    Org-scoped under `/organizations/{organization_id}/` rather than a
    bare `/billing/access-summary`, matching every other billing route:
    a member of several organizations must be able to ask about one of
    them, and an endpoint that infers the tenant from the session is an
    ARCH-02 isolation argument waiting to be lost.
    """
    state = dunning_service.access_state(
        db, organization_id=context.organization_id
    )
    live = subscription_service.live_subscription_for_organization(
        db, organization_id=context.organization_id
    )

    status_value = (
        (live.status.value if hasattr(live.status, "value") else getattr(live.status, "value", live.status))
        if live
        else None
    )
    grace_ends_at = live.grace_ends_at if live else None

    if not state.writes_allowed:
        # Already closed. Returning the expired date here would read as
        # "you have until <date in the past>", which is worse than no
        # date at all.
        summary_state = "RESTRICTED"
        exposed_grace = None
    elif status_value == "past_due" and grace_ends_at is not None:
        summary_state = "GRACE"
        exposed_grace = grace_ends_at
    else:
        summary_state = "ACTIVE"
        exposed_grace = None

    return BillingAccessSummaryResponse(
        state=summary_state,  # type: ignore[arg-type]
        is_read_only=not state.writes_allowed,
        grace_ends_at=exposed_grace,
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
    # ARCH39-S1:free-plan-endpoint — a zero-price tier is assigned here and
    # never reaches a payment gateway.
    if not payload.price_id and portal_service.is_free_tier(
        db, quota_tier_key=payload.quota_tier_key
    ):
        try:
            tier = portal_service.select_free_plan(
                db,
                organization_id=context.organization_id,
                quota_tier_key=payload.quota_tier_key,
            )
        except portal_service.PaidSubscriptionActiveError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "PAID_SUBSCRIPTION_ACTIVE",
                    "message": str(exc),
                    "details": {},
                },
            ) from exc
        audit_service.record(
            db,
            organization_id=context.organization_id,
            actor_id=context.user_id,
            resource_type=AuditResourceType.BILLING_ACCOUNT,
            action=AuditAction.CHECKOUT_STARTED,
            details={
                "quota_tier_key": payload.quota_tier_key,
                "mode": "free_plan_assigned",
                "tier_version": tier.version,
            },
        )
        db.commit()
        return EphemeralSessionResponse(url="", kind="assigned", expires_at=None)

    # ARCH50-S1:checkout-price-book. Annual / INR / a promo code: priced from the published plan price book,
    # the code reserved under its row lock; what was sold is recorded for revenue metrics. Without them this is
    # the ARCH-29 checkout -- except that a known gateway price must belong to the plan being bought.
    from app.services.billing import payment_gateway as _payment_gateway
    from app.services.revops import checkout as revops_checkout

    prepared = None
    price_id = payload.price_id
    revops_checkout.check_price_id(db, tier_key=payload.quota_tier_key, price_id=price_id)
    if payload.interval or payload.currency or payload.promo_code:
        prepared = revops_checkout.prepare(
            db, organization_id=context.organization_id, tier_key=payload.quota_tier_key,
            interval=payload.interval or "month", currency=payload.currency or "USD", seats=payload.seats,
            price_id=payload.price_id, promo_code=payload.promo_code)
        price_id = prepared.price_id
    try:
        session = portal_service.create_checkout_session(
            db,
            organization_id=context.organization_id,
            quota_tier_key=payload.quota_tier_key,
            seats=payload.seats,
            price_id=price_id,
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
            **({"discount_code": prepared.discount_code} if prepared is not None and prepared.discount_code else {}),
        )
    except CheckoutConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except (portal_service.CheckoutGatewayUnavailableError, GatewayNotConfiguredError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "BILLING_GATEWAY_NOT_CONFIGURED",
                "message": (
                    str(exc)
                    if isinstance(exc, portal_service.CheckoutGatewayUnavailableError)
                    else portal_service.gateway_readiness()
                    or "Paid checkout is not configured in this environment."
                ),
                "details": {"gateway": getattr(exc, "gateway", None)},
            },
        ) from exc
    except _payment_gateway.GatewayTransientError as exc:
        # ARCH50-S1:checkout-gateway-down. The gateway could not be reached (or answered 5xx): nothing was sold,
        # and the promo reservation made above is rolled back with this request -- a 503 to retry, never a 500.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "BILLING_GATEWAY_UNAVAILABLE",
                "message": "The payment provider could not be reached. Nothing was charged; please try again.",
                "details": {"gateway": _payment_gateway.active_gateway_name()},
            },
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
            **({"interval": prepared.price.interval, "currency": prepared.price.currency,
                "promo": (prepared.reservation or {}).get("code")} if prepared is not None else {}),
        },
    )
    if prepared is not None:
        revops_checkout.record(db, prepared, gateway=str(_payment_gateway.active_gateway_name() or "DODO").upper())
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
    except portal_service.PortalGatewayMismatchError as exc:
        # HARDENING-FINAL:billing-B
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except portal_service.CheckoutGatewayUnavailableError as exc:
        # HARDENING-FINAL:billing-B. Same readiness answer checkout gives.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
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
            # ARCH-19 §3.4 — third instance of the same bypass.
            # A portal session minted behind ingress recorded the
            # load balancer as the actor's address.
            "ip_address": _resolve_client_ip(request),
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

# ---------------------------------------------------------------------------
# ARCH50-S1:tenant-revops. A promo code quote before checkout, and the
# organization's invoiced contract (read-only: contracts are drafted, activated
# and invoiced by the operator's RevOps console).
# ---------------------------------------------------------------------------

from app.schemas.revops import PromoQuoteIn, PromoQuoteOut, TenantContractOut  # noqa: E402


@router.post(
    "/organizations/{organization_id}/billing/promo-quote",
    response_model=PromoQuoteOut,
    summary="What a promo code would take off a plan",
)
def quote_promo_code(
    organization_id: uuid.UUID,
    payload: PromoQuoteIn,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgBillingReader),
) -> PromoQuoteOut:
    from app.services.revops import price_books, promos
    from app.services.revops.service import RevOpsError

    price = price_books.resolve_price(db, tier_key=payload.quota_tier_key, interval=payload.interval,
                                      currency=payload.currency)
    if price is None:
        raise RevOpsError(f"the {payload.quota_tier_key} plan is not sold billed {payload.interval}ly in "
                          f"{payload.currency}", "PRICE_NOT_SOLD", 400)
    return PromoQuoteOut(**promos.quote(db, organization_id=context.organization_id, code=payload.code,
                                        tier_key=payload.quota_tier_key, interval=payload.interval,
                                        currency=payload.currency,
                                        list_amount_micros=price.unit_amount_micros * payload.seats))


@router.get(
    "/organizations/{organization_id}/billing/contract",
    response_model=TenantContractOut,
    summary="The organization's invoiced contract and its invoices",
)
def get_billing_contract(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgBillingReader),
) -> TenantContractOut:
    from app.services.revops import checkout as revops_checkout

    return TenantContractOut(contract=revops_checkout.tenant_contract(db, organization_id=context.organization_id))
