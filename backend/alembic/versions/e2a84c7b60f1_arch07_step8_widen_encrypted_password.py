"""arch07_step8_widen_encrypted_password

Revision ID: e2a84c7b60f1
Revises: d5f60ab7c318
Create Date: 2026-08-13 21:00:00.000000

ARCH-07 Step 8 (CONTRACT) — widen encrypted_password 255 -> 512.
"""

from __future__ import annotations
from typing import Union, Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e2a84c7b60f1'
down_revision: Union[str, None] = 'd5f60ab7c318'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_WIDTH = 255
NEW_WIDTH = 512


def _guard_no_truncation(bind) -> None:
    at_limit = bind.execute(
        sa.text(
            """
            SELECT count(*) FROM email_settings
             WHERE encrypted_password IS NOT NULL
               AND length(encrypted_password) >= :width
            """
        ),
        {"width": OLD_WIDTH},
    ).scalar_one()
    if at_limit:
        raise RuntimeError(
            f"{at_limit} email_settings rows have ciphertext at or above "
            f"{OLD_WIDTH} characters. These are ALREADY TRUNCATED."
        )


def upgrade() -> None:
    bind = op.get_bind()
    _guard_no_truncation(bind)

    op.alter_column(
        "email_settings", "encrypted_password",
        existing_type=sa.String(length=OLD_WIDTH),
        type_=sa.String(length=NEW_WIDTH),
        existing_nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    over = bind.execute(
        sa.text(
            """
            SELECT count(*) FROM email_settings
             WHERE encrypted_password IS NOT NULL
               AND length(encrypted_password) > :width
            """
        ),
        {"width": OLD_WIDTH},
    ).scalar_one()
    if over:
        raise RuntimeError(
            f"Cannot narrow: {over} rows exceed {OLD_WIDTH} characters."
        )

    op.alter_column(
        "email_settings", "encrypted_password",
        existing_type=sa.String(length=NEW_WIDTH),
        type_=sa.String(length=OLD_WIDTH),
        existing_nullable=True,
    )