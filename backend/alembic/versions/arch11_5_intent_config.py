"""ARCH-11.5 Step 4 — per-workspace intent configuration (EXPAND)

Revision ID: arch11_5_intent_config
Revises: arch11_step3_chunk_tokens
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch11_5_intent_config"
down_revision = "arch11_step3_chunk_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_settings",
        sa.Column(
            "intent_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "ARCH-11.5. {intent_name: [keyword, ...]}. NULL follows platform defaults."
            ),
        ),
    )
    op.create_check_constraint(
        "ck_document_settings_intent_config_object",
        "document_settings",
        "intent_config IS NULL OR jsonb_typeof(intent_config) = 'object'",
    )

    op.execute(
        """
        UPDATE document_settings
        SET intent_config = intent_config - 'upsc'
        WHERE intent_config ? 'upsc'
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_document_settings_intent_config_object",
        "document_settings",
        type_="check",
    )
    op.drop_column("document_settings", "intent_config")