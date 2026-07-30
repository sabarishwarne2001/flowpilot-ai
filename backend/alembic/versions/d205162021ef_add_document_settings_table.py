"""add_document_settings_table

Revision ID: d205162021ef
Revises: 8c5d9ab2c4b1
Create Date: 2026-07-28 12:34:49.133765

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd205162021ef'
down_revision: Union[str, None] = '8c5d9ab2c4b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_settings",

        sa.Column("id", sa.UUID(), nullable=False),

        sa.Column("chunk_size", sa.Integer(), nullable=False),

        sa.Column("chunk_overlap", sa.Integer(), nullable=False),

        sa.Column("embedding_model", sa.String(length=100), nullable=False),

        sa.Column("ocr_language", sa.String(length=20), nullable=False),

        sa.Column("max_upload_size", sa.Integer(), nullable=False),

        sa.Column("allowed_file_types", sa.String(length=255), nullable=False),

        sa.Column("duplicate_detection", sa.Boolean(), nullable=False),

        sa.Column("automatic_classification", sa.Boolean(), nullable=False),

        sa.Column("automatic_summarization", sa.Boolean(), nullable=False),

        sa.Column("automatic_entity_extraction", sa.Boolean(), nullable=False),

        sa.Column("user_id", sa.UUID(), nullable=False),

        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_document_settings_user_id"),
        "document_settings",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_document_settings_user_id"),
        table_name="document_settings",
    )

    op.drop_table("document_settings")