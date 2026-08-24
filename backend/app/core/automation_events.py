"""ARCH-13 Step 13.1 — the internal event vocabulary (F1).

`outbox_events` is one table with two audiences. `WEBHOOK_EVENT_TYPES`
(ARCH-09 §B.2) is what a customer's endpoint may receive. `INTERNAL_EVENT_TYPES`
is what the automation consumer may trigger on, and it must never be delivered
anywhere outside this process.

WHY ONE TABLE
=============

Two tables would give up atomicity between the state change and the event,
which is the entire point of a transactional outbox. So: one table, one
`visibility` discriminator, two vocabularies, and a CHECK constraint plus the
module-level assertion below so the two sets cannot silently converge.

WHY THE ASSERTION IS AT IMPORT TIME
===================================

The failure this guards against is not "someone writes the wrong event type".
It is "someone adds `work_item.enriched` to WEBHOOK_EVENT_TYPES eighteen months
from now because a customer asked for an enrichment webhook, and every internal
automation trigger starts being delivered to every configured endpoint". That
is a data leak introduced by a one-line, entirely reasonable-looking commit.
An import-time assertion turns it into a failed test run and a failed deploy.

The database CHECK constraint (`ck_outbox_events_visibility_vocabulary`) is the
second layer, because an assertion in Python does not constrain a psql session.
"""

from __future__ import annotations

from typing import Final, FrozenSet

from app.core.webhook_events import WEBHOOK_EVENT_TYPES

#: Visibility discriminator values. Mirrors the DB CHECK constraint.
VISIBILITY_PUBLIC: Final[str] = "PUBLIC"
VISIBILITY_INTERNAL: Final[str] = "INTERNAL"

VISIBILITIES: Final[FrozenSet[str]] = frozenset(
    {VISIBILITY_PUBLIC, VISIBILITY_INTERNAL}
)


#: Events the automation consumer may trigger on. Never delivered to a
#: customer endpoint. Every addition here must stay out of
#: `WEBHOOK_EVENT_TYPES` — the assertion below enforces that.
INTERNAL_EVENT_TYPES: Final[FrozenSet[str]] = frozenset(
    {
        # Emitted by `document.enrich` when enrichment commits. Replaces the
        # fire-and-forget in-process call in `enrich.py::_run_side_effects`
        # (F3). Carries work_item_id and the enrichment summary.
        "work_item.enriched",
        # Emitted by a `work_item.mutate` action when it changes a field.
        # This is the event that makes cross-rule cycles possible, which is
        # why 13.2 lands before any action can emit (Part 3).
        "work_item.field_changed",
        # ARCH-13 Step 13.7/13.8. Verification releases or blocks downstream
        # automation for a work item.
        "work_item.verification_completed",
        "work_item.verification_disagreed",
        # Terminal execution signal. Lets a rule chain off another rule
        # finishing without polling `automation_executions`.
        "automation.execution_completed",
        # A6. Emitted when an execution stops because it hit
        # `budget_cost_micros`. An operator-visible signal, not a webhook —
        # a customer's Zapier integration has no use for it and it names an
        # internal cost ceiling.
        "automation.budget_exhausted",
        # ARCH-15 Step 15.4. Seat lifecycle. A membership entering or leaving
        # `MembershipStatus.ACTIVE` changes what Stripe should be charging,
        # and the transition is the only moment at which anybody knows it.
        # INTERNAL because these name our seat accounting and our Stripe
        # state; a customer endpoint has no use for either, and
        # `billing.seat_removed` leaking would tell a subscriber's integration
        # about staffing changes at a tenant it merely shares a webhook with.
        "billing.seat_added",
        "billing.seat_removed",
        # Emitted by the drift detector when `seats_purchased` and the
        # `billable_seats` view disagree. Deliberately a *request to
        # re-assert*, not an instruction to overwrite: drift is a symptom,
        # and a job that silently corrects it hides the cause.
        "billing.seat_sync_needed",
    }
)


#: Namespaces reserved for internal events. An event type under one of these
#: prefixes may never be made PUBLIC, even if someone adds it to
#: `WEBHOOK_EVENT_TYPES`. Belt and braces: the disjointness assertion already
#: catches that, but this catches the case where a *new* internal namespace is
#: introduced and only added to one of the two sets.
#:
#: ARCH-15 reserves `billing.seat_` and not the whole `billing.` namespace.
#: Tranche 3 and 4 will want genuinely publishable billing events —
#: `billing.invoice_finalized` is a reasonable thing for a customer's
#: accounting integration to subscribe to — and reserving the parent namespace
#: now would make that a migration rather than a one-line addition. Seat
#: accounting is the part that must never leave this process.
INTERNAL_ONLY_PREFIXES: Final[tuple[str, ...]] = ("automation.", "billing.seat_")


def _assert_vocabularies_disjoint() -> None:
    """Refuse to import if an event type is in both vocabularies.

    Gate 13.1 asserts this raises. The test exists because the assertion is
    only useful if somebody has proven it fires.
    """
    overlap = INTERNAL_EVENT_TYPES & WEBHOOK_EVENT_TYPES
    if overlap:
        raise RuntimeError(
            "ARCH-13 F1 violation: event type(s) "
            f"{', '.join(sorted(overlap))} appear in both "
            "INTERNAL_EVENT_TYPES and WEBHOOK_EVENT_TYPES. An internal event "
            "in the webhook vocabulary is delivered to every customer "
            "endpoint that subscribes to it. Pick one audience."
        )

    leaked = {
        event_type
        for event_type in WEBHOOK_EVENT_TYPES
        if event_type.startswith(INTERNAL_ONLY_PREFIXES)
    }
    if leaked:
        raise RuntimeError(
            "ARCH-13 F1 violation: WEBHOOK_EVENT_TYPES contains "
            f"{', '.join(sorted(leaked))}, which is under a reserved internal "
            f"namespace ({', '.join(INTERNAL_ONLY_PREFIXES)})."
        )


_assert_vocabularies_disjoint()


def is_internal(event_type: str) -> bool:
    return event_type in INTERNAL_EVENT_TYPES


def visibility_for(event_type: str) -> str:
    """The only correct visibility for an event type.

    Callers should not pass `visibility` to `outbox_service.emit()` at all —
    the vocabulary determines it. The parameter exists on `emit()` so a caller
    that passes the *wrong* one gets an error rather than silently having it
    ignored.
    """
    if event_type in INTERNAL_EVENT_TYPES:
        return VISIBILITY_INTERNAL
    return VISIBILITY_PUBLIC


def sorted_internal_event_types() -> list[str]:
    return sorted(INTERNAL_EVENT_TYPES)


__all__ = [
    "INTERNAL_EVENT_TYPES",
    "INTERNAL_ONLY_PREFIXES",
    "VISIBILITIES",
    "VISIBILITY_INTERNAL",
    "VISIBILITY_PUBLIC",
    "is_internal",
    "sorted_internal_event_types",
    "visibility_for",
]