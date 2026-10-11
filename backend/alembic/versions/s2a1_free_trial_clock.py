"""Campaign session 2 — the one-week free trial (owner decision N-052).

Revision ID: s2a1_free_trial_clock
Revises: s1b1_calendar_feed_org_member
Create Date: 2026-10-11

Free is a trial of `FREE_TRIAL_DAYS` (7) per owner account. The account's clock starts when
the person creates their first organization (`users.trial_ends_at`); every organization they
create copies it (`organizations.trial_ends_at`), so a second organization or an ownership
transfer never restarts it. After the end, an organization still on Free is read-only until it
subscribes (`trial_service`).

Backfill (N-052, decided in campaign session 2): organizations on Free when this ships get
their seven days from today; any other organization gets `created_at + 7 days` (it only
matters if its paid plan later lapses to Free, when it is asked to subscribe). An account that
owns organizations gets the earliest end among them. The two `*_sent_at` columns record the
trial emails so each is sent once.

Downgrade drops the four columns; nothing else reads them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "s2a1_free_trial_clock"
down_revision = "s1b1_calendar_feed_org_member"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("trial_reminder_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("trial_ended_notice_sent_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        """
        UPDATE organizations o
           SET trial_ends_at = CASE
                 WHEN t.key = 'free' THEN now() + interval '7 days'
                 ELSE o.created_at + interval '7 days'
               END
          FROM organizations o2
          LEFT JOIN quota_tiers t ON t.id = o2.quota_tier_id
         WHERE o2.id = o.id
        """
    )
    op.execute(
        """
        UPDATE users u
           SET trial_ends_at = s.ends_at
          FROM (
                SELECT m.user_id, min(o.trial_ends_at) AS ends_at
                  FROM organization_members m
                  JOIN organizations o ON o.id = m.organization_id
                 WHERE m.role = 'OWNER' AND m.status = 'ACTIVE'
                 GROUP BY m.user_id
               ) s
         WHERE s.user_id = u.id
        """
    )


def downgrade() -> None:
    op.drop_column("users", "trial_ended_notice_sent_at")
    op.drop_column("users", "trial_reminder_sent_at")
    op.drop_column("users", "trial_ends_at")
    op.drop_column("organizations", "trial_ends_at")
