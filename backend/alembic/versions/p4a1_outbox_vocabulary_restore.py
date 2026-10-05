"""PHASE 4 (F-051) — restore five internal events the outbox CHECK lost.

Revision ID: p4a1_outbox_vocabulary_restore
Revises: arch40_step3_contract_ai_settings
Create Date: 2026-10-05

WHAT WAS WRONG
==============

`ck_outbox_events_visibility_vocabulary` pins the INTERNAL event names into the
database. ARCH-15 added the seat events and ARCH-13 the automation and
verification events, but ARCH-37 and every later migration rebuilt the CHECK
from a hand-written list that left five of them out:

    billing.seat_added              accepting an invitation on a paid organization
    billing.seat_removed            removing a member of a paid organization
    work_item.verification_disagreed  a verification that does not release the document
    automation.execution_completed  a rule chaining off another rule
    automation.budget_exhausted     a rule stopped at its cost ceiling

Each emitter's insert was refused, which rolled back the user's whole action:
the visible symptom was HTTP 500 on "Accept invitation" for every organization
with a billing account (F-051).

`tests/api/test_invitation_paid_org.py` now asserts the database admits every
name in `app.core.automation_events.INTERNAL_EVENT_TYPES`, so the two lists
cannot drift apart silently again.

The list is written out in full, not imported, for the reason ARCH-37's
migration gives: a migration that imports the live set silently changes what it
did the next time the set changes.
"""

from __future__ import annotations

from alembic import op

revision = "p4a1_outbox_vocabulary_restore"
down_revision = "arch40_step3_contract_ai_settings"
branch_labels = None
depends_on = None

#: The CHECK as arch49/arch50 left it (35 names), pinned.
INTERNAL_BEFORE: tuple[str, ...] = (
    "billing.invoice_finalization_needed",
    "billing.seat_sync_needed",
    "identity.domain_lapsed",
    "identity.domain_verified",
    "identity.idp_config_changed",
    "identity.jit_cap_reached",
    "identity.user_deprovisioned",
    "identity.user_provisioned",
    "identity.user_reactivated",
    "trigger.anomaly.detected",
    "trigger.assertion.held",
    "trigger.batch.completed",
    "trigger.case.completed",
    "trigger.case.inconsistent",
    "trigger.corroboration.discrepancies",
    "trigger.document.completed",
    "trigger.document.failed",
    "trigger.obligation.due_soon",
    "trigger.obligation.overdue",
    "trigger.packet.split",
    "trigger.posting.failed",
    "trigger.process.sla_at_risk",
    "trigger.procurement.approved",
    "trigger.procurement.completed",
    "trigger.procurement.disputed",
    "trigger.redaction.completed",
    "trigger.review.cleared",
    "trigger.table.flagged",
    "trigger.work_item.created",
    "trigger.work_item.reprocessed",
    "usage.aggregation_needed",
    "usage.recorded",
    "work_item.enriched",
    "work_item.field_changed",
    "work_item.verification_completed",
)

#: The five names the Python vocabulary emits and the CHECK refused.
RESTORED: tuple[str, ...] = (
    "automation.budget_exhausted",
    "automation.execution_completed",
    "billing.seat_added",
    "billing.seat_removed",
    "work_item.verification_disagreed",
)

INTERNAL_AFTER: tuple[str, ...] = INTERNAL_BEFORE + RESTORED


def _visibility_check(values: tuple[str, ...]) -> None:
    """Same shape as arch40_step0_review_vocabulary._visibility_check."""
    op.execute(
        "ALTER TABLE outbox_events DROP CONSTRAINT IF EXISTS "
        "ck_outbox_events_visibility_vocabulary"
    )
    array = ", ".join(f"'{value}'::character varying" for value in values)
    op.execute(
        "ALTER TABLE outbox_events ADD CONSTRAINT "
        "ck_outbox_events_visibility_vocabulary CHECK ("
        f"((visibility::text = 'INTERNAL'::text AND event_type::text = ANY (ARRAY[{array}]::text[])) OR "
        f"(visibility::text = 'PUBLIC'::text AND event_type::text <> ALL (ARRAY[{array}]::text[]))))"
    )


def upgrade() -> None:
    _visibility_check(INTERNAL_AFTER)


def downgrade() -> None:
    # Narrowing the CHECK is only valid once no row carries a restored name.
    names = ", ".join(f"'{name}'" for name in RESTORED)
    op.execute(f"DELETE FROM outbox_events WHERE event_type IN ({names})")
    _visibility_check(INTERNAL_BEFORE)
