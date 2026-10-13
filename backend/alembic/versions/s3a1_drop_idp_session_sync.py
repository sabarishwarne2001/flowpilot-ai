"""Campaign session 3 — drop the sign-in policy's dead `idp_session_sync` flag.

Revision ID: s3a1_drop_idp_session_sync
Revises: s2a1_free_trial_clock
Create Date: 2026-10-12

`tenant_security_policies.idp_session_sync` was stored, shown and editable, and nothing read it:
a signed single-logout request from the identity provider always ends that person's sessions
(F-205). The switch is removed rather than wired, because turning the provider's logout off is
not a choice a security policy should offer (NEEDS-OWNER.md, campaign session 3).

Downgrade restores the column with its old default (false); the value it held is not kept,
since nothing ever read it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "s3a1_drop_idp_session_sync"
down_revision = "s2a1_free_trial_clock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("tenant_security_policies", "idp_session_sync")


def downgrade() -> None:
    op.add_column(
        "tenant_security_policies",
        sa.Column("idp_session_sync", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
