"""add workspace invitations

Revision ID: bb57122ca97e
Revises: b13c7b21bec9
Create Date: 2026-08-01 19:12:39.454845
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "bb57122ca97e"
down_revision: Union[str, None] = "b13c7b21bec9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Reusable existing workspace role enum (must not be created/dropped by this migration)
workspace_role_enum = postgresql.ENUM(
    "OWNER",
    "MANAGER",
    "CONTRIBUTOR",
    "VIEWER",
    name="workspace_role",
    create_type=False,
)

# New invitation status enum introduced by this migration
invitation_status_enum = postgresql.ENUM(
    "PENDING",
    "ACCEPTED",
    "REJECTED",
    "EXPIRED",
    "REVOKED",
    name="invitation_status",
    create_type=False,
)


def upgrade() -> None:
    # Safely create custom PostgreSQL Enum for Invitation Statuses
    invitation_status_enum.create(op.get_bind(), checkfirst=True)

    # Create the workspace_invitations table
    op.create_table(
        "workspace_invitations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("inviter_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", workspace_role_enum, nullable=False),
        sa.Column("status", invitation_status_enum, nullable=False),
        sa.Column("token", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inviter_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Performance indexes
    op.create_index(
        op.f("ix_workspace_invitations_workspace_id"),
        "workspace_invitations",
        ["workspace_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_workspace_invitations_inviter_id"),
        "workspace_invitations",
        ["inviter_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_workspace_invitations_email"),
        "workspace_invitations",
        ["email"],
        unique=False,
    )

    op.create_index(
        op.f("ix_workspace_invitations_status"),
        "workspace_invitations",
        ["status"],
        unique=False,
    )

    op.create_index(
        op.f("ix_workspace_invitations_token"),
        "workspace_invitations",
        ["token"],
        unique=True,
    )

    # Partial Unique Index on (workspace_id, email) where status = 'PENDING'
    op.create_index(
        "uq_pending_invitation",
        "workspace_invitations",
        ["workspace_id", "email"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    # 1. Drop partial unique index
    op.drop_index(
        "uq_pending_invitation",
        table_name="workspace_invitations",
    )

    # 2. Drop standard indexes
    op.drop_index(
        op.f("ix_workspace_invitations_workspace_id"),
        table_name="workspace_invitations",
    )

    op.drop_index(
        op.f("ix_workspace_invitations_token"),
        table_name="workspace_invitations",
    )

    op.drop_index(
        op.f("ix_workspace_invitations_status"),
        table_name="workspace_invitations",
    )

    op.drop_index(
        op.f("ix_workspace_invitations_inviter_id"),
        table_name="workspace_invitations",
    )

    op.drop_index(
        op.f("ix_workspace_invitations_email"),
        table_name="workspace_invitations",
    )

    # 3. Drop main table
    op.drop_table("workspace_invitations")

    # 4. Safely drop invitation_status Enum type (does not drop workspace_role)
    invitation_status_enum.drop(
        op.get_bind(),
        checkfirst=True,
    )