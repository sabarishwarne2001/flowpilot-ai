"""arch06_step2_expand_email_change_requests

Revision ID: f28b6c94a1de
Revises: a4d17c9e2b58
Create Date: 2026-08-13 09:00:00.000000

ARCH-06 Step 2 (EXPAND) — email_change_requests.

THE ONLY MIGRATION THIS STEP HAS. No MIGRATE, no CONTRACT.

    email_change_requests is new and starts empty. There is no existing data
    to interpret into it — unlike notifications.organization_id (Steps 3-5,
    later in this phase) or email_verified_at under ARCH-03, there is no
    nullable-then-tighten sequence here. Every constraint applies from row
    zero, exactly matching 3cdea80e19f3's identical note for
    ownership_transfers.

VERIFIED before writing this file, not assumed:
    - configure_mappers() clean against the full model graph, including this
      table.
    - Every constraint and index name on email_change_requests is <= 63
      characters. Longest: fk_email_change_requests_user_id_users, 38 — the
      exact count the ARCH-06 plan asserted rather than computed, checked
      here programmatically rather than trusted a second time:

          pk_email_change_requests                     24
          fk_email_change_requests_user_id_users        38
          ix_email_change_requests_user_id              32
          ix_email_change_requests_token_hash            35
          ix_email_change_requests_status                31
          ix_email_change_requests_user_status            36
          uq_pending_email_change_per_user                32

    - No duplicate constraint/index name anywhere in the schema after this
      revision — checked against the full live catalog, not just the names
      declared in this file, since Step 1 (a4d17c9e2b58) exists precisely
      because two names collided without Alembic ever seeing it.
    - No prior migration in this chain creates a type named
      `email_change_status`.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f28b6c94a1de"
down_revision: Union[str, None] = "a4d17c9e2b58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ============================================================================
# Enum type — brand new, matching ownership_transfer_status's situation
# (3cdea80e19f3) rather than the pre-existing types ARCH-01 aligned. This
# revision is where CREATE TYPE happens, once, via .create() below;
# create_type=False on the column reference is what stops SQLAlchemy
# attempting a second CREATE TYPE when the table statement runs.
# ============================================================================

EMAIL_CHANGE_STATUS = postgresql.ENUM(
    "PENDING",
    "COMPLETED",
    "CANCELLED",
    "EXPIRED",
    name="email_change_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Enum type
    # ------------------------------------------------------------------
    EMAIL_CHANGE_STATUS.create(bind, checkfirst=True)

    # ------------------------------------------------------------------
    # 2. email_change_requests
    # ------------------------------------------------------------------
    op.create_table(
        "email_change_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("new_email", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("status", EMAIL_CHANGE_STATUS, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_email_change_requests"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_email_change_requests_user_id_users",
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_email_change_requests_user_id",
        "email_change_requests",
        ["user_id"],
    )

    # Unique, matching auth_tokens.token_hash's precedent exactly: a single
    # global lookup key, where uniqueness is what makes a hash collision or
    # an accidental double-issue impossible to persist rather than merely
    # unlikely. unique=True is passed here rather than declared as a separate
    # UniqueConstraint, because that is how auth_tokens' identical column was
    # created (a1c7f39b4e2d) and how the ORM model above declares it
    # (index=True, unique=True on the mapped_column) — one named object, not
    # two competing ones over the same column.
    op.create_index(
        "ix_email_change_requests_token_hash",
        "email_change_requests",
        ["token_hash"],
        unique=True,
    )

    op.create_index(
        "ix_email_change_requests_status",
        "email_change_requests",
        ["status"],
    )

    # Serves confirm_email_change's "does this user have anything else
    # pending" check and any future change-history read. Anticipates the read
    # the same way ix_ownership_transfers_organization_status anticipated its
    # own list view: the partial index below cannot serve a query that needs
    # a non-PENDING row.
    op.create_index(
        "ix_email_change_requests_user_status",
        "email_change_requests",
        ["user_id", "status"],
    )

    # §B.3/§B.9 (ARCH-06 Section C Step 2). One PENDING request per user,
    # directly the uq_pending_ownership_transfer_per_org pattern (3cdea80e19f3)
    # — the WHERE literal is the enum member's own string value.
    op.create_index(
        "uq_pending_email_change_per_user",
        "email_change_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Indexes are dropped implicitly with the table; only the table statement
    # itself is needed, matching 3cdea80e19f3's downgrade().
    op.drop_table("email_change_requests")

    # Enum type — after the table that depends on it.
    EMAIL_CHANGE_STATUS.drop(bind, checkfirst=True)
