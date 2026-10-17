"""A downgrade scheduled for the subscription's renewal (website and payments session, C6).

On Dodo a downgrade takes effect at the next renewal, so the paid period keeps the plan that was
paid for. The subscription carries the change until then: the plan and seats it moves to, when,
and who asked. `plan_change_service.apply_due` makes it at Dodo (change-plan, do_not_bill) just
before the renewal, so the renewal is billed at the new price.

Revision ID: w1a3_scheduled_plan_change
Revises: w1a2_billing_disputes
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "w1a3_scheduled_plan_change"
down_revision: Union[str, None] = "w1a2_billing_disputes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("pending_quota_tier_key", sa.String(32), nullable=True))
    op.add_column("subscriptions", sa.Column("pending_seats", sa.Integer(), nullable=True))
    op.add_column("subscriptions", sa.Column("pending_effective_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subscriptions", sa.Column("pending_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subscriptions", sa.Column(
        "pending_requested_by", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_subscriptions_pending_requested_by_users"),
        nullable=True))
    op.create_check_constraint(
        op.f("ck_subscriptions_pending_change_complete"), "subscriptions",
        "(pending_quota_tier_key IS NULL) = (pending_effective_at IS NULL) "
        "AND (pending_seats IS NULL OR pending_seats >= 1)",
    )
    op.create_index("ix_subscriptions_pending_due", "subscriptions", ["pending_effective_at"],
                    postgresql_where=sa.text("pending_quota_tier_key IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_subscriptions_pending_due", table_name="subscriptions")
    op.drop_constraint(op.f("ck_subscriptions_pending_change_complete"), "subscriptions", type_="check")
    for column in ("pending_requested_by", "pending_requested_at", "pending_effective_at", "pending_seats",
                   "pending_quota_tier_key"):
        op.drop_column("subscriptions", column)
