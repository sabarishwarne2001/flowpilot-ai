"""ARCH-15 Step 15.4 — seat events join the internal vocabulary (EXPAND)

ARCH-13 Step 13.1 wrote `ck_outbox_events_visibility_vocabulary`, which pins
the INTERNAL event list into the database so a Python constant cannot drift
from what a psql session may insert. Adding an internal event type therefore
means editing two places, and ARCH-13's release gate already asserts the two
agree — so that gate protects this phase for free.

The three new types:

    billing.seat_added        a membership entered ACTIVE
    billing.seat_removed      a membership left ACTIVE
    billing.seat_sync_needed  the drift detector wants Stripe re-asserted

All INTERNAL. None of them may ever become a customer-facing webhook: they
name our seat accounting and our Stripe state, and a customer's Zapier
integration has no use for either.

Revision ID: arch15_step4_seat_event_vocabulary
Revises: arch15_step3_billing_accounts_and_subscriptions
Create Date: 2026-08-22
"""

from __future__ import annotations

from alembic import op

revision = "arch15_step4_seat_event_vocabulary"
down_revision = "arch15_step3_billing_accounts_and_subscriptions"
branch_labels = None
depends_on = None


#: ARCH-13's list, verbatim.
ARCH13_INTERNAL_EVENT_TYPES: tuple[str, ...] = (
    "work_item.enriched",
    "work_item.field_changed",
    "work_item.verification_completed",
    "work_item.verification_disagreed",
    "automation.execution_completed",
    "automation.budget_exhausted",
)

#: ARCH-15 Step 15.4's additions.
ARCH15_INTERNAL_EVENT_TYPES: tuple[str, ...] = (
    "billing.seat_added",
    "billing.seat_removed",
    "billing.seat_sync_needed",
)

INTERNAL_EVENT_TYPES: tuple[str, ...] = (
    ARCH13_INTERNAL_EVENT_TYPES + ARCH15_INTERNAL_EVENT_TYPES
)

CONSTRAINT_NAME = "ck_outbox_events_visibility_vocabulary"


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _rewrite_vocabulary(values: tuple[str, ...]) -> None:
    op.execute(
        f"ALTER TABLE outbox_events DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}"
    )
    op.execute(
        f"""
        ALTER TABLE outbox_events ADD CONSTRAINT {CONSTRAINT_NAME} CHECK (
            (visibility = 'INTERNAL' AND event_type IN ({_sql_list(values)}))
            OR
            (visibility = 'PUBLIC' AND event_type NOT IN ({_sql_list(values)}))
        )
        """
    )


def upgrade() -> None:
    _rewrite_vocabulary(INTERNAL_EVENT_TYPES)


def downgrade() -> None:
    # Rows carrying the new vocabulary must go before the old constraint can
    # be validated, or the ALTER fails on existing data. They are internal
    # signals with no external consumer, so deleting them loses nothing a
    # subsequent drift check will not rediscover.
    op.execute(
        "DELETE FROM outbox_events WHERE event_type IN "
        f"({_sql_list(ARCH15_INTERNAL_EVENT_TYPES)})"
    )
    _rewrite_vocabulary(ARCH13_INTERNAL_EVENT_TYPES)
