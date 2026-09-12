"""ARCH-31 Step 0 — request-path refusal for tier-bundled capabilities.

Mirrors `app/api/addon_gate.py` in shape and diverges in exactly one way: the
remedy. An add-on can be bought on the plan the customer already has, so
`AddonRequiredError` sends them to a purchase flow. A capability is bundled
into a tier, so the only route to it is a plan change, and a 402 that offers
a purchase would send them somewhere with nothing to sell them.

Same ARCH-01 envelope (`{code, message, details}`), same 402, same audit
record on denial — so the frontend's `ApiError` handling needs no new branch
and the denial is as investigable as an add-on denial already is.

THE ABSENT-ENTITLEMENT CASE IS A DENIAL, NOT AN ERROR
=====================================================
A tier that simply does not carry `capability.reconciliation` is the normal
state for most customers. This raises 402 rather than 403, because 403 says
"you are not allowed" and 402 says "your plan does not include this" — and
only one of those is true.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core import entitlements
from app.core.billing_errors import CapabilityRequiredError
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.services import audit_service

CAPABILITY_REQUIRED_CODE = "CAPABILITY_REQUIRED"

_DISPLAY_NAMES = {
    entitlements.RECONCILIATION_CAPABILITY: "Procurement matching",
}


def has_capability(db: Session, *, organization_id: Any, capability_key: str) -> bool:
    """Whether the organization's resolved tier carries `capability_key`.

    Reads through `quota_service.resolve_tier`, which is the one place that
    knows how a tier is resolved (live subscription first, then the
    organization pointer). Re-deriving it here would give ARCH-31 a second,
    divergent answer to "what plan is this tenant on".
    """
    from app.services import quota_service

    if capability_key not in entitlements.ENTITLEMENT_KEYS:
        raise ValueError(
            f"{capability_key!r} is not a registered entitlement. Register it "
            f"in app/core/entitlements.py rather than checking a string "
            f"literal; an unregistered key silently returns False forever."
        )

    tier = quota_service.resolve_tier(db, organization_id=organization_id)
    if tier is None:
        return False
    limits = getattr(tier, "limits", None) or {}
    if isinstance(limits, dict) and capability_key in limits:
        return True
    for row in getattr(tier, "entitlement_rows", None) or []:
        if getattr(row, "entitlement_key", None) == capability_key:
            return True
    return False


def require_capability(
    db: Session, *, context: Any, capability_key: str, operation: str
) -> None:
    """Raise `CapabilityRequiredError` unless the tier carries the capability."""
    if has_capability(
        db, organization_id=context.organization_id, capability_key=capability_key
    ):
        return

    display = _DISPLAY_NAMES.get(capability_key, capability_key)

    audit_service.record_independently(
        organization_id=context.organization_id,
        actor_id=getattr(context, "user_id", None),
        resource_type=AuditResourceType.ORGANIZATION,
        action=AuditAction.ACCESSED,
        outcome=AuditOutcome.DENIED,
        details={
            "reason": "capability_required",
            "capability_key": capability_key,
            "operation": operation,
        },
    )

    raise CapabilityRequiredError(
        f"{display} is included on higher plans. Upgrade your plan to use it.",
        details={
            "code": CAPABILITY_REQUIRED_CODE,
            "capability_key": capability_key,
            "capability_name": display,
            "operation": operation,
            # No price and no purchase URL, deliberately. A capability has no
            # standalone price; inventing one here would be the first place a
            # number appears that nothing in billing can honour.
            "remedy": "PLAN_UPGRADE",
        },
    )


__all__ = ["CAPABILITY_REQUIRED_CODE", "has_capability", "require_capability"]