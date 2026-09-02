"""arch05_step4_expand_ownership_transfer_and_profile

Revision ID: 3cdea80e19f3
Revises: c7c6830e4458
Create Date: 2026-08-11 09:15:00.000000

ARCH-05 Step 4 (EXPAND) — ownership_transfers, users.display_name,
users.timezone, users.locale.

THE ONLY MIGRATION THIS PHASE HAS. No MIGRATE, no CONTRACT.

    ownership_transfers is new and starts empty. There is no existing data to
    interpret into it, so unlike organization_id under ARCH-01 or
    email_verified_at under ARCH-03 there is no nullable-then-tighten phase
    here — every constraint on the table applies from row zero.

    The three `users` columns land on an EXISTING table with existing rows,
    but need no separate backfill step either: display_name stays NULLABLE
    (a NULL is the honest "never set" — see the model docstring), and
    timezone/locale are added NOT NULL WITH A DEFAULT in the same
    ADD COLUMN statement that creates them. PostgreSQL 11+ backfills a
    constant DEFAULT into existing rows as part of a single ADD COLUMN,
    without a table rewrite, which is exactly what removes the need for a
    MIGRATE leg here. The server-side default is then dropped once the
    column is populated — `d3b59e17d2a1_add_priority_to_automation_rules`
    is the existing precedent for this add-then-strip shape in this
    codebase — so that going forward the DEFAULT a new row receives comes
    from the ORM (`default="UTC"` / `default="en"` on the model), not from
    a DB-side rule three files away from the code that relies on it.

A NAME THIS REVISION DELIBERATELY DOES NOT USE

    The mechanical, naming-convention-derived name for the
    target_membership_id -> organization_members foreign key is:

        fk_ownership_transfers_target_membership_id_organization_members

    That is 64 characters — one over PostgreSQL's NAMEDATALEN-derived
    63-character identifier limit — verified directly against this
    codebase's own naming convention (app/db/base.py), not assumed. Left
    alone, PostgreSQL would not error; it would silently truncate to 63
    characters and drop the trailing "s" of "members", which is a
    duplicate-name incident waiting for a second constraint that happens to
    truncate to the same prefix. The constraint below is given the shorter,
    explicit name `fk_ownership_transfers_target_membership_id` instead,
    matching this table's model declaration and the existing precedent for
    the same situation: `organization_members.deactivated_by_id`'s own FK
    (ARCH-01 EXPAND) is likewise named without its referred-table suffix.

VERIFIED before writing this file, not assumed:
    - configure_mappers() clean against the full model graph.
    - Every constraint and index name on ownership_transfers, and across the
      ENTIRE metadata (134 named objects total), is <= 63 characters.
    - No duplicate constraint/index name anywhere in the schema.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3cdea80e19f3'
down_revision: Union[str, None] = 'c7c6830e4458'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ============================================================================
# Enum type — brand new, unlike organization_role/invitation_status/
# workspace_role, which all pre-date this revision and are declared with
# create_type=False because their CREATE TYPE already ran in an earlier
# migration. This one has no earlier migration, so THIS revision is where
# CREATE TYPE happens — explicitly, once, via .create() below, for the same
# reason ARCH-01's EXPAND revision gives: create_type=False on every column
# reference and a single explicit .create() call is what prevents
# SQLAlchemy from trying (and failing) to CREATE TYPE a second time when the
# table statement runs.
# ============================================================================

OWNERSHIP_TRANSFER_STATUS = postgresql.ENUM(
    "PENDING",
    "ACCEPTED",
    "DECLINED",
    "CANCELLED",
    "EXPIRED",
    name="ownership_transfer_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Enum type
    # ------------------------------------------------------------------
    OWNERSHIP_TRANSFER_STATUS.create(bind, checkfirst=True)

    # ------------------------------------------------------------------
    # 2. users — profile columns (ARCH-05 §A.2.4, §B.4)
    # ------------------------------------------------------------------
    op.add_column(
        "users",
        sa.Column("display_name", sa.String(length=100), nullable=True),
    )

    # timezone / locale: NOT NULL from creation, via add-with-default then
    # strip-the-server-default. The ADD COLUMN below backfills every existing
    # row to the literal in one statement; the ALTER COLUMN immediately after
    # removes the server-side default so future inserts are governed by the
    # ORM's `default=` instead of a rule living only in the database.
    op.add_column(
        "users",
        sa.Column(
            "timezone",
            sa.String(length=100),
            nullable=False,
            server_default="UTC",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "locale",
            sa.String(length=20),
            nullable=False,
            server_default="en",
        ),
    )
    op.alter_column("users", "timezone", server_default=None)
    op.alter_column("users", "locale", server_default=None)

    # ------------------------------------------------------------------
    # 3. ownership_transfers
    # ------------------------------------------------------------------
    op.create_table(
        "ownership_transfers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "initiated_by_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "target_membership_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("status", OWNERSHIP_TRANSFER_STATUS, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_ownership_transfers"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ownership_transfers_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by_id"],
            ["users.id"],
            name="fk_ownership_transfers_initiated_by_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_membership_id"],
            ["organization_members.id"],
            # Explicit short name. See the module docstring — the
            # naming-convention default here is 64 characters, one over
            # PostgreSQL's limit.
            name="fk_ownership_transfers_target_membership_id",
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_ownership_transfers_organization_id",
        "ownership_transfers", ["organization_id"],
    )
    op.create_index(
        "ix_ownership_transfers_initiated_by_id",
        "ownership_transfers", ["initiated_by_id"],
    )
    op.create_index(
        "ix_ownership_transfers_target_membership_id",
        "ownership_transfers", ["target_membership_id"],
    )
    op.create_index(
        "ix_ownership_transfers_status",
        "ownership_transfers", ["status"],
    )

    # §B.9 / §C Step 3. One PENDING transfer per organization, the
    # uq_pending_organization_invitation pattern from ARCH-04.
    op.create_index(
        "uq_pending_ownership_transfer_per_org",
        "ownership_transfers",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    # Anticipates the transfer-history list view implied by §B.8 ("filtered
    # from list views"), the same way organization_invitations'
    # (organization_id, status) index anticipated its Step 7 list endpoint.
    op.create_index(
        "ix_ownership_transfers_organization_status",
        "ownership_transfers", ["organization_id", "status"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # ownership_transfers — indexes are dropped implicitly with the table;
    # only the table statement itself is needed.
    # ------------------------------------------------------------------
    op.drop_table("ownership_transfers")

    # ------------------------------------------------------------------
    # Enum type — after the table that depends on it.
    # ------------------------------------------------------------------
    OWNERSHIP_TRANSFER_STATUS.drop(bind, checkfirst=True)

    # ------------------------------------------------------------------
    # users — profile columns
    # ------------------------------------------------------------------
    op.drop_column("users", "locale")
    op.drop_column("users", "timezone")
    op.drop_column("users", "display_name")
