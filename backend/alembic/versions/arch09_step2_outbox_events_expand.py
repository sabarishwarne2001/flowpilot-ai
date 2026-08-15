"""ARCH-09 Step 2 — outbox_events (EXPAND)

Revision ID: arch09_step2_outbox_expand
Revises: arch08_step8_api_keys_expand
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch09_step2_outbox_expand"
down_revision = "arch08_step8_api_keys_expand"
branch_labels = None
depends_on = None

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
)

OUTBOX_STATUS_VALUES: tuple[str, ...] = (
    "PENDING",
    "CLAIMED",
    "PUBLISHED",
    "FAILED",
    "DEAD",
)

_EVENT_TYPE_SQL_LIST = ", ".join(f"'{value}'" for value in WEBHOOK_EVENT_TYPES)


def upgrade() -> None:
    bind = op.get_bind()

    outbox_status = postgresql.ENUM(
        *OUTBOX_STATUS_VALUES,
        name="outbox_event_status",
    )
    outbox_status.create(bind, checkfirst=True)

    op.create_table(
        "outbox_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "seq",
            sa.BigInteger(),
            sa.Identity(always=False, start=1),
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
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "audit_log_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audit_logs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                *OUTBOX_STATUS_VALUES,
                name="outbox_event_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'PENDING'::outbox_event_status"),
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            f"event_type IN ({_EVENT_TYPE_SQL_LIST})",
            name="event_type_vocabulary",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="attempts_non_negative",
        ),
        sa.CheckConstraint(
            "(status = 'CLAIMED'::outbox_event_status) "
            "= (claim_expires_at IS NOT NULL)",
            name="lease_matches_status",
        ),
        sa.CheckConstraint(
            "(status = 'PUBLISHED'::outbox_event_status) "
            "= (published_at IS NOT NULL)",
            name="published_at_matches_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="payload_is_object",
        ),
        sa.UniqueConstraint("seq", name="uq_outbox_events_seq"),
    )

    op.create_index(
        "ix_outbox_events_claimable",
        "outbox_events",
        ["available_at", "seq"],
        unique=False,
        postgresql_where=sa.text(
            "status IN ('PENDING'::outbox_event_status, "
            "'FAILED'::outbox_event_status)"
        ),
    )

    op.create_index(
        "ix_outbox_events_expired_leases",
        "outbox_events",
        ["claim_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'CLAIMED'::outbox_event_status"),
    )

    op.create_index(
        "ix_outbox_events_organization_id_created_at",
        "outbox_events",
        ["organization_id", sa.text("created_at DESC")],
        unique=False,
    )

    op.create_index(
        "ix_outbox_events_audit_log_id",
        "outbox_events",
        ["audit_log_id"],
        unique=False,
        postgresql_where=sa.text("audit_log_id IS NOT NULL"),
    )

    op.create_index(
        "uq_outbox_events_org_idempotency_key",
        "outbox_events",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_index(
        "ix_outbox_events_prunable",
        "outbox_events",
        ["published_at"],
        unique=False,
        postgresql_where=sa.text("status = 'PUBLISHED'::outbox_event_status"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_prunable", table_name="outbox_events")
    op.drop_index("uq_outbox_events_org_idempotency_key", table_name="outbox_events")
    op.drop_index("ix_outbox_events_audit_log_id", table_name="outbox_events")
    op.drop_index("ix_outbox_events_organization_id_created_at", table_name="outbox_events")
    op.drop_index("ix_outbox_events_expired_leases", table_name="outbox_events")
    op.drop_index("ix_outbox_events_claimable", table_name="outbox_events")
    op.drop_table("outbox_events")

    postgresql.ENUM(name="outbox_event_status").drop(
        op.get_bind(), checkfirst=True
    )