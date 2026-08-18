"""ARCH-10 Step 7 — pipeline state machine (EXPAND)

Revision ID: arch10_step7_pipeline_expand
Revises: arch10_step5_intake_expand
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch10_step7_pipeline_expand"
down_revision = "arch10_step5_intake_expand"
branch_labels = None
depends_on = None

PIPELINE_STAGES: tuple[str, ...] = (
    "QUEUED",
    "EXTRACTING",
    "EXTRACTED",
    "ENRICHING",
    "COMPLETED",
    "FAILED",
    "QUOTA_BLOCKED",
)

LEGAL_TRANSITIONS: tuple[tuple[str, str], ...] = (
    ("QUEUED", "EXTRACTING"),
    ("QUEUED", "FAILED"),
    ("QUEUED", "QUOTA_BLOCKED"),
    ("EXTRACTING", "EXTRACTED"),
    ("EXTRACTING", "FAILED"),
    ("EXTRACTING", "QUOTA_BLOCKED"),
    ("EXTRACTED", "ENRICHING"),
    ("EXTRACTED", "COMPLETED"),
    ("EXTRACTED", "FAILED"),
    ("ENRICHING", "COMPLETED"),
    ("ENRICHING", "FAILED"),
    ("COMPLETED", "QUEUED"),
    ("FAILED", "QUEUED"),
    ("QUOTA_BLOCKED", "QUEUED"),
)

WEBHOOK_EVENT_TYPES: tuple[str, ...] = (
    "organization.updated",
    "member.invited",
    "member.joined",
    "member.role_changed",
    "member.deactivated",
    "member.reactivated",
    "invitation.created",
    "invitation.accepted",
    "invitation.rejected",
    "invitation.revoked",
    "invitation.expired",
    "workspace.created",
    "workspace.updated",
    "workspace.archived",
    "workspace.restored",
    "work_item.created",
    "work_item.updated",
    "work_item.deleted",
    # ARCH-10 Step 7
    "document.queued",
    "document.processing",
    "document.completed",
    "document.failed",
)

_EVENT_ARRAY = ", ".join(f"'{value}'" for value in WEBHOOK_EVENT_TYPES)
_TRANSITION_ARRAY = ", ".join(
    f"('{source}','{target}')" for source, target in LEGAL_TRANSITIONS
)

TRANSITION_FUNCTION = f"""
CREATE OR REPLACE FUNCTION work_items_stage_transition_guard()
RETURNS TRIGGER AS $$
DECLARE
    legal BOOLEAN;
BEGIN
    IF NEW.pipeline_stage IS NOT DISTINCT FROM OLD.pipeline_stage THEN
        RETURN NEW;
    END IF;

    IF OLD.pipeline_stage IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM (VALUES {_TRANSITION_ARRAY}) AS t(src, dst)
        WHERE t.src = OLD.pipeline_stage::text
          AND t.dst = NEW.pipeline_stage::text
    ) INTO legal;

    IF NOT legal THEN
        RAISE EXCEPTION
            'work_items %: illegal pipeline transition % -> %',
            OLD.id, OLD.pipeline_stage, NEW.pipeline_stage
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    bind = op.get_bind()

    stage_enum = postgresql.ENUM(*PIPELINE_STAGES, name="work_item_pipeline_stage")
    stage_enum.create(bind, checkfirst=True)

    op.add_column(
        "work_items",
        sa.Column(
            "pipeline_stage",
            postgresql.ENUM(
                *PIPELINE_STAGES,
                name="work_item_pipeline_stage",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "work_items",
        sa.Column("stage_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "work_items", sa.Column("failure_stage", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "work_items", sa.Column("failure_reason", sa.String(length=1000), nullable=True)
    )

    op.execute(
        """
        UPDATE work_items
        SET pipeline_stage = CASE status
                WHEN 'COMPLETED'  THEN 'COMPLETED'
                WHEN 'FAILED'     THEN 'FAILED'
                WHEN 'PROCESSING' THEN 'EXTRACTING'
                ELSE 'QUEUED'
            END::work_item_pipeline_stage,
            stage_updated_at = COALESCE(updated_at, now())
        WHERE pipeline_stage IS NULL
        """
    )

    op.alter_column("work_items", "pipeline_stage", nullable=False)
    op.alter_column(
        "work_items",
        "pipeline_stage",
        server_default=sa.text("'QUEUED'::work_item_pipeline_stage"),
    )

    op.execute(
        "CREATE INDEX ix_work_items_workspace_pipeline_stage "
        "ON work_items (workspace_id, pipeline_stage)"
    )
    op.execute(
        "CREATE INDEX ix_work_items_stage_stuck "
        "ON work_items (stage_updated_at) "
        "WHERE pipeline_stage IN ('QUEUED','EXTRACTING','EXTRACTED','ENRICHING')"
    )

    op.execute(TRANSITION_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER trg_work_items_stage_transition
        BEFORE UPDATE ON work_items
        FOR EACH ROW EXECUTE FUNCTION work_items_stage_transition_guard();
        """
    )

    # --- outbox & webhook vocabulary updates: document.* ----------------
    op.execute(
        "ALTER TABLE outbox_events "
        "DROP CONSTRAINT IF EXISTS ck_outbox_events_event_type_vocabulary"
    )
    op.execute(
        "ALTER TABLE outbox_events ADD CONSTRAINT "
        "ck_outbox_events_event_type_vocabulary "
        f"CHECK (event_type IN ({_EVENT_ARRAY}))"
    )
    op.execute(
        "ALTER TABLE webhook_endpoints "
        "DROP CONSTRAINT IF EXISTS ck_webhook_endpoints_event_types_vocabulary"
    )
    op.execute(
        "ALTER TABLE webhook_endpoints ADD CONSTRAINT "
        "ck_webhook_endpoints_event_types_vocabulary "
        f"CHECK (event_types <@ ARRAY[{_EVENT_ARRAY}]::varchar[])"
    )
    op.execute(
        "ALTER TABLE webhook_deliveries "
        "DROP CONSTRAINT IF EXISTS ck_webhook_deliveries_event_type_vocabulary"
    )
    op.execute(
        "ALTER TABLE webhook_deliveries ADD CONSTRAINT "
        "ck_webhook_deliveries_event_type_vocabulary "
        f"CHECK (event_type IN ({_EVENT_ARRAY}))"
    )


def downgrade() -> None:
    _OLD_EVENTS = ", ".join(
        f"'{value}'" for value in WEBHOOK_EVENT_TYPES if not value.startswith("document.")
    )
    op.execute(
        "ALTER TABLE outbox_events "
        "DROP CONSTRAINT IF EXISTS ck_outbox_events_event_type_vocabulary"
    )
    op.execute(
        "ALTER TABLE outbox_events ADD CONSTRAINT "
        "ck_outbox_events_event_type_vocabulary "
        f"CHECK (event_type IN ({_OLD_EVENTS}))"
    )
    op.execute(
        "ALTER TABLE webhook_deliveries "
        "DROP CONSTRAINT IF EXISTS ck_webhook_deliveries_event_type_vocabulary"
    )
    op.execute(
        "ALTER TABLE webhook_deliveries ADD CONSTRAINT "
        "ck_webhook_deliveries_event_type_vocabulary "
        f"CHECK (event_type IN ({_OLD_EVENTS}))"
    )
    op.execute(
        "ALTER TABLE webhook_endpoints "
        "DROP CONSTRAINT IF EXISTS ck_webhook_endpoints_event_types_vocabulary"
    )
    op.execute(
        "ALTER TABLE webhook_endpoints ADD CONSTRAINT "
        "ck_webhook_endpoints_event_types_vocabulary "
        f"CHECK (event_types <@ ARRAY[{_OLD_EVENTS}]::varchar[])"
    )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_work_items_stage_transition ON work_items"
    )
    op.execute("DROP FUNCTION IF EXISTS work_items_stage_transition_guard()")
    op.execute("DROP INDEX IF EXISTS ix_work_items_stage_stuck")
    op.execute("DROP INDEX IF EXISTS ix_work_items_workspace_pipeline_stage")
    op.drop_column("work_items", "failure_reason")
    op.drop_column("work_items", "failure_stage")
    op.drop_column("work_items", "stage_updated_at")
    op.drop_column("work_items", "pipeline_stage")
    op.execute("DROP TYPE IF EXISTS work_item_pipeline_stage")