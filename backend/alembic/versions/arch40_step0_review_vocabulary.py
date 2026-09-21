"""ARCH-40 Step 0 — vocabulary only: audit resource types and one trigger event.

Revision ID: arch40_step0_review_vocabulary
Revises: arch38_step1_batches

WHY THIS IS ITS OWN MIGRATION
=============================

PostgreSQL refuses to use an enum value in the transaction that added it.
`ALTER TYPE ... ADD VALUE` must therefore run outside a transaction block, and
`arch40_step1_settings_review` needs `REVIEW_ASSIGNMENT` usable immediately.
ARCH-22, 25, 26, 27, 37 and 38 all split their vocabulary out for this reason;
this is the same split.

WHAT IT ADDS
============

  audit_resource_type   REVIEW_ITEM, REVIEW_ASSIGNMENT, AI_SETTINGS,
                        WORKSPACE_EMAIL_OVERRIDE
  outbox_events         `trigger.review.cleared` becomes a legal INTERNAL
                        event type.

`trigger.review.cleared` is NOT reserved anywhere before ARCH-40, unlike
`trigger.batch.completed`, which ARCH-37 reserved ahead of its emitter. So the
CHECK has to be rebuilt here rather than left alone.

THE PINNED VOCABULARY
=====================

`INTERNAL_AFTER_40` is written out in full rather than imported from
`app.core.automation_events`. A migration that imports the live set silently
changes what it did the next time the set changes; ARCH-37's own migration
makes the same choice and says so.
"""

from __future__ import annotations

from alembic import op

revision = "arch40_step0_review_vocabulary"
down_revision = "arch38_step1_batches"
branch_labels = None
depends_on = None


#: ARCH40-S1:audit-review. Four new resource types.
NEW_AUDIT_RESOURCE_TYPES: tuple[str, ...] = (
    "REVIEW_ITEM",
    "REVIEW_ASSIGNMENT",
    "AI_SETTINGS",
    "WORKSPACE_EMAIL_OVERRIDE",
)

#: The internal vocabulary as of ARCH-38, pinned, plus ARCH-40's one addition.
INTERNAL_BEFORE_40: tuple[str, ...] = (
    "work_item.enriched",
    "work_item.verification_completed",
    "work_item.field_changed",
    "usage.recorded",
    "usage.aggregation_needed",
    "billing.invoice_finalization_needed",
    "billing.seat_sync_needed",
    "identity.user_provisioned",
    "identity.user_deprovisioned",
    "identity.user_reactivated",
    "identity.domain_verified",
    "identity.domain_lapsed",
    "identity.jit_cap_reached",
    "identity.idp_config_changed",
    "trigger.document.completed",
    "trigger.document.failed",
    "trigger.procurement.completed",
    "trigger.procurement.approved",
    "trigger.procurement.disputed",
    "trigger.anomaly.detected",
    "trigger.work_item.created",
    "trigger.work_item.reprocessed",
    "trigger.assertion.held",
    "trigger.redaction.completed",
    "trigger.batch.completed",
)

REVIEW_CLEARED_EVENT: str = "trigger.review.cleared"

INTERNAL_AFTER_40: tuple[str, ...] = INTERNAL_BEFORE_40 + (REVIEW_CLEARED_EVENT,)


def _visibility_check(values: tuple[str, ...]) -> None:
    """Rebuild ck_outbox_events_visibility_vocabulary over `values`.

    Byte-for-byte the shape `arch37_step1_flow_builder._visibility_check`
    writes. Two definitions of one constraint that differ in whitespace would
    make `verify_arch37`'s constraint text comparison fail for no reason.
    """
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
    # ---- audit_resource_type -------------------------------------------
    # IF NOT EXISTS makes this re-runnable after a partial failure. Without
    # it a second attempt dies on the first value it already added, and the
    # autocommit block means there is no transaction to roll that back.
    with op.get_context().autocommit_block():
        for value in NEW_AUDIT_RESOURCE_TYPES:
            op.execute(
                f"ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS '{value}'"
            )

    # ---- outbox event vocabulary ---------------------------------------
    _visibility_check(INTERNAL_AFTER_40)


def downgrade() -> None:
    # PostgreSQL cannot remove an enum value. Narrowing the CHECK is the only
    # reversible half, and it is only safe once no row carries the event.
    op.execute(
        f"DELETE FROM outbox_events WHERE event_type = '{REVIEW_CLEARED_EVENT}'"
    )
    _visibility_check(INTERNAL_BEFORE_40)
