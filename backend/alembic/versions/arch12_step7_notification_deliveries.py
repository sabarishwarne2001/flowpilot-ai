"""ARCH-12 Step 7 — notification delivery records with dead-lettering (EXPAND)

Revision ID: arch12_step7_notification_deliveries
Revises: arch12_step6b_provenance
Create Date: 2026-08-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch12_step7_notification_deliveries"
down_revision = "arch12_step6b_provenance"
branch_labels = None
depends_on = None

DELIVERY_STATUS_ENUM = "notification_delivery_status"
DELIVERY_STATUSES: tuple[str, ...] = (
    "PENDING",
    "SENDING",
    "DELIVERED",
    "FAILED",
    "DEAD",
)

FINISH_REASONS: tuple[str, ...] = (
    "completed",
    "client_disconnected",
    "provider_error",
    "deadline_exceeded",
    "spend_limit",
    "output_ceiling",
    "filtered",
)


def upgrade() -> None:
    # Widen alembic_version.version_num to support descriptive revision IDs
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")

    # Idempotently ensure streaming check constraints exist on conversation_messages
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_conversation_messages_finish_reason') THEN
                ALTER TABLE conversation_messages ADD CONSTRAINT ck_conversation_messages_finish_reason
                CHECK (finish_reason IS NULL OR finish_reason IN (
                    'completed', 'client_disconnected', 'provider_error', 'deadline_exceeded', 'spend_limit', 'output_ceiling', 'filtered'
                ));
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_conversation_messages_stream_state_reason') THEN
                ALTER TABLE conversation_messages ADD CONSTRAINT ck_conversation_messages_stream_state_reason
                CHECK (
                    (stream_state IN ('NONE', 'STREAMING') AND finish_reason IS NULL)
                    OR (stream_state IN ('COMPLETE', 'ABORTED') AND finish_reason IS NOT NULL)
                );
            END IF;
        END $$;
        """
    )

    delivery_status = postgresql.ENUM(
        *DELIVERY_STATUSES,
        name=DELIVERY_STATUS_ENUM,
        create_type=False,
    )
    delivery_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "notification_deliveries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "notification_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "channel",
            postgresql.ENUM(name="notification_channel", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            delivery_status,
            nullable=False,
            server_default=sa.text(f"'PENDING'::{DELIVERY_STATUS_ENUM}"),
        ),
        sa.Column(
            "target",
            sa.String(512),
            nullable=True,
            comment=(
                "Email address, webhook URL, or NULL for IN_APP. Never a "
                "credential — the webhook signing secret stays on "
                "webhook_endpoints, per ARCH-09."
            ),
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment=(
                "The already-filtered title/body. Written post-redaction so "
                "the stored copy cannot leak what the stream filter removed."
            ),
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "max_attempts", sa.Integer(), nullable=False, server_default=sa.text("6")
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        sa.CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        sa.CheckConstraint(
            f"(status = 'DELIVERED'::{DELIVERY_STATUS_ENUM}) "
            "= (delivered_at IS NOT NULL)",
            name="delivered_at_matches_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="payload_is_object"
        ),
    )

    op.create_index(
        "ix_notification_deliveries_due",
        "notification_deliveries",
        ["next_attempt_at"],
        postgresql_where=sa.text(
            f"status IN ('PENDING'::{DELIVERY_STATUS_ENUM}, "
            f"'FAILED'::{DELIVERY_STATUS_ENUM})"
        ),
    )
    op.create_index(
        "ix_notification_deliveries_dead",
        "notification_deliveries",
        ["organization_id", sa.text("created_at DESC")],
        postgresql_where=sa.text(f"status = 'DEAD'::{DELIVERY_STATUS_ENUM}"),
    )
    op.create_index(
        "ix_notification_deliveries_notification",
        "notification_deliveries",
        ["notification_id"],
    )
    op.create_index(
        "uq_notification_deliveries_org_idempotency_key",
        "notification_deliveries",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_notification_deliveries_org_idempotency_key",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "ix_notification_deliveries_notification",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "ix_notification_deliveries_dead", table_name="notification_deliveries"
    )
    op.drop_index(
        "ix_notification_deliveries_due", table_name="notification_deliveries"
    )
    op.drop_table("notification_deliveries")
    postgresql.ENUM(name=DELIVERY_STATUS_ENUM).drop(op.get_bind(), checkfirst=True)