"""arch06_step5_contract_notifications_scope_and_uploaded_files

Revision ID: e1c9a7f42d63
Revises: 9b2f6d8e14a7
Create Date: 2026-08-16 09:00:00.000000

ARCH-06 Step 5 (CONTRACT) — notifications.has_scope CHECK constraint, and
the new uploaded_files table. §B.4 closing leg + §B.6.

TWO INDEPENDENT CHANGES IN ONE REVISION, DELIBERATELY
---------------------------------------------------------
Neither depends on the other, but both are the last unshipped piece of
their respective decision (§B.4's notifications scope work started at Step
3; §B.6's upload tracking work was named but deferred at Step 1b), and both
are small enough that splitting them into two revisions would only double
the ceremony without buying independent rollback: there is no scenario where
you would want the CHECK constraint without uploaded_files or vice versa
without also reverting whatever shipped after it.

THE CHECK CONSTRAINT IS THE OR FORM, NOT organization_id NOT NULL
-----------------------------------------------------------------------
Read this before assuming the approved plan's one-line Step 5 summary
("organization_id -> NOT NULL; workspace_id -> nullable") is what this
revision implements. It is not, and implementing it literally would be a
self-inflicted outage:

    SELECT count(*) FROM notifications;                              -- N
    SELECT count(*) FROM notifications WHERE organization_id IS NULL; -- 0, after Step 4

Every EXISTING row satisfies organization_id NOT NULL today, because Step 4
backfilled it. But `create_notification` (app/crud/notification.py) has not
been touched by any step so far and does not set organization_id on the rows
it writes — meaning the FIRST notification created by today's application
code, at any point after this migration runs, would violate a NOT NULL
organization_id constraint and fail. The CHECK constraint below —
`workspace_id IS NOT NULL OR organization_id IS NOT NULL` — is satisfied by
workspace_id alone, which create_notification has always set, so it is the
strongest constraint actually true of both the historical data AND every row
today's code can currently produce. Tightening to organization_id NOT NULL
is real future work, correctly gated on first updating the write path to
populate it unconditionally — not a corner this revision cuts, a dependency
this revision's author identified and is naming rather than hiding.

LOCKING: A PLAIN ADD CONSTRAINT, NOT NOT VALID + VALIDATE — ON PURPOSE
------------------------------------------------------------------------
PostgreSQL validates a new CHECK constraint against every existing row,
under an ACCESS EXCLUSIVE lock for the statement's duration. On a table of
consequence the disciplined pattern is `ADD CONSTRAINT ... NOT VALID`
followed by a separate `VALIDATE CONSTRAINT` statement, which trades one
brief exclusive lock for a longer scan under the much weaker SHARE UPDATE
EXCLUSIVE — but only if the two statements run in SEPARATE transactions.
Alembic runs an entire `upgrade()` in one transaction by default, and a lock
acquired inside a transaction is held until that transaction commits
regardless of what happens after it — so issuing `NOT VALID` and
`VALIDATE CONSTRAINT` back to back inside the same migration function would
hold the exclusive lock for the full validation scan anyway, buying none of
the intended benefit while adding two statements and a rollback edge case
for the reviewer to reason about instead of one.

Getting the real benefit requires running `VALIDATE CONSTRAINT` in its own
transaction — Alembic's `autocommit_block()` — which trades this migration's
atomicity for a lock-duration improvement.
`84251cd213bd` (ARCH-02 CONTRACT) already made this exact call, for the
identical situation on a larger set of columns, and named the trade
explicitly: "losing atomicity is a worse trade than a brief lock on a table
of ten rows." Notifications is smaller (15 rows per A.1.3). The plain,
single-statement `ADD CONSTRAINT` below is that same call, not an oversight —
matched to this codebase's own precedent rather than a generic "always split
it" rule that ARCH-02's CONTRACT revision already considered and rejected at
this scale.

uploaded_files IS A PLAIN EXPAND ON ITS OWN
------------------------------------------------
Considered as its own change, creating a new, empty, unreferenced table is
additive — nothing reads or writes it yet (see the model's own docstring:
wiring `upload.py` to it is Step 7's job). It is included in this CONTRACT
revision because it is the deliverable this step's request bundles it with,
not because it needed CONTRACT's stronger guarantees.

VERIFIED before writing this file, not assumed:
    - configure_mappers() clean against the full model graph, including
      UploadedFile.
    - Every new constraint and index name is <= 63 characters — the longest,
      fk_uploaded_files_organization_id_organizations, is 47.
    - No duplicate constraint/index name anywhere in the resulting schema.
    - The has_scope CHECK validates cleanly against the live, Step-4-backfilled
      notifications table with zero pre-existing violations.

A REAL DOUBLE-PREFIX BUG CAUGHT WHILE WRITING THIS FILE — THE EXACT CLASS
STEP 1a EXISTS TO FIX
--------------------------------------------------------------------------
An earlier draft called `op.create_check_constraint("ck_notifications_has_scope",
"notifications", ...)` — passing the FULLY QUALIFIED name, prefix included.
In this codebase's Alembic setup, `op.create_check_constraint`'s name
argument is resolved through the same metadata naming convention
(`app/db/base.py`'s `"ck": "ck_%(table_name)s_%(constraint_name)s"`) that
resolves the model's own `CheckConstraint(name="has_scope")` declaration —
it is not treated as an already-final name. Passing an already-prefixed
string produced `ck_notifications_ck_notifications_has_scope`: the identical
double-prefix shape as the two constraints Step 1a renamed, reintroduced by
this revision rather than inherited from one before it.

Caught by inspecting the live output of psql's describe command against
notifications, run against a real database, NOT by
`alembic revision --autogenerate` — which returned an empty
diff both before and after the fix. That is expected and consistent with
Step 1a's own finding: Alembic's autogenerate comparator does not include
check constraints at all, so a double-prefixed (or entirely wrong) check
constraint name is invisible to it in both directions. The only real
detection for this class of bug is a live catalog query, exactly like
A.1.6's. `op.drop_constraint` in `downgrade()` required the identical
short-name fix — it resolves its name argument through the same convention,
so a downgrade written against the fully-qualified name would fail with
"constraint does not exist" against the correctly-named constraint this
revision actually creates. Fixed to `op.create_check_constraint("has_scope",
...)` and `op.drop_constraint("has_scope", ..., type_="check")` below;
verified against a fresh database that the resulting name is
`ck_notifications_has_scope` — once, not twice.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e1c9a7f42d63"
down_revision: Union[str, None] = "9b2f6d8e14a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. notifications.has_scope — a single ADD CONSTRAINT, matching
    #    84251cd213bd's precedent for this exact trade-off. See the module
    #    docstring for why NOT VALID + VALIDATE would not actually reduce
    #    lock duration inside one Alembic transaction, and why splitting it
    #    into two transactions to get the real benefit is not worth this
    #    revision's atomicity at 15 rows.
    # ------------------------------------------------------------------
    op.create_check_constraint(
        "has_scope",
        "notifications",
        "workspace_id IS NOT NULL OR organization_id IS NOT NULL",
    )

    # ------------------------------------------------------------------
    # 2. uploaded_files
    # ------------------------------------------------------------------
    op.create_table(
        "uploaded_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_uploaded_files"),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_uploaded_files_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_uploaded_files_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_uploaded_files_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_uploaded_files_owner_id",
        "uploaded_files",
        ["owner_id"],
    )
    op.create_index(
        "ix_uploaded_files_organization_id",
        "uploaded_files",
        ["organization_id"],
    )
    op.create_index(
        "ix_uploaded_files_workspace_id",
        "uploaded_files",
        ["workspace_id"],
    )
    op.create_index(
        "ix_uploaded_files_checksum_sha256",
        "uploaded_files",
        ["checksum_sha256"],
    )
    op.create_index(
        "ix_uploaded_files_deleted_at",
        "uploaded_files",
        ["deleted_at"],
    )


def downgrade() -> None:
    # Reverse order of upgrade().
    op.drop_index("ix_uploaded_files_deleted_at", table_name="uploaded_files")
    op.drop_index("ix_uploaded_files_checksum_sha256", table_name="uploaded_files")
    op.drop_index("ix_uploaded_files_workspace_id", table_name="uploaded_files")
    op.drop_index("ix_uploaded_files_organization_id", table_name="uploaded_files")
    op.drop_index("ix_uploaded_files_owner_id", table_name="uploaded_files")
    op.drop_table("uploaded_files")

    op.drop_constraint(
        "has_scope",
        "notifications",
        type_="check",
    )