"""arch07_step2_expand_audit_logs

Revision ID: b1d7c4e9a052
Revises: f7302be6a48d
Create Date: 2026-08-13 12:00:00.000000

ARCH-07 Step 2 (EXPAND) — audit_logs table, enums and indexes.

Purely additive. Creates two enum types, one table, four indexes and three
foreign keys.
"""

from __future__ import annotations
from typing import Sequence, Union

import uuid
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b1d7c4e9a052"
down_revision: Union[str, None] = "f7302be6a48d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AUDIT_RESOURCE_TYPE_VALUES = (
    "ORGANIZATION",
    "WORKSPACE",
    "MEMBERSHIP",
    "INVITATION",
    "OWNERSHIP_TRANSFER",
    "EMAIL_SETTINGS",
    "UPLOADED_FILE",
    "USER",
    "SESSION",
)

AUDIT_ACTION_VALUES = (
    "CREATED",
    "UPDATED",
    "DELETED",
    "ARCHIVED",
    "RESTORED",
    "ROLE_CHANGED",
    "ACCEPTED",
    "DECLINED",
    "REVOKED",
    "TRANSFERRED",
    "ENABLED",
    "DISABLED",
)

audit_resource_type = postgresql.ENUM(
    *AUDIT_RESOURCE_TYPE_VALUES,
    name="audit_resource_type",
    create_type=False,
)

audit_action = postgresql.ENUM(
    *AUDIT_ACTION_VALUES,
    name="audit_action",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    audit_resource_type.create(bind, checkfirst=True)
    audit_action.create(bind, checkfirst=True)

    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resource_type", audit_resource_type, nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", audit_action, nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_audit_logs_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_audit_logs_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_audit_logs_actor_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )

    op.create_index(
        "ix_audit_logs_organization_id_created_at",
        "audit_logs",
        ["organization_id", sa.text("created_at DESC")],
        unique=False,
    )

    op.create_index(
        "ix_audit_logs_organization_id_resource_type_resource_id",
        "audit_logs",
        ["organization_id", "resource_type", "resource_id"],
        unique=False,
    )

    op.create_index(
        "ix_audit_logs_organization_id_actor_id",
        "audit_logs",
        ["organization_id", "actor_id"],
        unique=False,
    )

    op.create_index(
        "ix_audit_logs_workspace_id",
        "audit_logs",
        ["workspace_id"],
        unique=False,
        postgresql_where=sa.text("workspace_id IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("ix_audit_logs_workspace_id", table_name="audit_logs")
    op.drop_index(
        "ix_audit_logs_organization_id_actor_id", table_name="audit_logs"
    )
    op.drop_index(
        "ix_audit_logs_organization_id_resource_type_resource_id",
        table_name="audit_logs",
    )
    op.drop_index(
        "ix_audit_logs_organization_id_created_at", table_name="audit_logs"
    )
    op.drop_table("audit_logs")

    audit_action.drop(bind, checkfirst=True)
    audit_resource_type.drop(bind, checkfirst=True)