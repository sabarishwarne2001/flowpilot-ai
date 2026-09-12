"""ARCH-30 Tranche 3 — anchored patch script for MODIFIED files.

    python scripts/apply_arch30_tranche3.py --check
    python scripts/apply_arch30_tranche3.py

Runs AFTER Tranche 2 and refuses otherwise. Closes what the Tranche 3 audit found:

    T6-F1  schemas declared Stripe ids `str`; Dodo billing endpoints returned 500
    T6-F2  seat sync and drift called Stripe for Dodo subscriptions (NULL id)
    T6-F3  cancelled subscriptions left organizations on the paid tier (Stripe
           and Dodo); `apply_tier_for_status` + arch30_step2 repair
    T6-F4  402 bodies were not the ARCH-01 envelope and every 402 raised the
           quota banner; add-on refusals rendered "Something went wrong"
    T6-F5  read-only billing state (D-11) refused no writes server-side
    T6-F6  renewal-failure grace was invisible; banner only on the Billing page
    T6-F7  D-5: 35 timestamp sites used the browser clock, not the profile
    T6-F8  Tranche 2 copy claimed exports follow the workspace clock; they don't
    T6-F9  verified-domain GRACE/LAPSED emitted events nobody received
    T6-F10 B.1: sign-in without a destination stopped at the picker instead of
           the primary workspace dashboard

Same engine as Tranche 2: idempotent, loud, atomic, BOM- and CRLF-preserving.
Anchors were sliced from the post-Tranche-2 tree by a generator and verified by
--check, a full apply, and a second no-op apply.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent

BOM = b"\xef\xbb\xbf"


def _p(rel: str) -> pathlib.Path:
    return REPO.joinpath(*rel.split("/"))


TRANCHE_2_PRECONDITIONS: tuple[tuple[str, str], ...] = (
    ("backend/app/services/billing/inbound_service.py", 'index_where=text("gateway_event_id IS NOT NULL")'),
    ("backend/app/services/billing/subscription_service.py", "def resolve_pins_for_key("),
    ("backend/app/core/config.py", "BILLING_LAPSED_TIER_KEY"),
    ("frontend/src/pages/organization/OrganizationBranding.tsx", 'useAddonAccess(organizationId, "addon.custom_domain")'),
)

#: Delivered whole. Copy these into place BEFORE running this script.
REQUIRED_NEW_FILES: tuple[str, ...] = (
    "backend/app/core/billing_errors.py",
    "backend/app/api/billing_write_gate.py",
    "backend/app/api/addon_gate.py",
    "backend/app/services/billing/dodo_reconcile_service.py",
    "backend/alembic/versions/arch30_step2_lapsed_tier_repair.py",
    "backend/scripts/verify_arch30_tranche3.py",
    "frontend/src/utils/displayTime.ts",
    "frontend/src/components/common/DisplayPreferencesBoundary.tsx",
    "frontend/src/services/api/entitlements.ts",
    "frontend/src/components/billing/AddOnLockCard.tsx",
)

# Each entry: (path, sentinel, [(old, new, expected_occurrences), ...])
EDITS: list[tuple[pathlib.Path, str, list[tuple[str, str, int]]]] = []

# =============================================================================
# backend/app/core/exception_handlers.py
# =============================================================================
EDITS.append((
    _p('backend/app/core/exception_handlers.py'),
    'details=_exception_details(exc)',
    [
        (
            '''async def domain_exception_handler(request: Request, exc: Exception) -> JSONResponse:
''',
            '''def _exception_details(exc: Exception) -> dict:
    """ARCH-30 Tranche 3. Forward structured context a domain error carries.

    Every domain error previously rendered `details={}`, so an error that knew
    which add-on was missing, or which billing state blocked a write, could
    not tell the console. Only a mapping is forwarded; anything else is not a
    details payload and is dropped rather than stringified.
    """
    from collections.abc import Mapping

    raw = getattr(exc, "details", None)
    return dict(raw) if isinstance(raw, Mapping) else {}


async def domain_exception_handler(request: Request, exc: Exception) -> JSONResponse:
''',
            1,
        ),
        (
            '''            message=str(exc),
            detail=str(exc),
            details={},
        ).model_dump(),
        headers=headers,
''',
            '''            message=str(exc),
            detail=str(exc),
            details=_exception_details(exc),
        ).model_dump(),
        headers=headers,
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/api/deps.py
# =============================================================================
EDITS.append((
    _p('backend/app/api/deps.py'),
    'assert_billing_writes_allowed(',
    [
        (
            '''async def get_organization_context(
    organization_id: uuid.UUID = Path(..., description="Organization identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
) -> OrganizationContext:
    organization = organization_service.get_organization_or_raise(
        db, organization_id=organization_id
    )

    membership = crud.get_organization_member(
        db,
        organization_id=organization.id,
        user_id=current_user.id,
        statuses=ACTIVE_ONLY,
    )
    if membership is None:
        raise OrganizationAccessDeniedError("Organization not found.")

    organization_service.assert_organization_operational(organization)

    return OrganizationContext(
        user=current_user,
        organization=organization,
        membership=membership,
    )


''',
            '''async def get_organization_context(
    organization_id: uuid.UUID = Path(..., description="Organization identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
    request: Request = None,  # type: ignore[assignment]  # injected by FastAPI
) -> OrganizationContext:
    organization = organization_service.get_organization_or_raise(
        db, organization_id=organization_id
    )

    membership = crud.get_organization_member(
        db,
        organization_id=organization.id,
        user_id=current_user.id,
        statuses=ACTIVE_ONLY,
    )
    if membership is None:
        raise OrganizationAccessDeniedError("Organization not found.")

    organization_service.assert_organization_operational(organization)

    # ARCH-30 Tranche 3 (D-11). Read-only billing states refuse writes here,
    # once, for every organization-scoped route.
    from app.api.billing_write_gate import assert_billing_writes_allowed

    assert_billing_writes_allowed(request, db, organization_id=organization.id)

    return OrganizationContext(
        user=current_user,
        organization=organization,
        membership=membership,
    )


''',
            1,
        ),
        (
            '''async def get_sso_compliant_organization_context(
    request: Request,
    organization_id: uuid.UUID = Path(..., description="Organization identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
    token: str = Depends(oauth2_scheme),
) -> OrganizationContext:
    organization = organization_service.get_organization_or_raise(
        db, organization_id=organization_id
    )

    membership = crud.get_organization_member(
        db,
        organization_id=organization.id,
        user_id=current_user.id,
        statuses=ACTIVE_ONLY,
    )
    if membership is None:
        raise OrganizationAccessDeniedError("Organization not found.")

    principal = getattr(request.state, "principal", None) or get_current_principal()
    if not (principal and (principal.kind == "API_KEY" or principal.kind is PrincipalKind.API_KEY)):
        claims = security.decode_access_token_claims(token)
        session_id = claims.session_id if claims else None
        _assert_sso_compliance(
            db,
            organization_id=organization.id,
            membership=membership,
            session_id=session_id,
        )

    organization_service.assert_organization_operational(organization)

    return OrganizationContext(
        user=current_user,
        organization=organization,
        membership=membership,
    )


''',
            '''async def get_sso_compliant_organization_context(
    request: Request,
    organization_id: uuid.UUID = Path(..., description="Organization identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
    token: str = Depends(oauth2_scheme),
) -> OrganizationContext:
    organization = organization_service.get_organization_or_raise(
        db, organization_id=organization_id
    )

    membership = crud.get_organization_member(
        db,
        organization_id=organization.id,
        user_id=current_user.id,
        statuses=ACTIVE_ONLY,
    )
    if membership is None:
        raise OrganizationAccessDeniedError("Organization not found.")

    principal = getattr(request.state, "principal", None) or get_current_principal()
    if not (principal and (principal.kind == "API_KEY" or principal.kind is PrincipalKind.API_KEY)):
        claims = security.decode_access_token_claims(token)
        session_id = claims.session_id if claims else None
        _assert_sso_compliance(
            db,
            organization_id=organization.id,
            membership=membership,
            session_id=session_id,
        )

    organization_service.assert_organization_operational(organization)

    # ARCH-30 Tranche 3 (D-11). Read-only billing states refuse writes here,
    # once, for every organization-scoped route.
    from app.api.billing_write_gate import assert_billing_writes_allowed

    assert_billing_writes_allowed(request, db, organization_id=organization.id)

    return OrganizationContext(
        user=current_user,
        organization=organization,
        membership=membership,
    )


''',
            1,
        ),
        (
            '''    current_user: User = Depends(get_verified_user),
) -> TenantContext:
''',
            '''    current_user: User = Depends(get_verified_user),
    request: Request = None,  # type: ignore[assignment]  # injected by FastAPI
) -> TenantContext:
''',
            1,
        ),
        (
            '''    return TenantContext(
''',
            '''    # ARCH-30 Tranche 3 (D-11). Same gate for every workspace-scoped route.
    from app.api.billing_write_gate import assert_billing_writes_allowed

    assert_billing_writes_allowed(request, db, organization_id=workspace.organization_id)

    return TenantContext(
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/schemas/billing.py
# =============================================================================
EDITS.append((
    _p('backend/app/schemas/billing.py'),
    'gateway_customer_id: Optional[str] = None',
    [
        (
            '''    stripe_customer_id: str
''',
            '''    # ARCH-30 Tranche 3. Optional since the gateway columns: a Dodo-adopted
    # account has no Stripe customer, and `str` made this endpoint a 500.
    stripe_customer_id: Optional[str] = None
    gateway: str = "STRIPE"
    gateway_customer_id: Optional[str] = None
''',
            1,
        ),
        (
            '''    stripe_subscription_id: str
    status: str
''',
            '''    stripe_subscription_id: Optional[str] = None
    gateway: str = "STRIPE"
    gateway_subscription_id: Optional[str] = None
    status: str
''',
            1,
        ),
        (
            '''    trial_end: Optional[datetime] = None
    last_reconciled_at: datetime
''',
            '''    trial_end: Optional[datetime] = None
    grace_ends_at: Optional[datetime] = None
    last_reconciled_at: datetime
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/schemas/invoice.py
# =============================================================================
EDITS.append((
    _p('backend/app/schemas/invoice.py'),
    'subscription_status: Optional[str] = None',
    [
        (
            '''class SubscriptionBrief(BaseModel):
    id: uuid.UUID
    stripe_subscription_id: str
''',
            '''class SubscriptionBrief(BaseModel):
    id: uuid.UUID
    stripe_subscription_id: Optional[str] = None
    gateway: str = "STRIPE"
    gateway_subscription_id: Optional[str] = None
    grace_ends_at: Optional[datetime] = None
''',
            1,
        ),
        (
            '''            stripe_subscription_id=subscription.stripe_subscription_id,
''',
            '''            stripe_subscription_id=subscription.stripe_subscription_id,
            gateway=str(getattr(subscription, "gateway", None) or "STRIPE"),
            gateway_subscription_id=getattr(subscription, "gateway_subscription_id", None),
            grace_ends_at=getattr(subscription, "grace_ends_at", None),
''',
            1,
        ),
        (
            '''    dunning_steps_applied: list[str]
    next_dunning_step: Optional[str] = None
''',
            '''    dunning_steps_applied: list[str]
    next_dunning_step: Optional[str] = None
    # ARCH-30 Tranche 3 (D-11). During a renewal-failure grace the access state
    # is still ACTIVE; these let the console say "payment failed, full access
    # until ..." instead of saying nothing until the day writes stop.
    subscription_status: Optional[str] = None
    grace_ends_at: Optional[datetime] = None
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/api/v1/billing.py
# =============================================================================
EDITS.append((
    _p('backend/app/api/v1/billing.py'),
    'grace_ends_at=live.grace_ends_at if live else None',
    [
        (
            '''    position = dunning_service.position(db, organization_id=context.organization_id)

    return BillingAccessResponse(
''',
            '''    position = dunning_service.position(db, organization_id=context.organization_id)
    live = subscription_service.live_subscription_for_organization(
        db, organization_id=context.organization_id
    )

    return BillingAccessResponse(
''',
            1,
        ),
        (
            '''        next_dunning_step=(
            position.next_step.value if position.next_step else None
        ),
    )
''',
            '''        next_dunning_step=(
            position.next_step.value if position.next_step else None
        ),
        subscription_status=(
            (live.status.value if hasattr(live.status, "value") else str(live.status))
            if live
            else None
        ),
        grace_ends_at=live.grace_ends_at if live else None,
    )
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/billing/seat_service.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/billing/seat_service.py'),
    'set_subscription_seats(\n            subscription_id=subscription.gateway_subscription_id',
    [
        (
            '''    stripe_subscription_id: str
''',
            '''    #: ARCH-30 Tranche 3. The vendor's id, whichever vendor issued it.
    gateway_subscription_id: Optional[str]
''',
            1,
        ),
        (
            '''            "stripe_subscription_id": self.stripe_subscription_id,
''',
            '''            "gateway_subscription_id": self.gateway_subscription_id,
''',
            1,
        ),
        (
            '''        stripe_subscription_id=subscription.stripe_subscription_id,
        seats_billable=billable_seats(db, organization_id=organization_id),
''',
            '''        gateway_subscription_id=subscription.gateway_subscription_id,
        seats_billable=billable_seats(db, organization_id=organization_id),
''',
            1,
        ),
        (
            '''def detect_all_drift(db: Session, *, limit: int = 1000) -> list[SeatDrift]:
    """Every live subscription whose seat count disagrees with the view.

    One query, left-joined against the view, because the alternative — a
    per-organization loop — turns a gate into an N+1 nobody runs often enough
    to be a gate.
    """
    rows = db.execute(
        select(
            BillingAccount.organization_id,
            Subscription.id,
            Subscription.stripe_subscription_id,
            Subscription.seats_purchased,
            func.coalesce(BillableSeat.seats, 0),
        )
        .join(BillingAccount, BillingAccount.id == Subscription.billing_account_id)
        .outerjoin(
            BillableSeat,
            BillableSeat.organization_id == BillingAccount.organization_id,
        )
        .where(Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES))
        .limit(limit)
    ).all()

    drifts = [
        SeatDrift(
            organization_id=organization_id,
            subscription_id=subscription_id,
            stripe_subscription_id=stripe_subscription_id,
            seats_billable=int(seats_billable),
            seats_purchased=int(seats_purchased),
        )
        for (
            organization_id,
            subscription_id,
            stripe_subscription_id,
            seats_purchased,
            seats_billable,
        ) in rows
    ]
    return [drift for drift in drifts if drift.has_drift]


''',
            '''def detect_all_drift(db: Session, *, limit: int = 1000) -> list[SeatDrift]:
    """Every live subscription whose seat count disagrees with the view.

    One query, left-joined against the view, because the alternative — a
    per-organization loop — turns a gate into an N+1 nobody runs often enough
    to be a gate.
    """
    rows = db.execute(
        select(
            BillingAccount.organization_id,
            Subscription.id,
            Subscription.gateway_subscription_id,
            Subscription.seats_purchased,
            func.coalesce(BillableSeat.seats, 0),
        )
        .join(BillingAccount, BillingAccount.id == Subscription.billing_account_id)
        .outerjoin(
            BillableSeat,
            BillableSeat.organization_id == BillingAccount.organization_id,
        )
        .where(Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES))
        .limit(limit)
    ).all()

    drifts = [
        SeatDrift(
            organization_id=organization_id,
            subscription_id=subscription_id,
            gateway_subscription_id=gateway_subscription_id,
            seats_billable=int(seats_billable),
            seats_purchased=int(seats_purchased),
        )
        for (
            organization_id,
            subscription_id,
            gateway_subscription_id,
            seats_purchased,
            seats_billable,
        ) in rows
    ]
    return [drift for drift in drifts if drift.has_drift]


''',
            1,
        ),
        (
            '''        client = gateway or stripe_gateway.get_gateway()
''',
            '''        if gateway is None and subscription.gateway != "STRIPE":
            # ARCH-30 Tranche 3. Only the Stripe adapter offers a preview here;
            # asking Stripe about a Dodo subscription would fail and be
            # reported as Stripe being unreachable.
            raise LookupError(f"no proration preview for {subscription.gateway}")
        client = gateway or stripe_gateway.get_gateway()
''',
            1,
        ),
        (
            '''        reason = (
            "Stripe could not be reached for a proration preview. The figure "
            "is unknown rather than zero; it will be whatever Stripe invoices."
        )
''',
            '''        vendor = "Stripe" if subscription.gateway == "STRIPE" else str(subscription.gateway).title()
        reason = (
            f"A proration preview is not available from {vendor} right now. The "
            f"figure is unknown rather than zero; it will be whatever {vendor} charges."
        )
''',
            1,
        ),
        (
            '''    snapshot = stripe_gateway.get_gateway().set_subscription_seats(
        subscription_id=subscription.stripe_subscription_id,
        seats=seats,
        reason=reason,
    )

    applied = subscription_service.record_seat_count(
        db,
        subscription=subscription,
        seats=snapshot.seats,
        state_version=snapshot.state_version,
    )
''',
            '''    if subscription.gateway == "STRIPE":
        snapshot = stripe_gateway.get_gateway().set_subscription_seats(
            subscription_id=subscription.stripe_subscription_id,
            seats=seats,
            reason=reason,
        )
        synced_seats, state_version = snapshot.seats, snapshot.state_version
    else:
        # ARCH-30 Tranche 3 (D-10). Seats on the live gateway. Previously every
        # Dodo-billed organization reached the Stripe call above with a NULL
        # subscription id: the job failed and added seats were never billed.
        from app.models.quota_tier import QuotaTier
        from app.services.billing.payment_gateway import get_payment_gateway

        tier = db.get(QuotaTier, subscription.quota_tier_id)
        if tier is None or not tier.gateway_price_id:
            raise SeatError(
                f"Subscription {subscription.id} is pinned to a tier with no gateway "
                "price id; the seat count cannot be changed at the gateway."
            )
        dodo_snapshot = get_payment_gateway(subscription.gateway).set_subscription_seats(
            subscription_id=subscription.gateway_subscription_id,
            product_id=tier.gateway_price_id,
            seats=seats,
            proration_mode=str(settings.BILLING_DODO_SEAT_PRORATION_MODE),
        )
        synced_seats, state_version = dodo_snapshot.quantity, dodo_snapshot.state_version

    applied = subscription_service.record_seat_count(
        db,
        subscription=subscription,
        seats=synced_seats,
        state_version=state_version,
    )
''',
            1,
        ),
        (
            '''        "seats_now_purchased": snapshot.seats,
''',
            '''        "seats_now_purchased": synced_seats,
        "gateway": subscription.gateway,
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/billing/dodo_gateway.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/billing/dodo_gateway.py'),
    'def set_subscription_seats(',
    [
        (
            '''            supports_seat_proration=False,
''',
            '''            # ARCH-30 Tranche 3. POST /subscriptions/{id}/change-plan accepts
            # a quantity and a proration mode; `set_subscription_seats` uses it.
            supports_seat_proration=True,
''',
            1,
        ),
        (
            '''            raw=dict(raw),
        )

''',
            '''            raw=dict(raw),
        )

    def set_subscription_seats(
        self,
        *,
        subscription_id: str,
        product_id: str,
        seats: int,
        proration_mode: str,
    ) -> DodoSubscriptionSnapshot:
        """Change the seat quantity on a live subscription, then re-fetch (D-9).

        Dodo's change-plan requires the product id even when only the quantity
        changes; the caller passes the product of the tier the subscription is
        pinned to. The response body is not trusted for state: the subscription
        is re-fetched so `state_version` orders this write against webhooks.
        """
        if int(seats) < 1:
            raise DodoGatewayError("Dodo subscriptions need at least one unit.")
        allowed = {
            "prorated_immediately",
            "full_immediately",
            "difference_immediately",
            "do_not_bill",
        }
        if proration_mode not in allowed:
            raise DodoGatewayError(
                f"Unknown Dodo proration mode {proration_mode!r}; expected one of "
                f"{sorted(allowed)}."
            )
        self._request(
            "POST",
            f"/subscriptions/{quote(str(subscription_id), safe='')}/change-plan",
            {
                "product_id": product_id,
                "quantity": int(seats),
                "proration_billing_mode": proration_mode,
            },
        )
        return self.fetch_subscription(subscription_id)

''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/core/config.py
# =============================================================================
EDITS.append((
    _p('backend/app/core/config.py'),
    'BILLING_DODO_SEAT_PRORATION_MODE',
    [
        (
            '''    BILLING_LAPSED_TIER_KEY: str = "free"
''',
            '''    BILLING_LAPSED_TIER_KEY: str = "free"
    # ARCH-30 Tranche 3. How Dodo bills a mid-cycle seat change. Dodo documents
    # that the three "_immediately" modes charge now and move the renewal date;
    # do_not_bill keeps the date and bills the new quantity at renewal.
    BILLING_DODO_SEAT_PRORATION_MODE: str = "prorated_immediately"
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/billing/subscription_service.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/billing/subscription_service.py'),
    'def apply_tier_for_status(',
    [
        (
            '''from app.models.subscription import (
    LIVE_SUBSCRIPTION_STATUSES,
''',
            '''from app.models.subscription import (
    ENTITLED_SUBSCRIPTION_STATUSES,
    LIVE_SUBSCRIPTION_STATUSES,
''',
            1,
        ),
        (
            '''    if subscription is not None:
        _propagate_tier_to_organization(
            db, account=account, subscription=subscription
        )
''',
            '''    if subscription is not None:
        # ARCH-30 Tranche 3. Previously an unconditional propagation: a
        # cancelled subscription re-pinned the organization to the tier it
        # had stopped paying for.
        apply_tier_for_status(db, account=account, subscription=subscription)
''',
            1,
        ),
        (
            '''def _coerce_status(raw: str) -> SubscriptionStatus:
''',
            '''TERMINAL_SUBSCRIPTION_STATUSES: frozenset[SubscriptionStatus] = frozenset(
    {SubscriptionStatus.CANCELED, SubscriptionStatus.INCOMPLETE_EXPIRED}
)


def apply_tier_for_status(
    db: Session, *, account: BillingAccount, subscription: Subscription
) -> dict[str, Any]:
    """ARCH-30 Tranche 3. What a subscription's status means for the tier pointer.

    `resolve_tier` reads the LIVE subscription's tier, then falls back to
    `organizations.quota_tier_id`. Propagation writes that pointer. Before this
    function it was written on every reconcile regardless of status, so an
    ended subscription left the organization on the paid tier indefinitely.

        entitled (trialing, active, past_due)   pin the subscription's tier
        terminal (canceled, incomplete_expired) release it to the lapsed tier,
                                                but only if this subscription
                                                is what pinned it and no other
                                                subscription is live
        anything else (unpaid, paused, ...)      leave the pointer alone

    The "only if this subscription pinned it" test is the pointer still
    equalling the subscription's tier. An operator who assigned a different
    tier by hand after the cancellation is not overruled by a late webhook.
    """
    raw_status = subscription.status
    # The ORM hands back the enum; `_coerce_status` validates Stripe's strings.
    status = (
        raw_status
        if isinstance(raw_status, SubscriptionStatus)
        else _coerce_status(str(raw_status))
    )
    if status in ENTITLED_SUBSCRIPTION_STATUSES:
        _propagate_tier_to_organization(db, account=account, subscription=subscription)
        return {"tier_action": "pinned"}
    if status not in TERMINAL_SUBSCRIPTION_STATUSES:
        return {"tier_action": "unchanged"}

    other_live = live_subscription_for_organization(
        db, organization_id=account.organization_id
    )
    if other_live is not None and other_live.id != subscription.id:
        return {"tier_action": "another_subscription_is_live"}

    organization = db.get(Organization, account.organization_id)
    if organization is None or organization.quota_tier_id != subscription.quota_tier_id:
        return {"tier_action": "not_pinned_by_this_subscription"}

    from app.services import quota_service

    lapsed_key = str(getattr(settings, "BILLING_LAPSED_TIER_KEY", "free"))
    try:
        tier = quota_service.assign_tier(
            db, organization_id=account.organization_id, tier_key=lapsed_key
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "organization.lapsed_tier_unassignable",
            extra={
                "organization_id": str(account.organization_id),
                "tier_key": lapsed_key,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return {"tier_action": "lapse_failed", "error": f"{type(exc).__name__}: {exc}"}

    logger.warning(
        "organization.quota_tier_lapsed",
        extra={
            "organization_id": str(account.organization_id),
            "from_tier": subscription.quota_tier_key,
            "to_tier": tier.key,
        },
    )
    return {"tier_action": "lapsed", "tier_key": tier.key}


def _coerce_status(raw: str) -> SubscriptionStatus:
''',
            1,
        ),
        (
            '''    "propagate_tier_to_organization",
''',
            '''    "apply_tier_for_status",
    "propagate_tier_to_organization",
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/identity/domain_service.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/identity/domain_service.py'),
    'notify_verified_domain_state(',
    [
        (
            '''                        details={"domain": row.domain, "status": "GRACE"})
''',
            '''                        details={"domain": row.domain, "status": "GRACE"})
            # ARCH-30 Tranche 3. The DNS proof failed; SSO and JIT stop when
            # grace ends. An event nobody subscribes to is not a warning.
            from app.services import organization_notification_service
            organization_notification_service.notify_verified_domain_state(
                db, organization_id=row.organization_id, domain=row.domain,
                phase="GRACE", grace_expires_at=row.grace_expires_at)
''',
            1,
        ),
        (
            '''                                 "effect": "jit_provisioning_blocked"})
''',
            '''                                 "effect": "jit_provisioning_blocked"})
            from app.services import organization_notification_service
            organization_notification_service.notify_verified_domain_state(
                db, organization_id=row.organization_id, domain=row.domain,
                phase="LAPSED", grace_expires_at=None)
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/organization_notification_service.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/organization_notification_service.py'),
    'def notify_verified_domain_state(',
    [
        (
            '''            "change in the audit log."
        ),
        notification_type=NotificationType.SECURITY,
        priority=NotificationPriority.WARNING,
    )
''',
            '''            "change in the audit log."
        ),
        notification_type=NotificationType.SECURITY,
        priority=NotificationPriority.WARNING,
    )


def notify_verified_domain_state(
    db: Session,
    *,
    organization_id: uuid.UUID,
    domain: str,
    phase: str,
    grace_expires_at=None,
) -> int:
    """ARCH-30 Tranche 3. A verified identity domain failed its DNS re-check."""
    if phase == "GRACE":
        until = (
            grace_expires_at.strftime("%d %b %Y") + " (UTC)"
            if grace_expires_at is not None
            else "the end of the grace period"
        )
        title = f"Domain verification failing for {domain}"
        message = (
            f"We could not confirm the DNS record that verifies {domain}. Single "
            f"sign-on and automatic provisioning keep working until {until}. "
            "Restore the TXT record to keep them running."
        )
    else:
        title = f"{domain} is no longer verified"
        message = (
            f"The DNS record for {domain} was not restored in time. New members "
            "can no longer be provisioned from your identity provider for this "
            "domain. Existing members keep their access. Re-verify the domain in "
            "Identity settings to restore provisioning."
        )
    return emit_to_roles(
        db,
        organization_id=organization_id,
        roles=SECURITY_ROLES,
        title=title,
        message=message,
        notification_type=NotificationType.SECURITY,
        priority=NotificationPriority.WARNING,
    )
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/services/api/client.ts
# =============================================================================
EDITS.append((
    _p('frontend/src/services/api/client.ts'),
    'parsed.code === "BILLING_READ_ONLY"',
    [
        (
            '''    if (status === 402) {
      // Held, not retried. See the module header.
''',
            '''    if (status === 402) {
      // ARCH-30 Tranche 3. Two 402s are about what the organization pays for,
      // not a usage ceiling. They carry their own message and details for the
      // page that asked, and must not raise the global quota banner.
      if (parsed.code === "ADDON_REQUIRED" || parsed.code === "BILLING_READ_ONLY") {
        return reject();
      }

      // Held, not retried. See the module header.
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/types/billing.ts
# =============================================================================
EDITS.append((
    _p('frontend/src/types/billing.ts'),
    'readonly grace_ends_at: string | null;',
    [
        (
            '''  readonly stripe_subscription_id: string;
  readonly status: string;
''',
            '''  /** Null for subscriptions issued by a gateway other than Stripe. */
  readonly stripe_subscription_id: string | null;
  readonly gateway: string;
  readonly gateway_subscription_id: string | null;
  readonly grace_ends_at: string | null;
  readonly status: string;
''',
            1,
        ),
        (
            '''  readonly dunning_steps_applied: readonly string[];
  readonly next_dunning_step: string | null;
}
''',
            '''  readonly dunning_steps_applied: readonly string[];
  readonly next_dunning_step: string | null;
  /** ARCH-30 Tranche 3 (D-11). */
  readonly subscription_status: string | null;
  readonly grace_ends_at: string | null;
}
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/components/billing/DunningBanner.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/components/billing/DunningBanner.tsx'),
    'access.subscription_status === "past_due"',
    [
        (
            '''import { pollUnlessRefused } from "@/services/api/polling";
''',
            '''import { pollUnlessRefused } from "@/services/api/polling";
import { formatTimestampDate } from "@/utils/displayTime";
''',
            1,
        ),
        (
            '''  if (!access || HEALTHY_STATES.has(access.access_state.toUpperCase())) {
    return null;
  }
''',
            '''  if (!access) {
    return null;
  }

  if (HEALTHY_STATES.has(access.access_state.toUpperCase())) {
    // ARCH-30 Tranche 3 (D-11). A failed renewal inside its grace window keeps
    // full access, so the access state is healthy — but the customer needs to
    // hear about it now, not on the day writes stop.
    if (access.subscription_status === "past_due" && access.grace_ends_at) {
      return (
        <div
          role="status"
          className={`border-b border-amber-500/40 bg-amber-500/10 px-4 py-3 ${className}`}
        >
          <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3">
            <AlertOctagon className="h-5 w-5 shrink-0 text-amber-600" aria-hidden="true" />
            <p className="min-w-0 flex-1 text-sm text-foreground">
              <span className="font-semibold">Payment failed.</span> Full access continues
              until {formatTimestampDate(access.grace_ends_at)} while the payment is retried.
              {canManageBilling
                ? " Update the payment method to avoid interruption."
                : " Ask an owner or billing admin to update the payment method."}
            </p>
            {canManageBilling ? (
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm font-semibold hover:bg-muted disabled:opacity-60"
                disabled={portal.isPending}
                onClick={() => portal.mutate()}
              >
                {portal.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <ExternalLink className="h-4 w-4" aria-hidden="true" />
                )}
                Update payment method
              </button>
            ) : null}
          </div>
        </div>
      );
    }
    return null;
  }
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/layouts/OrganizationLayout.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/layouts/OrganizationLayout.tsx'),
    '<DisplayPreferencesBoundary>',
    [
        (
            '''import { useResolvedOrganization } from "@/routes/OrganizationGuard";
''',
            '''import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import DunningBanner from "@/components/billing/DunningBanner";
import { DisplayPreferencesBoundary } from "@/components/common/DisplayPreferencesBoundary";
''',
            1,
        ),
        (
            '''  const { organization, organizationId } = useResolvedOrganization();
''',
            '''  const { organization, organizationId, organizationRole } = useResolvedOrganization();
  // ARCH-30 Tranche 3 (D-11). Billing state is visible wherever the tenant
  // works, not only on the Billing page. Limited to roles that can read billing
  // so members do not generate a denied request on every navigation.
  const canSeeBilling = ["OWNER", "ADMIN", "BILLING"].includes(String(organizationRole).toUpperCase());
''',
            1,
        ),
        (
            '''        <main className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 lg:p-6">
          <Outlet />
        </main>
''',
            '''        {canSeeBilling ? (
          <DunningBanner organizationId={organizationId} canManageBilling />
        ) : null}

        <main className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 lg:p-6">
          <DisplayPreferencesBoundary>
            <Outlet />
          </DisplayPreferencesBoundary>
        </main>
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/layouts/DashboardLayout.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/layouts/DashboardLayout.tsx'),
    '<DisplayPreferencesBoundary>',
    [
        (
            '''import PendingInvitationsBanner from "@/components/invitations/PendingInvitationsBanner";
''',
            '''import PendingInvitationsBanner from "@/components/invitations/PendingInvitationsBanner";
import DunningBanner from "@/components/billing/DunningBanner";
import { DisplayPreferencesBoundary } from "@/components/common/DisplayPreferencesBoundary";
import { useTenant } from "@/hooks/useTenant";
''',
            1,
        ),
        (
            '''  const isSidebarCollapsed = useUIStore((state) => state.isSidebarCollapsed);
''',
            '''  const isSidebarCollapsed = useUIStore((state) => state.isSidebarCollapsed);
  const { state: tenantState } = useTenant();
  const billingOrganizationId =
    tenantState.status === "ready" &&
    ["OWNER", "ADMIN", "BILLING"].includes(String(tenantState.organizationRole).toUpperCase())
      ? tenantState.organization.organization_id
      : null;
''',
            1,
        ),
        (
            '''        <VerificationBanner />

        <main className="flex-1 overflow-y-auto bg-muted/10 dark:bg-background p-3 sm:p-4 md:p-6">
          <Outlet />
        </main>
''',
            '''        <VerificationBanner />
        {billingOrganizationId ? (
          <DunningBanner organizationId={billingOrganizationId} canManageBilling />
        ) : null}

        <main className="flex-1 overflow-y-auto bg-muted/10 dark:bg-background p-3 sm:p-4 md:p-6">
          <DisplayPreferencesBoundary>
            <Outlet />
          </DisplayPreferencesBoundary>
        </main>
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/billing/BillingHub.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/billing/BillingHub.tsx'),
    'rendered by the organization layout',
    [
        (
            '''import DunningBanner from "@/components/billing/DunningBanner";
''',
            '''''',
            1,
        ),
        (
            '''      <DunningBanner
        organizationId={organizationId}
        canManageBilling={canManageBilling}
      />
''',
            '''      {/* The dunning banner is rendered by the organization layout (ARCH-30 Tranche 3). */}
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/organization/OrganizationBranding.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/organization/OrganizationBranding.tsx'),
    'error instanceof ApiError',
    [
        (
            '''import { useAddonAccess } from "@/hooks/useAddonAccess";
''',
            '''import { useAddonAccess } from "@/hooks/useAddonAccess";
import { ApiError } from "@/services/api/errors";
import { addonRequiredDetail } from "@/services/api/entitlements";
''',
            1,
        ),
        (
            '''import { brandingKeys } from "@/services/api/queryKeys";
''',
            '''import { brandingKeys, entitlementKeys } from "@/services/api/queryKeys";
''',
            1,
        ),
        (
            '''const errorMessage = (error: unknown): string => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
''',
            '''const errorMessage = (error: unknown): string => {
  // ARCH-30 Tranche 3. The API client rejects with ApiError; the branch below
  // this one never matched, so every refusal read "Something went wrong".
  if (error instanceof ApiError) {
    return error.message;
  }
  const detail = (error as { response?: { data?: { detail?: unknown } } })
''',
            1,
        ),
        (
            '''    setProblem(errorMessage(error));
''',
            '''    setProblem(errorMessage(error));
    // An add-on refusal means the lock state we rendered is stale.
    if (addonRequiredDetail(error)) {
      void queryClient.invalidateQueries({ queryKey: entitlementKeys.all(organizationId) });
    }
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/organization/OrganizationAnalytics.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/organization/OrganizationAnalytics.tsx'),
    'error instanceof ApiError',
    [
        (
            '''import { useAddonAccess } from "@/hooks/useAddonAccess";
''',
            '''import { useAddonAccess } from "@/hooks/useAddonAccess";
import { ApiError } from "@/services/api/errors";
''',
            1,
        ),
        (
            '''const errorMessage = (error: unknown): string => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
''',
            '''const errorMessage = (error: unknown): string => {
  // ARCH-30 Tranche 3. The API client rejects with ApiError; the branch below
  // this one never matched, so every refusal read "Something went wrong".
  if (error instanceof ApiError) {
    return error.message;
  }
  const detail = (error as { response?: { data?: { detail?: unknown } } })
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/Settings/Workspace.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/Settings/Workspace.tsx'),
    'Organization-wide warehouse exports',
    [
        (
            '''                Scheduled exports and automation schedules run on this clock.
''',
            '''                The workspace clock. Organization-wide warehouse exports still run on UTC.
''',
            1,
        ),
        (
            '''import React, { useEffect, useMemo, useRef, useState } from "react";
''',
            '''import { formatTimestamp } from "@/utils/displayTime";
import React, { useEffect, useMemo, useRef, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(inv.expires_at).toLocaleString()''',
            '''formatTimestamp(inv.expires_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/utils/formatters.ts
# =============================================================================
EDITS.append((
    _p('frontend/src/utils/formatters.ts'),
    'from "@/utils/displayTime"',
    [
        (
            '''/**
 * Format dates using the user's locale.
 */
''',
            '''import { formatTimestamp } from "@/utils/displayTime";

/**
 * Format dates in the reader's profile timezone and language (ARCH-30 D-5).
 */
''',
            1,
        ),
        (
            '''    return new Intl.DateTimeFormat(
      navigator.language,
      {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      },
    ).format(date);
''',
            '''    return formatTimestamp(date, "Invalid Date");
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/Tenant/WorkspacePicker.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/Tenant/WorkspacePicker.tsx'),
    'get("landing") === "1"',
    [
        (
            '''  const organizations =
    state.status === "ready" || state.status === "no_workspace"
''',
            '''  // ARCH-30 Tranche 3 (B.1). Sign-in without a destination lands on the
  // primary workspace dashboard: the one tenant resolution already selected
  // (last used, else first). A person who opens /workspaces deliberately to
  // switch still gets the picker, because only sign-in adds `landing=1`.
  if (new URLSearchParams(location.search).get("landing") === "1" && state.status === "ready") {
    return (
      <Navigate
        to={workspacePath(state.organization.organization_slug, state.workspace.slug)}
        replace
      />
    );
  }

  const organizations =
    state.status === "ready" || state.status === "no_workspace"
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/Auth/SsoComplete.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/Auth/SsoComplete.tsx'),
    '?landing=1',
    [
        (
            '''      : ROUTES.WORKSPACES;
''',
            '''      : `${ROUTES.WORKSPACES}?landing=1`;
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/Auth/Login.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/Auth/Login.tsx'),
    '?landing=1',
    [
        (
            '''      : ROUTES.WORKSPACES;
''',
            '''      : `${ROUTES.WORKSPACES}?landing=1`;
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/components/assistant/ChatBubble.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/components/assistant/ChatBubble.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useState } from "react";
''',
            '''import { formatTimestamp, formatTimestampTime } from "@/utils/displayTime";
import React, { useState } from "react";
''',
            1,
        ),
        (
            '''new Date(message.created_at).toLocaleString()''',
            '''formatTimestamp(message.created_at)''',
            1,
        ),
        (
            '''new Date(message.created_at).toLocaleTimeString([], ''',
            '''formatTimestampTime(message.created_at, ''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/components/billing/SupplierInvoicePanels.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/components/billing/SupplierInvoicePanels.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useState } from "react";
''',
            '''import { formatTimestamp } from "@/utils/displayTime";
import React, { useState } from "react";
''',
            1,
        ),
        (
            '''new Date(value).toLocaleString()''',
            '''formatTimestamp(value)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/components/invitations/PendingInvitationsBanner.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/components/invitations/PendingInvitationsBanner.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React from "react";
''',
            '''import { formatTimestampDate } from "@/utils/displayTime";
import React from "react";
''',
            1,
        ),
        (
            '''new Date(invitation.expires_at).toLocaleDateString()''',
            '''formatTimestampDate(invitation.expires_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/components/organization/AuditDetailInspector.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/components/organization/AuditDetailInspector.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React from "react";
''',
            '''import { formatTimestamp } from "@/utils/displayTime";
import React from "react";
''',
            1,
        ),
        (
            '''new Date(entry.created_at).toLocaleString()''',
            '''formatTimestamp(entry.created_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/components/organization/IncomingOwnershipBanner.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/components/organization/IncomingOwnershipBanner.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useMemo, useState } from "react";
''',
            '''import { formatTimestampDate } from "@/utils/displayTime";
import React, { useMemo, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(transfer.expires_at).toLocaleDateString()''',
            '''formatTimestampDate(transfer.expires_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/components/organization/OrganizationDetailPanel.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/components/organization/OrganizationDetailPanel.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React from "react";
''',
            '''import { formatTimestampDate } from "@/utils/displayTime";
import React from "react";
''',
            1,
        ),
        (
            '''new Date(value).toLocaleDateString()''',
            '''formatTimestampDate(value)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/components/organization/OwnershipTransferPanel.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/components/organization/OwnershipTransferPanel.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useMemo, useState } from "react";
''',
            '''import { formatTimestampDate } from "@/utils/displayTime";
import React, { useMemo, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(transfer.created_at).toLocaleDateString()''',
            '''formatTimestampDate(transfer.created_at)''',
            1,
        ),
        (
            '''new Date(transfer.expires_at).toLocaleDateString()''',
            '''formatTimestampDate(transfer.expires_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/Automation/ExecutionTimeline.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/Automation/ExecutionTimeline.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useMemo, useState } from "react";
''',
            '''import { formatTimestamp } from "@/utils/displayTime";
import React, { useMemo, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(chain.startedAt).toLocaleString()''',
            '''formatTimestamp(chain.startedAt)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/Settings/EmailChangePanel.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/Settings/EmailChangePanel.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useState } from "react";
''',
            '''import { formatTimestamp } from "@/utils/displayTime";
import React, { useState } from "react";
''',
            1,
        ),
        (
            '''new Date(pending.expiresAt).toLocaleString()''',
            '''formatTimestamp(pending.expiresAt)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/Settings/SessionManagement.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/Settings/SessionManagement.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useMemo, useState } from "react";
''',
            '''import { formatTimestampDate } from "@/utils/displayTime";
import React, { useMemo, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(value).toLocaleDateString()''',
            '''formatTimestampDate(value)''',
            1,
        ),
        (
            '''new Date(session.created_at).toLocaleDateString()''',
            '''formatTimestampDate(session.created_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/admin/AuditExplorer.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/admin/AuditExplorer.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useCallback, useState } from "react";
''',
            '''import { formatTimestamp } from "@/utils/displayTime";
import React, { useCallback, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(row.created_at).toLocaleString()''',
            '''formatTimestamp(row.created_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/billing/InvoiceBrowser.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/billing/InvoiceBrowser.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useState } from "react";
''',
            '''import { formatTimestampDate } from "@/utils/displayTime";
import React, { useState } from "react";
''',
            1,
        ),
        (
            '''new Date(invoice.period_start).toLocaleDateString()''',
            '''formatTimestampDate(invoice.period_start)''',
            1,
        ),
        (
            '''new Date(invoice.period_end).toLocaleDateString()''',
            '''formatTimestampDate(invoice.period_end)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/billing/SeatManager.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/billing/SeatManager.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useState } from "react";
''',
            '''import { formatTimestampDate } from "@/utils/displayTime";
import React, { useState } from "react";
''',
            1,
        ),
        (
            '''new Date(
                state.subscription.current_period_end,
              ).toLocaleDateString()''',
            '''formatTimestampDate(state.subscription.current_period_end,)''',
            1,
        ),
        (
            '''new Date(state.delinquent_since).toLocaleDateString()''',
            '''formatTimestampDate(state.delinquent_since)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/billing/UsageDashboard.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/billing/UsageDashboard.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useMemo, useState } from "react";
''',
            '''import { formatTimestamp, formatTimestampDate } from "@/utils/displayTime";
import React, { useMemo, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(summary.period_start).toLocaleDateString()''',
            '''formatTimestampDate(summary.period_start)''',
            1,
        ),
        (
            '''new Date(summary.period_end).toLocaleDateString()''',
            '''formatTimestampDate(summary.period_end)''',
            1,
        ),
        (
            '''new Date(summary.sealed_at).toLocaleDateString()''',
            '''formatTimestampDate(summary.sealed_at)''',
            1,
        ),
        (
            '''new Date(bucket.bucket_start).toLocaleString()''',
            '''formatTimestamp(bucket.bucket_start)''',
            1,
        ),
        (
            '''new Date(limit.resets_at).toLocaleDateString()''',
            '''formatTimestampDate(limit.resets_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/identity/DomainManager.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/identity/DomainManager.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useCallback, useState } from "react";
''',
            '''import { formatTimestamp } from "@/utils/displayTime";
import React, { useCallback, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(
                            domain.challenge_expires_at,
                          ).toLocaleString()''',
            '''formatTimestamp(domain.challenge_expires_at,)''',
            1,
        ),
        (
            '''new Date(domain.last_checked_at).toLocaleString()''',
            '''formatTimestamp(domain.last_checked_at)''',
            1,
        ),
        (
            '''new Date(domain.grace_expires_at).toLocaleString()''',
            '''formatTimestamp(domain.grace_expires_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/identity/ScimTokenManager.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/identity/ScimTokenManager.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useCallback, useState } from "react";
''',
            '''import { formatTimestamp } from "@/utils/displayTime";
import React, { useCallback, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(identity.last_synced_at).toLocaleString()''',
            '''formatTimestamp(identity.last_synced_at)''',
            1,
        ),
        (
            '''new Date(scimKey.last_used_at).toLocaleString()''',
            '''formatTimestamp(scimKey.last_used_at)''',
            1,
        ),
        (
            '''new Date(
                scimKey.previous_secret_expires_at as string,
              ).toLocaleString()''',
            '''formatTimestamp(scimKey.previous_secret_expires_at as string,)''',
            1,
        ),
        (
            '''new Date(scimKey.previous_last_used_at).toLocaleString()''',
            '''formatTimestamp(scimKey.previous_last_used_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/marketplace/MarketplaceCatalog.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/marketplace/MarketplaceCatalog.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import { useState } from "react";
''',
            '''import { formatTimestampDate } from "@/utils/displayTime";
import { useState } from "react";
''',
            1,
        ),
        (
            '''new Date(
                        installation.installed_at,
                      ).toLocaleDateString()''',
            '''formatTimestampDate(installation.installed_at,)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/organization/OrganizationApiKeys.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/organization/OrganizationApiKeys.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useMemo, useState } from "react";
''',
            '''import { formatTimestamp, formatTimestampDate } from "@/utils/displayTime";
import React, { useMemo, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(key.created_at).toLocaleDateString()''',
            '''formatTimestampDate(key.created_at)''',
            1,
        ),
        (
            '''new Date(key.last_used_at).toLocaleDateString()''',
            '''formatTimestampDate(key.last_used_at)''',
            1,
        ),
        (
            '''new Date(key.expires_at).toLocaleDateString()''',
            '''formatTimestampDate(key.expires_at)''',
            1,
        ),
        (
            '''new Date(key.previous_secret_expires_at!).toLocaleString()''',
            '''formatTimestamp(key.previous_secret_expires_at!)''',
            1,
        ),
        (
            '''new Date(key.previous_secret_expires_at).toLocaleString()''',
            '''formatTimestamp(key.previous_secret_expires_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/organization/OrganizationBYOK.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/organization/OrganizationBYOK.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useMemo, useState } from "react";
''',
            '''import { formatTimestamp } from "@/utils/displayTime";
import React, { useMemo, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(value).toLocaleString()''',
            '''formatTimestamp(value)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/organization/OrganizationCompliance.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/organization/OrganizationCompliance.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useCallback,  useMemo, useState  } from "react";
''',
            '''import { formatTimestamp } from "@/utils/displayTime";
import React, { useCallback,  useMemo, useState  } from "react";
''',
            1,
        ),
        (
            '''new Date(value).toLocaleString()''',
            '''formatTimestamp(value)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/organization/OrganizationDeveloperPortal.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/organization/OrganizationDeveloperPortal.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useMemo, useState } from "react";
''',
            '''import { formatTimestampDate } from "@/utils/displayTime";
import React, { useMemo, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(apiKey.last_used_at).toLocaleDateString()''',
            '''formatTimestampDate(apiKey.last_used_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/organization/OrganizationMembers.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/organization/OrganizationMembers.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useMemo, useState } from "react";
''',
            '''import { formatTimestampDate } from "@/utils/displayTime";
import React, { useMemo, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(member.deactivated_at).toLocaleDateString()''',
            '''formatTimestampDate(member.deactivated_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/organization/OrganizationNotifications.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/organization/OrganizationNotifications.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useState } from "react";
''',
            '''import { formatTimestamp } from "@/utils/displayTime";
import React, { useState } from "react";
''',
            1,
        ),
        (
            '''new Date(notification.created_at).toLocaleString()''',
            '''formatTimestamp(notification.created_at)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/organization/OrganizationWebhooks.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/organization/OrganizationWebhooks.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import React, { useMemo, useState } from "react";
''',
            '''import { formatTimestamp, formatTimestampDate } from "@/utils/displayTime";
import React, { useMemo, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(delivery.created_at).toLocaleString()''',
            '''formatTimestamp(delivery.created_at)''',
            1,
        ),
        (
            '''new Date(attempt.attempted_at).toLocaleString()''',
            '''formatTimestamp(attempt.attempted_at)''',
            1,
        ),
        (
            '''new Date(revealed.validUntil).toLocaleString()''',
            '''formatTimestamp(revealed.validUntil)''',
            1,
        ),
        (
            '''new Date(endpoint.last_success_at).toLocaleDateString()''',
            '''formatTimestampDate(endpoint.last_success_at)''',
            1,
        ),
        (
            '''new Date(endpoint.disabled_at).toLocaleString()''',
            '''formatTimestamp(endpoint.disabled_at)''',
            1,
        ),
        (
            '''new Date(endpoint.rotation_overlap_until!).toLocaleString()''',
            '''formatTimestamp(endpoint.rotation_overlap_until!)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/partner/PartnerPortal.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/partner/PartnerPortal.tsx'),
    'from "@/utils/displayTime";',
    [
        (
            '''import { useMemo, useState } from "react";
''',
            '''import { formatTimestampDate } from "@/utils/displayTime";
import { useMemo, useState } from "react";
''',
            1,
        ),
        (
            '''new Date(entry.effective_from).toLocaleDateString()''',
            '''formatTimestampDate(entry.effective_from)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/types/analytics.ts
# =============================================================================
EDITS.append((
    _p('frontend/src/types/analytics.ts'),
    'from "@/utils/displayTime";',
    [
        (
            '''/**
 * ARCH-26 — Enterprise Analytics, BI Egress & Warehouse Sync.
''',
            '''import { formatTimestamp } from "@/utils/displayTime";
/**
 * ARCH-26 — Enterprise Analytics, BI Egress & Warehouse Sync.
''',
            1,
        ),
        (
            '''new Date(value).toLocaleString()''',
            '''formatTimestamp(value)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/types/branding.ts
# =============================================================================
EDITS.append((
    _p('frontend/src/types/branding.ts'),
    'from "@/utils/displayTime";',
    [
        (
            '''/**
 * ARCH-25 — white-label, custom domains and tenant branding.
''',
            '''import { formatTimestamp } from "@/utils/displayTime";
/**
 * ARCH-25 — white-label, custom domains and tenant branding.
''',
            1,
        ),
        (
            '''new Date(value).toLocaleString()''',
            '''formatTimestamp(value)''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/types/planEntitlements.ts
# =============================================================================
EDITS.append((
    _p('frontend/src/types/planEntitlements.ts'),
    '"addon.custom_domain": {',
    [
        (
            '''  | "llm.platform_key"
''',
            '''  | "llm.platform_key"
  | "addon.custom_domain"
  | "addon.warehouse_sync"
''',
            1,
        ),
        (
            '''    label: "AI included (no provider key needed)",
    unit: "none",
    capability: true,
  },
''',
            '''    label: "AI included (no provider key needed)",
    unit: "none",
    capability: true,
  },
  "addon.custom_domain": {
    // ARCH-30 D-8. A capability: presence on a tier is the grant.
    label: "Custom domains included",
    unit: "none",
    capability: true,
  },
  "addon.warehouse_sync": {
    label: "Warehouse sync included",
    unit: "none",
    capability: true,
  },
''',
            1,
        ),
    ],
))




# =============================================================================
# Engine
# =============================================================================

def _first_line(fragment: str) -> str:
    for line in fragment.splitlines():
        if line.strip():
            return line.strip()
    return fragment[:60]


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-30 Tranche 3 patch script")
    parser.add_argument(
        "--check", action="store_true",
        help="validate every anchor and report, without writing",
    )
    args = parser.parse_args()

    planned: list[tuple[pathlib.Path, bool, bool, str]] = []
    skipped: list[pathlib.Path] = []
    errors: list[str] = []

    for rel, sentinel in TRANCHE_2_PRECONDITIONS:
        target = _p(rel)
        if not target.exists() or sentinel not in target.read_bytes().decode("utf-8-sig"):
            errors.append(f"{rel}: Tranche 2 is not applied (missing {sentinel!r}) — run apply_arch30_tranche2.py first")

    for rel in REQUIRED_NEW_FILES:
        if not _p(rel).exists():
            errors.append(f"{rel}: required new file is missing — copy the delivered files first")

    for path, sentinel, replacements in EDITS:
        rel = path.relative_to(REPO)
        if not path.exists():
            errors.append(f"{rel}: file not found")
            continue

        raw = path.read_bytes()
        has_bom = raw.startswith(BOM)
        original = raw.decode("utf-8-sig")
        crlf = "\r\n" in original
        text = original.replace("\r\n", "\n")

        if sentinel in text:
            skipped.append(path)
            continue

        updated = text
        failed = False
        for old, new, expected in replacements:
            found = updated.count(old)
            if found != expected:
                errors.append(
                    f"{rel}: anchor expected {expected}x, found {found}x — "
                    f"{_first_line(old)!r}"
                )
                failed = True
                break
            updated = updated.replace(old, new)

        if failed:
            continue

        if sentinel not in updated:
            errors.append(
                f"{rel}: sentinel {sentinel!r} absent after applying edits; the "
                f"edit set is malformed and would re-apply on every run"
            )
            continue

        planned.append((path, has_bom, crlf, updated))

    print("=" * 78)
    print("ARCH-30 TRANCHE 3 — PATCH")
    print("=" * 78)

    for path in skipped:
        print(f"[SKIP] {path.relative_to(REPO)}  (already applied)")

    if errors:
        for message in errors:
            print(f"[FAIL] {message}")
        print("-" * 78)
        print("ABORTED — no file was written.")
        return 1

    for path, _, crlf, _ in planned:
        verb = "WOULD" if args.check else " OK "
        suffix = "  (CRLF preserved)" if crlf else ""
        print(f"[{verb}] {path.relative_to(REPO)}{suffix}")

    if args.check:
        print("-" * 78)
        print(f"check: {len(planned)} to apply, {len(skipped)} already applied")
        return 0

    for path, has_bom, crlf, updated in planned:
        text = updated.replace("\n", "\r\n") if crlf else updated
        payload = text.encode("utf-8")
        path.write_bytes((BOM + payload) if has_bom else payload)

    print("-" * 78)
    print(f"{len(planned)} applied, {len(skipped)} already applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())

