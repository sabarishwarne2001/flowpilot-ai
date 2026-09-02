"""ARCH-09 Step 6a — webhook_delivery_attempts (EXPAND)

One row per ATTEMPT. webhook_deliveries is one row per logical delivery;
this is the per-attempt history behind it.

Revision ID: arch09_step6_attempts_expand
Revises: arch09_step4_webhooks_expand
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch09_step6_attempts_expand"
down_revision = "arch09_step4_webhooks_expand"
branch_labels = None
depends_on = None

ATTEMPT_DISPOSITION_VALUES: tuple[str, ...] = (
    "DELIVERED",
    "RETRY",
    "DEAD",
)


def upgrade() -> None:
    bind = op.get_bind()

    disposition = postgresql.ENUM(
        *ATTEMPT_DISPOSITION_VALUES, name="webhook_attempt_disposition"
    )
    disposition.create(bind, checkfirst=True)

    op.create_table(
        "webhook_delivery_attempts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "webhook_delivery_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("webhook_deliveries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        # --- request ---------------------------------------------------
        sa.Column("request_url", sa.Text(), nullable=False),
        sa.Column(
            "request_headers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("resolved_ip", sa.String(length=45), nullable=True),
        # --- response --------------------------------------------------
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column(
            "response_headers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("response_body_excerpt", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        # --- outcome ---------------------------------------------------
        sa.Column(
            "disposition",
            postgresql.ENUM(
                *ATTEMPT_DISPOSITION_VALUES,
                name="webhook_attempt_disposition",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
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
        # --- constraints ------------------------------------------------
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="attempt_number_positive",
        ),
        sa.CheckConstraint(
            "duration_ms >= 0",
            name="duration_non_negative",
        ),
        sa.CheckConstraint(
            "response_status IS NOT NULL OR error IS NOT NULL",
            name="outcome_recorded",
        ),
        sa.CheckConstraint(
            "response_status IS NULL OR (response_status BETWEEN 100 AND 599)",
            name="status_in_range",
        ),
        sa.UniqueConstraint(
            "webhook_delivery_id",
            "attempt_number",
            name="uq_webhook_delivery_attempts_delivery_attempt",
        ),
    )

    op.create_index(
        "ix_webhook_delivery_attempts_delivery_id_attempt",
        "webhook_delivery_attempts",
        ["webhook_delivery_id", sa.text("attempt_number DESC")],
    )
    op.create_index(
        "ix_webhook_delivery_attempts_attempted_at",
        "webhook_delivery_attempts",
        ["attempted_at"],
    )
    op.create_index(
        "ix_webhook_delivery_attempts_organization_id_attempted_at",
        "webhook_delivery_attempts",
        ["organization_id", sa.text("attempted_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_webhook_delivery_attempts_organization_id_attempted_at",
        table_name="webhook_delivery_attempts",
    )
    op.drop_index(
        "ix_webhook_delivery_attempts_attempted_at",
        table_name="webhook_delivery_attempts",
    )
    op.drop_index(
        "ix_webhook_delivery_attempts_delivery_id_attempt",
        table_name="webhook_delivery_attempts",
    )
    op.drop_table("webhook_delivery_attempts")
    postgresql.ENUM(name="webhook_attempt_disposition").drop(
        op.get_bind(), checkfirst=True
    )
