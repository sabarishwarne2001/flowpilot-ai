"""expand notifications schema

Revision ID: 188ffda2ce99
Revises: 7f1cb037d639
Create Date: 2026-07-07 14:34:41.340272

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '188ffda2ce99'
down_revision: Union[str, None] = '7f1cb037d639'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    notification_type = sa.Enum(
        "DOCUMENT",
        "AUTOMATION",
        "EMAIL",
        "SYSTEM",
        "SECURITY",
        name="notificationtype",
    )

    notification_priority = sa.Enum(
        "INFO",
        "SUCCESS",
        "WARNING",
        "ERROR",
        name="notificationpriority",
    )

    notification_channel = sa.Enum(
        "IN_APP",
        "EMAIL",
        "SLACK",
        "TEAMS",
        "WEBHOOK",
        name="notificationchannel",
    )

    notification_status = sa.Enum(
        "PENDING",
        "SENT",
        "FAILED",
        name="notificationstatus",
    )

    bind = op.get_bind()

    notification_type.create(bind, checkfirst=True)
    notification_priority.create(bind, checkfirst=True)
    notification_channel.create(bind, checkfirst=True)
    notification_status.create(bind, checkfirst=True)

    op.add_column(
        "notifications",
        sa.Column(
            "notification_type",
            notification_type,
            nullable=False,
        ),
    )

    op.add_column(
        "notifications",
        sa.Column(
            "priority",
            notification_priority,
            nullable=False,
        ),
    )

    op.add_column(
        "notifications",
        sa.Column(
            "delivery_channel",
            notification_channel,
            nullable=False,
        ),
    )

    op.add_column(
        "notifications",
        sa.Column(
            "delivery_status",
            notification_status,
            nullable=False,
        ),
    )

    op.add_column(
        "notifications",
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    op.add_column(
        "notifications",
        sa.Column(
            "failure_reason",
            sa.Text(),
            nullable=True,
        ),
    )

    op.alter_column(
        "notifications",
        "retry_count",
        server_default=None,
    )

    op.create_index(
        op.f("ix_notifications_delivery_channel"),
        "notifications",
        ["delivery_channel"],
    )

    op.create_index(
        op.f("ix_notifications_delivery_status"),
        "notifications",
        ["delivery_status"],
    )

    op.create_index(
        op.f("ix_notifications_notification_type"),
        "notifications",
        ["notification_type"],
    )

    op.create_index(
        op.f("ix_notifications_priority"),
        "notifications",
        ["priority"],
    )


def downgrade() -> None:

    op.drop_index(op.f("ix_notifications_priority"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_notification_type"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_delivery_status"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_delivery_channel"), table_name="notifications")

    op.drop_column("notifications", "failure_reason")
    op.drop_column("notifications", "retry_count")
    op.drop_column("notifications", "delivery_status")
    op.drop_column("notifications", "delivery_channel")
    op.drop_column("notifications", "priority")
    op.drop_column("notifications", "notification_type")

    bind = op.get_bind()

    sa.Enum(name="notificationstatus").drop(bind, checkfirst=True)
    sa.Enum(name="notificationchannel").drop(bind, checkfirst=True)
    sa.Enum(name="notificationpriority").drop(bind, checkfirst=True)
    sa.Enum(name="notificationtype").drop(bind, checkfirst=True)