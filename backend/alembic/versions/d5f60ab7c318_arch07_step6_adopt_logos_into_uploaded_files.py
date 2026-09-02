"""arch07_step6_adopt_logos_into_uploaded_files

Revision ID: d5f60ab7c318
Revises: c93a5f18e7d4
Create Date: 2026-08-13 18:00:00.000000

ARCH-07 Step 6 — adopt logos into uploaded_files; normalise file_path.
"""

from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5f60ab7c318"
down_revision: Union[str, None] = "c93a5f18e7d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_PREFIX = "/uploads/"


def upgrade() -> None:
    bind = op.get_bind()

    op.alter_column(
        "uploaded_files", "owner_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    op.add_column(
        "workspaces",
        sa.Column("logo_file_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True),
    )
    op.create_foreign_key(
        "fk_workspaces_logo_file_id_uploaded_files",
        "workspaces", "uploaded_files",
        ["logo_file_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_workspaces_logo_file_id", "workspaces", ["logo_file_id"],
        unique=False, postgresql_where=sa.text("logo_file_id IS NOT NULL"),
    )

    # 1. Adopt staged logos into uploaded_files
    staged = bind.execute(
        sa.text(
            "SELECT to_regclass('public.arch07_logo_adoption_staging') IS NOT NULL"
        )
    ).scalar()

    if staged:
        bind.execute(
            sa.text(
                """
                INSERT INTO uploaded_files (
                    id, file_path, original_filename, mime_type, file_size, checksum_sha256,
                    owner_id, organization_id, workspace_id,
                    deleted_at, created_at, updated_at
                )
                SELECT gen_random_uuid(), s.storage_key,
                       substring(s.storage_key from '[^/]+$'),
                       s.mime_type, s.size_bytes, s.checksum_sha256,
                       NULL,
                       s.organization_id, s.workspace_id,
                       NULL, now(), now()
                  FROM arch07_logo_adoption_staging s
                 WHERE NOT EXISTS (
                       SELECT 1 FROM uploaded_files u
                        WHERE u.file_path = s.storage_key
                          AND u.deleted_at IS NULL)
                """
            )
        )

        # 2. Link workspaces to adopted uploaded_files
        bind.execute(
            sa.text(
                """
                UPDATE workspaces w
                   SET logo_file_id = u.id
                  FROM uploaded_files u
                 WHERE u.workspace_id = w.id
                   AND u.deleted_at IS NULL
                   AND w.logo_file_id IS NULL
                """
            )
        )

    # 3. Normalise file_path across uploaded_files
    bind.execute(
        sa.text(
            """
            UPDATE uploaded_files
               SET file_path = substring(file_path from :offset)
             WHERE file_path LIKE :pattern
            """
        ),
        {"offset": len(LEGACY_PREFIX) + 1, "pattern": LEGACY_PREFIX + "%"},
    )


def downgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            UPDATE uploaded_files
               SET file_path = :prefix || file_path
             WHERE file_path NOT LIKE :pattern
            """
        ),
        {"prefix": LEGACY_PREFIX, "pattern": LEGACY_PREFIX + "%"},
    )

    op.drop_index("ix_workspaces_logo_file_id", table_name="workspaces")
    op.drop_constraint(
        "fk_workspaces_logo_file_id_uploaded_files", "workspaces",
        type_="foreignkey",
    )
    op.drop_column("workspaces", "logo_file_id")

    op.alter_column(
        "uploaded_files", "owner_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
