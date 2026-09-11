"""ARCH-30 Tranche 2 (D-8) — the request-path refusal for add-on endpoints.

Lives in the API layer because it speaks HTTP. The decision itself is
`entitlement_service.addon_access`; this module only turns "not permitted"
into a 402 the console can render, and writes the refusal to the audit log in
its own transaction so the rollback that follows the exception cannot erase it.

WHY 402
=======

403 already means "your role may not do this" everywhere in this API, and the
console treats it as a permissions problem. An owner refused a custom domain
has the right role and the wrong plan. 402 Payment Required is the status that
says so, and the structured body carries what the lock card needs.

TWO POLICIES, NAMED AT EACH CALL SITE
=====================================

    allow_grace=False   creating something new — a hostname, a destination,
                        a schedule. Needs the add-on ACTIVE.
    allow_grace=True    keeping existing resources working — re-verifying,
                        renewing a certificate, testing or running an export.
                        Permitted during the D-6 grace.

Revoking, releasing and deleting are never gated. A tenant must always be able
to remove what it no longer pays for.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core import entitlements
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.services import audit_service
from app.services.billing import entitlement_service

ADDON_REQUIRED_CODE = "ADDON_REQUIRED"

_RESOURCE_TYPES = {
    entitlements.CUSTOM_DOMAIN_ADDON: AuditResourceType.CUSTOM_DOMAIN,
    entitlements.WAREHOUSE_SYNC_ADDON: AuditResourceType.WAREHOUSE_DESTINATION,
}


def require_addon(
    db: Session,
    *,
    context: Any,
    addon_key: str,
    operation: str,
    allow_grace: bool,
) -> entitlement_service.AddonAccess:
    access = entitlement_service.addon_access(
        db, organization_id=context.organization_id, addon_key=addon_key
    )
    permitted = access.can_maintain if allow_grace else access.can_create
    if permitted:
        return access

    offer = entitlement_service.offer_for(addon_key)
    audit_service.record_independently(
        organization_id=context.organization_id,
        actor_id=context.user_id,
        resource_type=_RESOURCE_TYPES[addon_key],
        action=AuditAction.ACCESSED,
        outcome=AuditOutcome.DENIED,
        details={
            "reason": "addon_required",
            "addon_key": addon_key,
            "operation": operation,
            "state": access.state,
        },
    )

    if access.state == entitlement_service.STATE_GRACE:
        message = (
            f"{offer.display_name} is in its grace period. Existing resources keep "
            "working, but new ones need the add-on. Add it to your plan to continue."
        )
    elif access.state == entitlement_service.STATE_LAPSED:
        message = (
            f"The {offer.display_name} add-on has ended, so {offer.halt_effect}. "
            "Add it again to your plan to restore this."
        )
    else:
        message = (
            f"{offer.display_name} is an add-on. Add it to your plan, or upgrade "
            "to a plan that includes it."
        )

    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "code": ADDON_REQUIRED_CODE,
            "addon_key": addon_key,
            "addon_name": offer.display_name,
            "state": access.state,
            "grace_ends_at": (
                access.grace_ends_at.isoformat() if access.grace_ends_at else None
            ),
            "message": message,
        },
    )


__all__ = ["ADDON_REQUIRED_CODE", "require_addon"]