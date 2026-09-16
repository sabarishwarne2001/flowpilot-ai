"""ARCH-09 §B.2, ARCH-10 Step 7 — the curated webhook event vocabulary."""

from __future__ import annotations

from typing import Final, FrozenSet

#: Events publishable to customer webhook endpoints in v1.
WEBHOOK_EVENT_TYPES: Final[FrozenSet[str]] = frozenset(
    {
        # organization
        "organization.updated",
        # membership (ARCH-01 / ARCH-06)
        "member.invited",
        "member.joined",
        "member.role_changed",
        "member.deactivated",
        "member.reactivated",
        # invitations (ARCH-04)
        "invitation.created",
        "invitation.accepted",
        "invitation.rejected",
        "invitation.revoked",
        "invitation.expired",
        # workspaces (ARCH-01 / ARCH-02)
        "workspace.created",
        "workspace.updated",
        "workspace.archived",
        "workspace.restored",
        # work items (ARCH-02)
        "work_item.created",
        "work_item.updated",
        "work_item.deleted",
        # documents / pipeline (ARCH-10 Step 7)
        "document.queued",
        "document.processing",
        "document.completed",
        "document.failed",
        # ARCH-31 procurement matching. PUBLIC rather than INTERNAL:
        # a tenant's AP automation is the intended consumer — these
        # say an invoice finished matching, was approved, or was
        # disputed, and every one of those is a thing an external
        # workflow should be able to react to. None of them names an
        # internal cost, a seat count or a provider.
        "procurement.completed",
        "procurement.approved",
        "procurement.disputed",
        # ARCH34-S2:anomaly-detected-public. PUBLIC rather than INTERNAL,
        # for the same reason the three above are: a tenant's AP
        # automation is the intended consumer. §5.2 is explicit that the
        # radar does not block payment itself — blocking is an ARCH-13
        # rule a tenant chooses to write on top ("if a duplicate finding
        # above 90% exists, send to review"), and a rule cannot fire on
        # an event it cannot see.
        #
        # The payload names the finding and carries no evidence body.
        # Evidence is quoted document text, and pushing it through a
        # webhook would send contract language to whatever URL a tenant
        # configured; the API serves it under the capability gate.
        "anomaly.detected",
    }
)

FORBIDDEN_EVENT_PREFIXES: Final[tuple[str, ...]] = (
    "api_key.",
    "session.",
    "audit_log.",
    "auth.",
    "ownership.",
)


def is_publishable(event_type: str) -> bool:
    return event_type in WEBHOOK_EVENT_TYPES


def sorted_event_types() -> list[str]:
    return sorted(WEBHOOK_EVENT_TYPES)
