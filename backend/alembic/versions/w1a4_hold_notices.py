"""Which notice a Dodo hold has had (website and payments session, C1).

A declined Dodo renewal puts the subscription on hold with a grace window. The
`billing.hold_notices` job reminds the owner a week before the window closes, warns them two days
before, and tells them when the organization turns read-only. These two columns say which of
those this hold has had, and which hold it was (its `grace_ends_at`), so each is sent once per hold
and a later hold starts again.

Revision ID: w1a4_hold_notices
Revises: w1a3_scheduled_plan_change
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w1a4_hold_notices"
down_revision: Union[str, None] = "w1a3_scheduled_plan_change"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("hold_notice_step", sa.String(16), nullable=True))
    op.add_column("subscriptions", sa.Column("hold_notice_grace_ends_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("subscriptions", "hold_notice_grace_ends_at")
    op.drop_column("subscriptions", "hold_notice_step")
