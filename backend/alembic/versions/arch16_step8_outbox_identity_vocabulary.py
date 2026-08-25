"""ARCH-16 Step 8 — Update outbox_events visibility vocabulary to include identity events.

Revision ID: arch16_step8_outbox_identity_vocabulary
Revises: arch16_step7_job_suppression
"""
from alembic import op

revision = "arch16_step8_outbox_identity_vocabulary"
down_revision = "arch16_step7_job_suppression"
branch_labels = None
depends_on = None

INTERNAL_EVENT_TYPES = (
    "work_item.enriched",
    "work_item.field_changed",
    "work_item.verification_completed",
    "work_item.verification_disagreed",
    "automation.execution_completed",
    "automation.budget_exhausted",
    "billing.seat_added",
    "billing.seat_removed",
    "billing.seat_sync_needed",
    "identity.user_provisioned",
    "identity.user_deprovisioned",
    "identity.user_reactivated",
    "identity.domain_verified",
    "identity.domain_lapsed",
    "identity.jit_cap_reached",
    "identity.idp_config_changed",
)


def upgrade() -> None:
    # Use raw SQL to drop exact constraint name without naming-convention double prefix
    op.execute("ALTER TABLE outbox_events DROP CONSTRAINT IF EXISTS ck_outbox_events_visibility_vocabulary")

    arr_sql = ", ".join(f"'{t}'::character varying" for t in INTERNAL_EVENT_TYPES)
    op.execute(
        f"ALTER TABLE outbox_events ADD CONSTRAINT ck_outbox_events_visibility_vocabulary "
        f"CHECK (((visibility::text = 'INTERNAL'::text AND event_type::text = ANY (ARRAY[{arr_sql}]::text[])) OR "
        f"(visibility::text = 'PUBLIC'::text AND event_type::text <> ALL (ARRAY[{arr_sql}]::text[]))))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE outbox_events DROP CONSTRAINT IF EXISTS ck_outbox_events_visibility_vocabulary")

    legacy_types = (
        "work_item.enriched",
        "work_item.field_changed",
        "work_item.verification_completed",
        "work_item.verification_disagreed",
        "automation.execution_completed",
        "automation.budget_exhausted",
        "billing.seat_added",
        "billing.seat_removed",
        "billing.seat_sync_needed",
    )
    arr_sql = ", ".join(f"'{t}'::character varying" for t in legacy_types)
    op.execute(
        f"ALTER TABLE outbox_events ADD CONSTRAINT ck_outbox_events_visibility_vocabulary "
        f"CHECK (((visibility::text = 'INTERNAL'::text AND event_type::text = ANY (ARRAY[{arr_sql}]::text[])) OR "
        f"(visibility::text = 'PUBLIC'::text AND event_type::text <> ALL (ARRAY[{arr_sql}]::text[]))))"
    )