"""Restore workspace_invitations — RECONSTRUCTION of purged bb57122ca97e.

This revision restores the CREATE for workspace_invitations so the migration
chain replays cleanly on fresh databases.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "bb57122ca97e"
down_revision = "b13c7b21bec9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    invitation_status = postgresql.ENUM(
        "PENDING", "ACCEPTED", "REJECTED", "EXPIRED", "REVOKED",
        name="invitation_status",
    )
    invitation_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "workspace_invitations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING", "ACCEPTED", "REJECTED", "EXPIRED", "REVOKED",
                name="invitation_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'PENDING'::invitation_status"),
        ),
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "inviter_id",
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

    op.create_index(
        "ix_workspace_invitations_workspace_id",
        "workspace_invitations",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_invitations_email",
        "workspace_invitations",
        ["email"],
    )
    op.create_index(
        "ix_workspace_invitations_token",
        "workspace_invitations",
        ["token"],
    )
    op.create_index(
        "uq_pending_invitation",
        "workspace_invitations",
        ["workspace_id", "email"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("uq_pending_invitation", table_name="workspace_invitations")
    op.drop_index("ix_workspace_invitations_token", table_name="workspace_invitations")
    op.drop_index("ix_workspace_invitations_email", table_name="workspace_invitations")
    op.drop_index(
        "ix_workspace_invitations_workspace_id", table_name="workspace_invitations"
    )
    op.drop_table("workspace_invitations")
