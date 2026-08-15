"""ARCH-09 §B.2 — the curated webhook event vocabulary.

Sits beside `app/core/scopes.py` for the same reason that module exists: a
closed vocabulary that the database enforces needs exactly one authoritative
Python definition, or the CHECK constraint and the service drift apart and the
drift is only discovered by an integrity error in production.

The audit log is not the source. `audit_logs` records denials, aggregated
abuse counters, and internal actions. Publishing it to customer endpoints would
hand every integrator a live feed of the platform's security surface. Outbox
rows carry `audit_log_id` so the two can be correlated by an operator who is
already authorised to read both.

Adding an event type is a migration (the CHECK constraint) plus an edit here.
`scripts/verify_arch09_step2.py` asserts the two agree.
"""

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
    }
)

#: Prefixes that must never become publishable. Enforced by test, not by
#: convention, because "someone will eventually add it" is not a threat model.
#:
#:   api_key.*    ARCH-08 §B.2 — key lifecycle is the security surface itself
#:   session.*    ARCH-08 §B.8 — authentication events have no established actor
#:   audit_log.*  §B.2 — raw audit is never the source
#:   auth.*       as above
#:
#: `ownership.*` is a deliberate OPEN QUESTION for §B.2 sign-off rather than a
#: permanent exclusion: ARCH-05 built transfer as a human-confirmed flow, and a
#: customer integration arguably has a legitimate interest in knowing it
#: happened. It ships excluded until someone decides.
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
    """Stable ordering, for migrations, docs, and gate output."""
    return sorted(WEBHOOK_EVENT_TYPES)