"""arch04_step5b_contract_drop_workspace_invitations

Revision ID: c7c6830e4458
Revises: f3c137bf53d5
Create Date: 2026-08-10 18:53:53.100509

ARCH-04 Step 5B (CONTRACT) - drop workspace_invitations

Destructive. Drops workspace_invitations after organization_invitations cutover.
"""

from __future__ import annotations
from typing import Sequence, Union

import logging

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c7c6830e4458'
down_revision: Union[str, None] = 'f3c137bf53d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.arch04.step5b")

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
    conn = op.get_bind()

    unmigrated = conn.execute(sa.text("""
        SELECT count(*) FROM workspace_invitations wi
        WHERE NOT EXISTS (
            SELECT 1 FROM organization_invitations oi WHERE oi.id = wi.id
        )
    """)).scalar_one()

    if unmigrated:
        raise RuntimeError(
            f"ARCH-04 Step 5B CONTRACT aborted: {unmigrated} row(s) in "
            f"workspace_invitations have no counterpart in "
            f"organization_invitations. Re-run the Step 4 backfill."
        )

    total = conn.execute(
        sa.text("SELECT count(*) FROM workspace_invitations")
    ).scalar_one()
    logger.info(
        "ARCH-04 Step 5B: dropping workspace_invitations (%s row(s)).", total
    )

    op.drop_table("workspace_invitations")


def downgrade() -> None:
    op.create_table(
        "workspace_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("inviter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", WORKSPACE_ROLE, nullable=False),
        sa.Column("status", INVITATION_STATUS, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspace_invitations"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"],
            name="fk_workspace_invitations_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_workspace_invitations_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inviter_id"], ["users.id"],
            name="fk_workspace_invitations_inviter_id_users",
            ondelete="CASCADE",
        ),
    )
    for column in (
        "workspace_id", "organization_id", "inviter_id", "email", "status",
    ):
        op.create_index(
            f"ix_workspace_invitations_{column}",
            "workspace_invitations", [column],
        )
    op.create_index(
        "ix_workspace_invitations_token_hash", "workspace_invitations",
        ["token_hash"], unique=True,
    )
    op.create_index(
        "uq_pending_invitation", "workspace_invitations",
        ["workspace_id", "email"], unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.execute(sa.text("""
        INSERT INTO workspace_invitations (
            id, workspace_id, organization_id, inviter_id, email, role,
            status, token_hash, expires_at, accepted_at, rejected_at,
            revoked_at, created_at, updated_at
        )
        SELECT oi.id, g.workspace_id, oi.organization_id, oi.inviter_id,
               oi.email, g.role, oi.status, oi.token_hash, oi.expires_at,
               oi.accepted_at, oi.rejected_at, oi.revoked_at,
               oi.created_at, oi.updated_at
        FROM organization_invitations oi
        JOIN invitation_workspace_grants g ON g.invitation_id = oi.id
        ON CONFLICT (id) DO NOTHING
    """))