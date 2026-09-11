"""ARCH-30 Tranche 2 (D-8) — add-on entitlements for the console.

    GET  /organizations/{id}/entitlements                          [any role]
    POST /organizations/{id}/billing/addons/{key}/checkout-session [OWNER]

The GET is readable by every role because a member who opens the analytics
page deserves to see "this is an add-on" rather than an empty table, and the
answer reveals nothing a plan card does not. The POST is OWNER because it
starts a purchase.

Success and cancel URLs are not accepted from the client. A caller-supplied
return URL on a payment flow is an open redirect with a trusted domain in
front of it; the deployment's configured URLs are the only ones used.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import OrganizationContext, RequireOrgOwner, RequireOrgRole, get_db
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.organization import OrganizationRole
from app.schemas.entitlements import AddonAccessResponse, OrganizationEntitlementsResponse
from app.schemas.invoice import EphemeralSessionResponse
from app.services import audit_service
from app.services.billing import addon_service, entitlement_service
from app.services.billing.portal_service import CheckoutConfigurationError

logger = logging.getLogger("app.api.v1.entitlements")

router = APIRouter(tags=["Entitlements"])

RequireAnyOrgRole = RequireOrgRole(
    [
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
        OrganizationRole.BILLING,
        OrganizationRole.MEMBER,
    ]
)


def _assert_scope(context: OrganizationContext, organization_id: uuid.UUID) -> None:
    if context.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found."
        )


@router.get(
    "/organizations/{organization_id}/entitlements",
    response_model=OrganizationEntitlementsResponse,
    summary="Add-on access for this organization",
)
def get_entitlements(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireAnyOrgRole),
) -> OrganizationEntitlementsResponse:
    _assert_scope(context, organization_id)
    now = datetime.now(timezone.utc)

    addons: list[AddonAccessResponse] = []
    for key, offer in entitlement_service.ADDON_CATALOG.items():
        access = entitlement_service.addon_access(
            db, organization_id=organization_id, addon_key=key, now=now
        )
        addons.append(
            AddonAccessResponse(
                addon_key=key,
                display_name=offer.display_name,
                description=offer.description,
                state=access.state,
                source=access.source,
                grace_ends_at=access.grace_ends_at,
                can_create=access.can_create,
                can_maintain=access.can_maintain,
                live_resource_count=access.live_resource_count,
                halt_effect=offer.halt_effect,
                monthly_price_micros=offer.monthly_price_micros,
                currency=offer.currency,
                purchasable=entitlement_service.purchasable(key),
                included_in=entitlement_service.tiers_including(db, key),
            )
        )

    return OrganizationEntitlementsResponse(
        organization_id=organization_id, as_of=now, addons=addons
    )


@router.post(
    "/organizations/{organization_id}/billing/addons/{addon_key}/checkout-session",
    response_model=EphemeralSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a checkout for one add-on",
)
def create_addon_checkout_session(
    organization_id: uuid.UUID,
    addon_key: str,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> EphemeralSessionResponse:
    _assert_scope(context, organization_id)
    if addon_key not in entitlement_service.ADDON_CATALOG:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Add-on not found.")

    try:
        session = addon_service.create_addon_checkout(
            db, organization_id=organization_id, addon_key=addon_key
        )
    except (CheckoutConfigurationError, addon_service.AddonAlreadyGrantedError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.BILLING_ACCOUNT,
        action=AuditAction.CHECKOUT_STARTED,
        details={
            "addon_key": addon_key,
            "gateway_session_id": session.stripe_session_id,
            "url_persisted": False,
        },
    )
    db.commit()

    return EphemeralSessionResponse(
        url=session.url, kind=session.kind, expires_at=session.expires_at
    )


__all__ = ["router"]
