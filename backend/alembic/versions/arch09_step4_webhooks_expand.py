"""ARCH-09 Step 4 — webhook_endpoints & webhook_deliveries (EXPAND)

Revision ID: arch09_step4_webhooks_expand
Revises: arch09_step2_outbox_expand
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch09_step4_webhooks_expand"
down_revision = "arch09_step2_outbox_expand"
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

WEBHOOK_ENDPOINT_STATUS_VALUES: tuple[str, ...] = ("ACTIVE", "DISABLED")
WEBHOOK_DELIVERY_STATUS_VALUES: tuple[str, ...] = (
    "PENDING",
    "CLAIMED",
    "DELIVERED",
    "FAILED",
    "DEAD",
)

_EVENT_TYPE_SQL_ARRAY = ", ".join(f"'{v}'" for v in WEBHOOK_EVENT_TYPES)


def upgrade() -> None:
    bind = op.get_bind()

    endpoint_status = postgresql.ENUM(
        *WEBHOOK_ENDPOINT_STATUS_VALUES, name="webhook_endpoint_status"
    )
    endpoint_status.create(bind, checkfirst=True)

    delivery_status = postgresql.ENUM(
        *WEBHOOK_DELIVERY_STATUS_VALUES, name="webhook_delivery_status"
    )
    delivery_status.create(bind, checkfirst=True)

    # 1. webhook_endpoints
    op.create_table(
        "webhook_endpoints",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
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
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "event_types",
            postgresql.ARRAY(sa.String()),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                *WEBHOOK_ENDPOINT_STATUS_VALUES,
                name="webhook_endpoint_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'ACTIVE'::webhook_endpoint_status"),
        ),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "disabled_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("disabled_reason", sa.Text(), nullable=True),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("previous_secret_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "previous_secret_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "secret_last_rotated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
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
    )

    op.execute("ALTER TABLE webhook_endpoints ADD CONSTRAINT ck_webhook_endpoints_https_only CHECK (url LIKE 'https://%')")
    op.execute("ALTER TABLE webhook_endpoints ADD CONSTRAINT ck_webhook_endpoints_event_types_non_empty CHECK (cardinality(event_types) >= 1)")
    op.execute(f"ALTER TABLE webhook_endpoints ADD CONSTRAINT ck_webhook_endpoints_event_types_vocabulary CHECK (event_types <@ ARRAY[{_EVENT_TYPE_SQL_ARRAY}]::varchar[])")
    op.execute("ALTER TABLE webhook_endpoints ADD CONSTRAINT ck_webhook_endpoints_disabled_at_matches_status CHECK ((status = 'DISABLED'::webhook_endpoint_status) = (disabled_at IS NOT NULL))")
    op.execute("ALTER TABLE webhook_endpoints ADD CONSTRAINT ck_webhook_endpoints_previous_secret_paired CHECK ((previous_secret_encrypted IS NULL) = (previous_secret_expires_at IS NULL))")

    op.create_index(
        "ix_webhook_endpoints_organization_id",
        "webhook_endpoints",
        ["organization_id"],
    )
    op.create_index(
        "ix_webhook_endpoints_event_types_active",
        "webhook_endpoints",
        ["event_types"],
        postgresql_using="gin",
        postgresql_where=sa.text("status = 'ACTIVE'::webhook_endpoint_status"),
    )

    # 2. webhook_deliveries
    op.create_table(
        "webhook_deliveries",
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
            "webhook_endpoint_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "outbox_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outbox_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                *WEBHOOK_DELIVERY_STATUS_VALUES,
                name="webhook_delivery_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'PENDING'::webhook_delivery_status"),
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
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_response_status", sa.Integer(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("seq", name="uq_webhook_deliveries_seq"),
    )

    op.execute(f"ALTER TABLE webhook_deliveries ADD CONSTRAINT ck_webhook_deliveries_event_type_vocabulary CHECK (event_type IN ({_EVENT_TYPE_SQL_ARRAY}))")
    op.execute("ALTER TABLE webhook_deliveries ADD CONSTRAINT ck_webhook_deliveries_attempts_non_negative CHECK (attempts >= 0)")
    op.execute("ALTER TABLE webhook_deliveries ADD CONSTRAINT ck_webhook_deliveries_lease_matches_status CHECK ((status = 'CLAIMED'::webhook_delivery_status) = (claim_expires_at IS NOT NULL))")
    op.execute("ALTER TABLE webhook_deliveries ADD CONSTRAINT ck_webhook_deliveries_delivered_at_matches_status CHECK ((status = 'DELIVERED'::webhook_delivery_status) = (delivered_at IS NOT NULL))")
    op.execute("ALTER TABLE webhook_deliveries ADD CONSTRAINT ck_webhook_deliveries_payload_is_object CHECK (jsonb_typeof(payload) = 'object')")

    op.create_index(
        "ix_webhook_deliveries_claimable",
        "webhook_deliveries",
        ["available_at", "seq"],
        postgresql_where=sa.text(
            "status IN ('PENDING'::webhook_delivery_status, "
            "'FAILED'::webhook_delivery_status)"
        ),
    )
    op.create_index(
        "ix_webhook_deliveries_expired_leases",
        "webhook_deliveries",
        ["claim_expires_at"],
        postgresql_where=sa.text("status = 'CLAIMED'::webhook_delivery_status"),
    )
    op.create_index(
        "ix_webhook_deliveries_endpoint_id_created_at",
        "webhook_deliveries",
        ["webhook_endpoint_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_webhook_deliveries_organization_id_created_at",
        "webhook_deliveries",
        ["organization_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "uq_webhook_deliveries_outbox_event_endpoint",
        "webhook_deliveries",
        ["outbox_event_id", "webhook_endpoint_id"],
        unique=True,
        postgresql_where=sa.text("outbox_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_webhook_deliveries_outbox_event_endpoint", table_name="webhook_deliveries"
    )
    op.drop_index(
        "ix_webhook_deliveries_organization_id_created_at",
        table_name="webhook_deliveries",
    )
    op.drop_index(
        "ix_webhook_deliveries_endpoint_id_created_at", table_name="webhook_deliveries"
    )
    op.drop_index("ix_webhook_deliveries_expired_leases", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_claimable", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")

    op.drop_index("ix_webhook_endpoints_event_types_active", table_name="webhook_endpoints")
    op.drop_index("ix_webhook_endpoints_organization_id", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")

    postgresql.ENUM(name="webhook_delivery_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="webhook_endpoint_status").drop(op.get_bind(), checkfirst=True)
