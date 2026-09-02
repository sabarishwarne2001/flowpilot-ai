"""arch04_step3_expand_invitation_tables

Revision ID: cb521957f5f0
Revises: c3f8a6b21d47
Create Date: 2026-08-10 10:41:59.485424

ARCH-04 Step 3 (EXPAND) - organization_invitations, invitation_workspace_grants,
organizations.seat_limit

Purely additive. Both new tables are created empty and in final form — there
is no existing data to migrate into them, so unlike organization_id under
ARCH-01 there is no nullable-then-tighten phase here. workspace_invitations is
not referenced by any statement in this revision; its 19 rows, 1 of them
PENDING, are untouched.
"""

from __future__ import annotations
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'cb521957f5f0'
down_revision: Union[str, None] = 'c3f8a6b21d47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ============================================================================
# Enum types — all three already exist. create_type=False on every one.
# ============================================================================

ORGANIZATION_ROLE = postgresql.ENUM(
    "OWNER", "ADMIN", "BILLING", "MEMBER",
    name="organization_role",
    create_type=False,
)
INVITATION_STATUS = postgresql.ENUM(
    "PENDING", "ACCEPTED", "REJECTED", "EXPIRED", "REVOKED",
    name="invitation_status",
    create_type=False,
)
WORKSPACE_ROLE = postgresql.ENUM(
    "ADMIN", "CONTRIBUTOR", "VIEWER",
    name="workspace_role",
    create_type=False,
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. organizations.seat_limit
    # ------------------------------------------------------------------
    op.add_column(
        "organizations",
        sa.Column("seat_limit", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_organizations_seat_limit_positive",
        "organizations",
        "seat_limit IS NULL OR seat_limit >= 1",
    )

    # ------------------------------------------------------------------
    # 2. organization_invitations
    # ------------------------------------------------------------------
    op.create_table(
        "organization_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inviter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invited_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("organization_role", ORGANIZATION_ROLE, nullable=False),
        sa.Column("status", INVITATION_STATUS, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("send_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_invitations"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_organization_invitations_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inviter_id"], ["users.id"],
            name="fk_organization_invitations_inviter_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_user_id"], ["users.id"],
            name="fk_organization_invitations_invited_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_id"], ["users.id"],
            name="fk_organization_invitations_revoked_by_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "organization_role <> 'OWNER'",
            name="ck_organization_invitations_role_not_owner",
        ),
    )

    op.create_index(
        "ix_organization_invitations_organization_id",
        "organization_invitations", ["organization_id"],
    )
    op.create_index(
        "ix_organization_invitations_inviter_id",
        "organization_invitations", ["inviter_id"],
    )
    op.create_index(
        "ix_organization_invitations_invited_user_id",
        "organization_invitations", ["invited_user_id"],
    )
    op.create_index(
        "ix_organization_invitations_email",
        "organization_invitations", ["email"],
    )
    op.create_index(
        "ix_organization_invitations_status",
        "organization_invitations", ["status"],
    )
    op.create_index(
        "ix_organization_invitations_token_hash",
        "organization_invitations", ["token_hash"],
        unique=True,
    )
    op.create_index(
        "uq_pending_organization_invitation",
        "organization_invitations",
        ["organization_id", sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_organization_invitations_organization_status",
        "organization_invitations", ["organization_id", "status"],
    )
    op.create_index(
        "ix_organization_invitations_status_expires_at",
        "organization_invitations", ["status", "expires_at"],
    )

    # ------------------------------------------------------------------
    # 3. invitation_workspace_grants
    # ------------------------------------------------------------------
    op.create_table(
        "invitation_workspace_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invitation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", WORKSPACE_ROLE, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invitation_workspace_grants"),
        sa.ForeignKeyConstraint(
            ["invitation_id"], ["organization_invitations.id"],
            name="fk_invitation_workspace_grants_invitation_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name="fk_invitation_workspace_grants_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "invitation_id", "workspace_id",
            name="uq_invitation_workspace_grant",
        ),
    )
    op.create_index(
        "ix_invitation_workspace_grants_invitation_id",
        "invitation_workspace_grants", ["invitation_id"],
    )
    op.create_index(
        "ix_invitation_workspace_grants_workspace_id",
        "invitation_workspace_grants", ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_table("invitation_workspace_grants")
    op.drop_table("organization_invitations")

    op.drop_constraint(
        "ck_organizations_seat_limit_positive", "organizations", type_="check"
    )
    op.drop_column("organizations", "seat_limit")
