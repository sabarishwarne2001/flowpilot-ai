"""Campaign session 1 — a calendar feed belongs to an organization member, not a workspace grant.

Revision ID: s1b1_calendar_feed_org_member
Revises: s1a1_upload_session_part_sizes
Create Date: 2026-10-11

Organization owners and admins are admins of every workspace without an explicit grant
(`resolve_effective_workspace_role`). The feed table's foreign key pointed at the explicit
grant, so an owner or admin subscribing to a workspace's obligations calendar was answered a
409 "changed by another request" (F-249). The feed now references the organization
membership (removing a member still deletes their feeds); access to the workspace itself is
checked when the feed is issued and again on every poll.

Downgrade restores the old key, which cannot hold a feed issued through an implicit grant:
those feeds are deleted first (their owner can issue a new one).
"""

from __future__ import annotations

from alembic import op

revision = "s1b1_calendar_feed_org_member"
down_revision = "s1a1_upload_session_part_sizes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE calendar_feed_tokens DROP CONSTRAINT fk_calendar_feed_tokens_member")
    op.execute(
        "ALTER TABLE calendar_feed_tokens ADD CONSTRAINT fk_calendar_feed_tokens_member "
        "FOREIGN KEY (organization_id, user_id) "
        "REFERENCES organization_members (organization_id, user_id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE calendar_feed_tokens DROP CONSTRAINT fk_calendar_feed_tokens_member")
    op.execute(
        "DELETE FROM calendar_feed_tokens t WHERE NOT EXISTS ("
        "SELECT 1 FROM workspace_members m WHERE m.user_id = t.user_id AND m.workspace_id = t.workspace_id)"
    )
    op.execute(
        "ALTER TABLE calendar_feed_tokens ADD CONSTRAINT fk_calendar_feed_tokens_member "
        "FOREIGN KEY (user_id, workspace_id) "
        "REFERENCES workspace_members (user_id, workspace_id) ON DELETE CASCADE"
    )
