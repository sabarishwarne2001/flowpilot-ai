"""arch06_step8_organization_email_settings

Revision ID: f7302be6a48d
Revises: d4a91e7b302c
Create Date: 2026-08-19 09:00:00.000000

ARCH-06 Step 8 — organization_email_settings. §B.5 Option B.

A PLAIN EXPAND. The table is new and starts empty, nothing references it
yet, and `email_settings` is not touched by a single statement in this
revision — that last point is the whole content of Option B, so it is worth
being explicit that this file contains no ALTER against that table.

THE ENUM IS REUSED, NOT CREATED
-----------------------------------
`email_encryption` already exists in the database. It was created as
`emailencryption` by 08cbbe53034b and renamed by 74a07cbe5d7e (ARCH-01's enum
alignment). This revision binds to it with `create_type=False` and issues NO
`CREATE TYPE`.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f7302be6a48d"
down_revision: Union[str, None] = "d4a91e7b302c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EMAIL_ENCRYPTION = postgresql.ENUM(
    "NONE",
    "TLS",
    "SSL",
    name="email_encryption",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "organization_email_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("smtp_host", sa.String(length=255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=True),
        sa.Column("smtp_username", sa.String(length=255), nullable=True),
        sa.Column("encrypted_password", sa.String(length=512), nullable=True),
        sa.Column("sender_name", sa.String(length=255), nullable=True),
        sa.Column("sender_email", sa.String(length=255), nullable=True),
        sa.Column("encryption", EMAIL_ENCRYPTION, nullable=True),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_organization_email_settings"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_email_settings_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name="fk_organization_email_settings_updated_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "organization_id",
            name="uq_organization_email_settings_organization_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("organization_email_settings")
