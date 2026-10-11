"""Campaign session 1 — remember each upload part's size, so a session is bounded in total.

Revision ID: s1a1_upload_session_part_sizes
Revises: r1a1_invitation_delivery
Create Date: 2026-10-11

A resumable upload's parts were bounded one at a time (64 MB) but never in total: up to 10,000
parts reached storage before the assembled file was checked, past any plan's file size. The
storage drivers cannot list a multipart upload's parts, so the session records the size of each
part it holds (a re-sent part replaces its earlier size); a part that would take the total past
the declared size or the plan's file size is refused before it is stored.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "s1a1_upload_session_part_sizes"
down_revision = "r1a1_invitation_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "upload_sessions",
        sa.Column(
            "part_sizes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("upload_sessions", "part_sizes")
