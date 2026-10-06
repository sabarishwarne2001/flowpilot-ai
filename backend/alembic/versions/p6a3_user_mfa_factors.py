"""FINAL RELEASE (N-017) — two-factor sign-in with an authenticator app.

Revision ID: p6a3_user_mfa_factors
Revises: p6a2_review_extraction_reasons
Create Date: 2026-10-06

`user_mfa_factors`: one row per user who started or completed enrolment. The secret is a
MultiFernet ciphertext, recovery codes are keyed hashes, `last_used_step` stops a code being used
twice. The row goes with the user (CASCADE). Additive; downgrade drops the table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p6a3_user_mfa_factors"
down_revision = "p6a2_review_extraction_reasons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_mfa_factors",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("key_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_code_hashes", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("last_used_step", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_mfa_factors"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_mfa_factors_user_id_users",
                                ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("user_mfa_factors")
