"""Campaign session 3 — an organization can require two-factor sign-in.

Revision ID: s3a2_require_two_factor
Revises: s3a1_drop_idp_session_sync
Create Date: 2026-10-12

`tenant_security_policies.require_mfa`: when true, a person signed in with a password and no
second factor is refused everywhere in the organization until they turn it on (N-017, decided
again in campaign session 3). Off for every existing organization.

Downgrade drops the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "s3a2_require_two_factor"
down_revision = "s3a1_drop_idp_session_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_security_policies",
        sa.Column("require_mfa", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("tenant_security_policies", "require_mfa")
