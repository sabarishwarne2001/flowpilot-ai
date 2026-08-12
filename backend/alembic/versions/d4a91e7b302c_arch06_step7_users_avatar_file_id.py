"""arch06_step7_users_avatar_file_id

Revision ID: d4a91e7b302c
Revises: b6f03a7c581e
Create Date: 2026-08-18 09:00:00.000000

ARCH-06 Step 7 — users.avatar_file_id.

A DECISION THAT WAS NOT IN THE PLAN, MADE HERE AND FLAGGED FOR REVIEW
------------------------------------------------------------------------
Step 7 was scoped as "avatar upload service, streaming endpoint, tests" —
service work, no schema. It cannot be, and the reason is worth stating
plainly rather than discovering in review:

  - `users` has no avatar column. The model says so deliberately: "avatar_url
    was considered and deliberately excluded (§B.4). Upload infrastructure
    already exists elsewhere in the product, so it is possible — but it
    brings image validation, storage lifecycle, and a moderation question
    that belong to their own change." Step 7 IS that change; the exclusion
    was scoped to ARCH-05, not permanent.

  - `uploaded_files` (Step 5) has no `purpose`/`kind` discriminator. So
    "this user's current avatar" is not expressible against it either: a
    query on owner_id alone cannot distinguish an avatar from any other file
    that user uploaded.

Two ways to close that gap:

  A. `users.avatar_file_id` -> uploaded_files.id. A pointer column.
  B. A `purpose` enum on `uploaded_files`, with "current" derived as the
     newest non-deleted row for (owner, purpose).

**Option A, taken here.** Three reasons:

  1. "Current" is a fact, not a derivation. Under B, two rows momentarily
     satisfying (owner, AVATAR, deleted_at IS NULL) — an upload that raced a
     cleanup, a cleanup that half-failed — leave "which is current" decided
     by ORDER BY. Under A the pointer is single-valued by construction.
  2. It matches the pattern already in this codebase.
     `workspaces.company_logo_url` is exactly this: a pointer column on the
     owning row naming the current file, which ARCH-06 Step 1b relied on as
     the ownership record. A is that pattern with a foreign key instead of a
     string, which is strictly better.
  3. ON DELETE SET NULL makes deletion coherent for free. Removing the
     `uploaded_files` row clears the pointer in the same statement, so a user
     can never point at a file record that no longer exists.

Option B is still worth adding later — a `purpose` column would make the
orphan sweeper and per-purpose quotas easier — but it is not a substitute
for the pointer, and this step does not need it.

**This is a §B-class decision made under a "continue" instruction rather
than with explicit sign-off.** If the intent was B, this revision and
`avatar_service.resolve_current` are the only two places that change.

WHY ondelete="SET NULL" AND NOT CASCADE
------------------------------------------
Every other FK added by ARCH-06 uses CASCADE, so the exception needs a
reason. CASCADE here would mean: deleting a file record deletes the USER.
That is obviously wrong — the child is the file, the parent is the person.
SET NULL expresses the actual relationship: the user survives, and stops
having an avatar. `organization_invitations.revoked_by_id` uses SET NULL for
the same shape of reason.

Nullable, and stays nullable. Most users will never set an avatar, and
`display_name`'s own docstring already established this codebase's position
on inventing defaults for absent profile data: "a NULL that a caller renders
as the email address is honest, where a derived default is a guess the
product would then treat as a fact." An identicon is a rendering decision,
not a stored one.

VERIFIED before writing this file:
    - fk_users_avatar_file_id_uploaded_files = 39 chars, under the 63 limit.
    - configure_mappers() clean with the relationship declared.
    - No circular-FK problem: uploaded_files.owner_id -> users.id and
      users.avatar_file_id -> uploaded_files.id form a cycle, but both are
      nullable-or-deferred at the row level -- an avatar row is always
      INSERTed before the pointer is UPDATEd to it, never in one statement.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4a91e7b302c"
down_revision: Union[str, None] = "b6f03a7c581e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("avatar_file_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_avatar_file_id_uploaded_files",
        "users",
        "uploaded_files",
        ["avatar_file_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # No standalone index. The column is read via a loaded User row (the
    # pointer is followed, never searched by), so an index would serve no
    # query this application makes -- the same reasoning that kept
    # notifications.organization_id from getting a solo index in Step 3.


def downgrade() -> None:
    op.drop_constraint(
        "fk_users_avatar_file_id_uploaded_files", "users", type_="foreignkey"
    )
    op.drop_column("users", "avatar_file_id")