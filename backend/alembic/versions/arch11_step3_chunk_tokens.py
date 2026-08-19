"""ARCH-11 Step 3 — token-based chunk settings on document_settings (EXPAND)

Revision ID: arch11_step3_chunk_tokens
Revises: arch11_step2_chunks_expand
Create Date: 2026-08-19

Additive. `chunk_size` and `chunk_overlap` are left in place and still read by
nothing after Step 3 ships — they are dropped in the Step 9 CONTRACT alongside
the `CHROMA_*` settings, because a column the application no longer reads is
safe to carry for one release and unsafe to drop in the same deploy that stops
reading it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "arch11_step3_chunk_tokens"
down_revision = "arch11_step2_chunks_expand"
branch_labels = None
depends_on = None

DEFAULT_CHUNK_SIZE_TOKENS = 220
DEFAULT_CHUNK_OVERLAP_PCT = 10
CANONICAL_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

MAX_CHUNK_SIZE_TOKENS = 254
MIN_CHUNK_SIZE_TOKENS = 32


def upgrade() -> None:
    op.add_column(
        "document_settings",
        sa.Column(
            "chunk_size_tokens",
            sa.Integer(),
            nullable=False,
            server_default=str(DEFAULT_CHUNK_SIZE_TOKENS),
        ),
    )
    op.add_column(
        "document_settings",
        sa.Column(
            "chunk_overlap_pct",
            sa.Integer(),
            nullable=False,
            server_default=str(DEFAULT_CHUNK_OVERLAP_PCT),
        ),
    )

    # Preserve deliberate tuning; snap the default population onto the default.
    op.execute(
        f"""
        UPDATE document_settings
        SET chunk_size_tokens = LEAST(
                {MAX_CHUNK_SIZE_TOKENS},
                GREATEST({MIN_CHUNK_SIZE_TOKENS}, ROUND(chunk_size / 4.0))
            ),
            chunk_overlap_pct = LEAST(
                40,
                GREATEST(0, ROUND(100.0 * chunk_overlap / NULLIF(chunk_size, 0)))
            )
        WHERE NOT (chunk_size = 500 AND chunk_overlap = 100)
        """
    )

    op.create_check_constraint(
        "ck_document_settings_chunk_size_tokens_range",
        "document_settings",
        f"chunk_size_tokens BETWEEN {MIN_CHUNK_SIZE_TOKENS} AND {MAX_CHUNK_SIZE_TOKENS}",
    )
    op.create_check_constraint(
        "ck_document_settings_chunk_overlap_pct_range",
        "document_settings",
        "chunk_overlap_pct BETWEEN 0 AND 40",
    )

    # §0.4: the workspace-level model setting stops being able to lie.
    op.execute(
        sa.text(
            "UPDATE document_settings SET embedding_model = :model "
            "WHERE embedding_model IS DISTINCT FROM :model"
        ).bindparams(model=CANONICAL_EMBEDDING_MODEL)
    )
    op.create_check_constraint(
        "ck_document_settings_embedding_model_pinned",
        "document_settings",
        f"embedding_model = '{CANONICAL_EMBEDDING_MODEL}'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_document_settings_embedding_model_pinned",
        "document_settings",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_settings_chunk_overlap_pct_range",
        "document_settings",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_settings_chunk_size_tokens_range",
        "document_settings",
        type_="check",
    )
    op.drop_column("document_settings", "chunk_overlap_pct")
    op.drop_column("document_settings", "chunk_size_tokens")