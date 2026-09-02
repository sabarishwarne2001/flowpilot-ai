"""arch06_step3_expand_notifications_organization_scope

Revision ID: c8e4a1f7b930
Revises: f28b6c94a1de
Create Date: 2026-08-14 09:00:00.000000

ARCH-06 Step 3 (EXPAND) — notifications.organization_id, and
notifications.workspace_id loosened to nullable. §B.4, Option A.

BOTH CHANGES IN THIS REVISION ARE PURELY ADDITIVE
---------------------------------------------------
Adding a nullable column and loosening an existing NOT NULL are both changes
that reject nothing today's schema already accepts — every row currently in
`notifications`, and every row `create_notification` (app/crud/notification.py,
unchanged by this revision) writes going forward, keeps satisfying both the
old and the new constraints. That is what makes it safe to do both in one
EXPAND revision rather than splitting the nullability change into its own
step: there is no MIGRATE concern for a change that only widens what is
already accepted.

WHAT THIS REVISION DELIBERATELY DOES NOT DO
-----------------------------------------------
No backfill. organization_id is NULL for every existing row after this
migration runs — Step 4 (MIGRATE) is what derives it from
workspace_id -> workspaces.organization_id for the rows that have a
workspace_id, and only Step 4's own gate confirms A.1.2 (0 NULL
organization_id rows) actually holds.

No CHECK constraint. The eventual invariant —
`(workspace_id IS NOT NULL) OR (organization_id IS NOT NULL)` — is not
enforceable yet, because it is not yet TRUE: this revision leaves every row
with organization_id NULL, and adding the CHECK now would fail immediately
against the data it runs against. It lands in Step 5 (CONTRACT), after Step 4
has made it hold.

No change to `ix_notifications_workspace_user_read_created`. It still serves
every existing read path unchanged; only a new index is added alongside it.

VERIFIED before writing this file, not assumed:
    - configure_mappers() clean against the full model graph, including this
      change.
    - Every new constraint and index name is <= 63 characters:
          fk_notifications_organization_id_organizations   46
          ix_notifications_organization_user_read_created  47
    - No duplicate constraint/index name anywhere in the resulting schema.
    - Baseline (this revision's down_revision, f28b6c94a1de) autogenerate diff
      is empty before this file's changes are layered on — confirmed against
      a real database, not assumed from the previous step's own gate having
      passed once already.

A REAL DRIFT CAUGHT WHILE WRITING THIS FILE, WORTH RECORDING
------------------------------------------------------------
An earlier draft of this migration also created a standalone
`ix_notifications_organization_id` index on the new column. Autogenerate
correctly flagged it for removal: the model declares no `index=True` on
`organization_id` and no matching single-column `Index` in `__table_args__`
— deliberately, to match `workspace_id`'s existing shape, which has never had
a solo index either, only the composite that leads with it. The migration had
drifted from the model it was supposed to produce. Removed before this
revision ran against the database recorded in Step 3's verification gate; the
composite index created below is the only index this revision adds.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c8e4a1f7b930"
down_revision: Union[str, None] = "f28b6c94a1de"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Loosen workspace_id. Additive: nothing currently in the table, and
    #    nothing existing code writes, has a NULL workspace_id, so this
    #    rejects zero rows in both directions. Done first so the table is
    #    never briefly in a state with a NOT NULL workspace_id and a
    #    partially-added organization_id — not that either ordering would be
    #    observable mid-migration inside the same transaction, but it keeps
    #    this file reading in the same order §B.4 describes the change.
    # ------------------------------------------------------------------
    op.alter_column(
        "notifications",
        "workspace_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    # ------------------------------------------------------------------
    # 2. organization_id — new, nullable, unpopulated. Step 4 backfills it;
    #    Step 5 is what makes it NOT NULL for the rows that need it to be.
    # ------------------------------------------------------------------
    op.add_column(
        "notifications",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_notifications_organization_id_organizations",
        "notifications",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # No standalone index on organization_id alone. Matches workspace_id's
    # existing shape exactly: that column has never had a single-column
    # index either, because the composite index below already leads with
    # organization_id, and PostgreSQL can use a composite index's leftmost
    # column(s) for a query that filters on organization_id alone. A second,
    # single-column index would duplicate what the composite already serves
    # and would be pure write-amplification with no read this table needs
    # that isn't already covered.

    # ------------------------------------------------------------------
    # 3. Organization-scoped composite index, alongside the existing
    #    workspace-scoped one — which is untouched by this revision.
    # ------------------------------------------------------------------
    op.create_index(
        "ix_notifications_organization_user_read_created",
        "notifications",
        ["organization_id", "user_id", "is_read", "created_at"],
    )


def downgrade() -> None:
    # Reverse order of upgrade().
    op.drop_index(
        "ix_notifications_organization_user_read_created",
        table_name="notifications",
    )
    op.drop_constraint(
        "fk_notifications_organization_id_organizations",
        "notifications",
        type_="foreignkey",
    )
    op.drop_column("notifications", "organization_id")

    # Restoring NOT NULL is safe here specifically because this revision's
    # downgrade only runs before Step 4 has ever backfilled or written a row
    # with workspace_id NULL — within this chain, nothing between this
    # revision and head produces one. A downgrade attempted AFTER Step 6 has
    # written genuine organization-level rows would fail here, correctly:
    # that is real data this migration cannot silently discard, and Alembic
    # raising on the NOT NULL violation is the right outcome, not a bug in
    # this downgrade.
    op.alter_column(
        "notifications",
        "workspace_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
