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
    # ARCH32-S1:capability-redaction-display. Without an entry here the 402
    # body reads "capability.redaction is included on higher plans", which is
    # a key name in front of a customer.
    entitlements.REDACTION_CAPABILITY: "Document redaction",
    # ARCH33-S1:capability-assertions-display. Without an entry here the 402
    # body reads "capability.semantic_assertions is included on higher
    # plans", which is a key name in front of a customer.
    entitlements.SEMANTIC_ASSERTIONS_CAPABILITY: "Clause assertions",
    # ARCH34-S2:capability-anomaly-radar-display. Without an entry here the
    # 402 body reads "capability.anomaly_radar is included on higher plans",
    # which is a key name in front of a customer.
    entitlements.ANOMALY_RADAR_CAPABILITY: "Forensic audit radar",
    # ARCH35-S1:capability-calibrated-autonomy-display.
    entitlements.CALIBRATED_AUTONOMY_CAPABILITY: "Calibrated autonomy",
    # HM-S1:capability-display-names
    entitlements.DEVELOPER_API_CAPABILITY: "The developer API",
    entitlements.OUTGOING_WEBHOOKS_CAPABILITY: "Outgoing webhooks",
    entitlements.CUSTOM_BRANDING_CAPABILITY: "Custom branding",
    entitlements.CUSTOM_EMAIL_CAPABILITY: "Custom email",
    entitlements.ENTERPRISE_IDENTITY_CAPABILITY: "Enterprise single sign-on and SCIM",
    entitlements.PRIORITY_SLO_CAPABILITY: "The priority 99.9% SLO",
    # ARCH41-S2:capability-display
    entitlements.EXTRACTION_MEMORY_CAPABILITY: "Extraction memory",
    # ARCH42-S1:capability-display
    entitlements.ENTITY_GRAPH_CAPABILITY: "The entity graph",
    # ARCH43-S1:capability-display
    entitlements.CASE_INTELLIGENCE_CAPABILITY: "Case intelligence and the packet dicer",
    # ARCH44-S1:capability-display
    entitlements.TABLE_INTELLIGENCE_CAPABILITY: "Table intelligence",
    # ARCH45-S1:capability-display
    entitlements.UNIVERSAL_CORROBORATOR_CAPABILITY: "The document corroborator",
    # ARCH46-S1:capability-display
    entitlements.OBLIGATIONS_CAPABILITY: "Obligations and calendar feeds",
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

    # ARCH-31 Step 3 FIX. The first cut read `tier.limits` and
    # `tier.entitlement_rows`. `resolve_tier` returns a
    # `quota_service._TierSnapshot`, which has NEITHER attribute — it
    # exposes `entries`, a tuple of `TierLimit`. Both getattr calls
    # therefore returned their defaults and this function returned
    # False for every organization on every tier, including tiers
    # that bundle the capability.
    #
    # Nothing failed loudly: a 402 with remedy PLAN_UPGRADE is the
    # NORMAL response for most customers, so a paying Enterprise
    # tenant being told to upgrade looks exactly like the intended
    # behaviour until they call support.
    #
    # `entitlement_service.tier_grants` had the correct shape all
    # along — `any(entry.limit_key == key for entry in tier.entries)`
    # — which is the reading used here. One source of truth for
    # "does this tier grant X", not two that disagree.
    return any(
        getattr(entry, "limit_key", None) == capability_key
        for entry in getattr(tier, "entries", ()) or ()
    )


def granted_capabilities(db: Session, *, organization_id: Any) -> list[str]:
    """Every capability key the organization's tier carries, in key order.

    Resolves the tier ONCE, through the same reading `has_capability` uses —
    `tier.entries[].limit_key` — so the console's list and the request-path
    gate cannot disagree.
    """
    from app.services import quota_service

    tier = quota_service.resolve_tier(db, organization_id=organization_id)
    if tier is None:
        return []
    held = {
        getattr(entry, "limit_key", None)
        for entry in getattr(tier, "entries", ()) or ()
    }
    return [key for key in entitlements.CAPABILITY_KEYS if key in held]


def plans_including(db: Session) -> dict[str, list[str]]:
    """HM-S1. Display names of the plans on sale that carry each capability.

    Read from the same published tiers the plan cards render, so the upgrade
    dialog's "included on Business and Enterprise" cannot disagree with them.
    """
    from app.services import quota_service

    result: dict[str, list[str]] = {key: [] for key in entitlements.CAPABILITY_KEYS}
    for tier in quota_service.list_published_tiers(db):
        held = {getattr(entry, "limit_key", None) for entry in tier.entries}
        for key in entitlements.CAPABILITY_KEYS:
            if key in held and tier.display_name not in result[key]:
                result[key].append(tier.display_name)
    return result


def require_capability(
    db: Session, *, context: Any, capability_key: str, operation: str
) -> None:
    """Raise `CapabilityRequiredError` unless the tier carries the capability."""
    require_capability_for_organization(
        db,
        organization_id=context.organization_id,
        actor_id=getattr(context, "user_id", None),
        capability_key=capability_key,
        operation=operation,
    )


def require_capability_for_organization(
    db: Session,
    *,
    organization_id: Any,
    actor_id: Any,
    capability_key: str,
    operation: str,
    audit: bool = True,
) -> None:
    """HM-S1. The same refusal for callers that hold no request context.

    `audit=False` is for the API-key authentication path: a refused key is
    refused on every request it makes, and one audit row per request would let
    a script fill the audit log.
    """
    if has_capability(db, organization_id=organization_id, capability_key=capability_key):
        return

    display = _DISPLAY_NAMES.get(capability_key, capability_key)

    if audit:
        audit_service.record_independently(
            organization_id=organization_id,
            actor_id=actor_id,
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


__all__ = [
    "CAPABILITY_REQUIRED_CODE",
    "granted_capabilities",
    "has_capability",
    "plans_including",
    "require_capability",
    "require_capability_for_organization",
]