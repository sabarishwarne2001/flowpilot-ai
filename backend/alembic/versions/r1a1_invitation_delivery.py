"""F-226 — remember whether an invitation's link left through a mail server FlowPilot does not run.

Revision ID: r1a1_invitation_delivery
Revises: q1a1_retired_groq_models
Create Date: 2026-10-10

An invitation's token is proof that its holder reads the invited mailbox only when the platform
relay delivered it. An organization with its own SMTP relays its invitations through that server,
so whoever runs it can read every accept link. `delivered_off_platform` is set (and committed)
before such a send; a token marked so does not verify an address, does not open the one-step
invitation sign-up, and the preview does not say whether the address has an account.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r1a1_invitation_delivery"
down_revision = "q1a1_retired_groq_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organization_invitations",
        sa.Column("delivered_off_platform", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("organization_invitations", "delivered_off_platform")
