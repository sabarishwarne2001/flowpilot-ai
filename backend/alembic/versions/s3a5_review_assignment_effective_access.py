"""Campaign session 3 — who may hold a review item or own an obligation follows effective access (F-296, N-063).

Revision ID: s3a5_review_assignment_effective_access
Revises: s3a4_identity_audit_vocabulary
Create Date: 2026-10-12

`review_assignments` referenced the stored workspace grant `(assignee_user_id, workspace_id)`,
which refused the organization's owners and admins (they administer every workspace without a
stored grant) and never released anything (a revoked grant is marked inactive, not deleted). The
key now references the user; the service checks effective access on every assignment and hands
items back when access ends.

Downgrade restores the old key, which cannot hold an assignment to someone without a stored
grant: those assignments are removed first (the items go back to the queue).

`obligations.owner_user_id` had the same shape (`fk_obligations_owner` onto the stored grant,
ON DELETE SET NULL); it keeps its name and now references the user (ON DELETE SET NULL). An
owner who loses access is cleared by the service, as the old key did for a deleted grant.
Downgrade clears owners without a stored grant before restoring the old key.
"""

from __future__ import annotations

from alembic import op

revision = "s3a5_review_assignment_effective_access"
down_revision = "s3a4_identity_audit_vocabulary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT fk_review_assignments_assignee_membership")
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT fk_review_assignments_assignee_user_id_users "
        "FOREIGN KEY (assignee_user_id) REFERENCES users (id) ON DELETE CASCADE"
    )
    op.execute("ALTER TABLE obligations DROP CONSTRAINT fk_obligations_owner")
    op.execute(
        "ALTER TABLE obligations ADD CONSTRAINT fk_obligations_owner "
        "FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE obligations DROP CONSTRAINT fk_obligations_owner")
    op.execute(
        "UPDATE obligations o SET owner_user_id = NULL WHERE owner_user_id IS NOT NULL AND NOT EXISTS ("
        " SELECT 1 FROM workspace_members m"
        " WHERE m.user_id = o.owner_user_id AND m.workspace_id = o.workspace_id)"
    )
    op.execute(
        "ALTER TABLE obligations ADD CONSTRAINT fk_obligations_owner FOREIGN KEY (owner_user_id, workspace_id) "
        "REFERENCES workspace_members (user_id, workspace_id) ON DELETE SET NULL (owner_user_id)"
    )
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT fk_review_assignments_assignee_user_id_users")
    op.execute(
        "DELETE FROM review_assignments a WHERE NOT EXISTS ("
        " SELECT 1 FROM workspace_members m"
        " WHERE m.user_id = a.assignee_user_id AND m.workspace_id = a.workspace_id)"
    )
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT fk_review_assignments_assignee_membership "
        "FOREIGN KEY (assignee_user_id, workspace_id) "
        "REFERENCES workspace_members (user_id, workspace_id) ON DELETE CASCADE"
    )
