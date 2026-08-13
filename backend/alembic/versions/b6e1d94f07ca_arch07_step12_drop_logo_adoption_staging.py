"""ARCH-07 Step 12 — drop the logo adoption staging table.

Revision ID: b6e1d94f07ca
Revises: a8b3f5c02d47
Create Date: 2026-08-13 23:00:00.000000

arch07_logo_adoption_staging was created by
scripts/verify_arch07_step6_preflight.py and consumed by migration
d5f60ab7c318.
"""

from __future__ import annotations
from typing import Union,Sequence

from pathlib import Path
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6e1d94f07ca"
down_revision: Union[str, None] = "a8b3f5c02d47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STAGING_TABLE = "arch07_logo_adoption_staging"
LOGO_PURPOSE = "WORKSPACE_LOGO"
LEGACY_PREFIX = "/uploads/"


def _table_exists(bind) -> bool:
    return bool(
        bind.execute(
            sa.text(f"SELECT to_regclass('public.{STAGING_TABLE}') IS NOT NULL")
        ).scalar()
    )


def _guard_adoption_is_complete(bind) -> None:
    unadopted = bind.execute(
        sa.text(
            f"""
            SELECT count(*) FROM {STAGING_TABLE} s
             WHERE NOT EXISTS (
                   SELECT 1 FROM uploaded_files u
                    WHERE u.file_path = s.storage_key
                      AND u.deleted_at IS NULL)
            """
        )
    ).scalar_one()
    if unadopted:
        raise RuntimeError(
            f"{unadopted} staged logos have no live uploaded_files row. "
            f"Step 6 adoption is incomplete — dropping the staging table now "
            f"would make that unrecoverable. Investigate before proceeding."
        )

    residual = bind.execute(
        sa.text(
            "SELECT count(*) FROM uploaded_files WHERE file_path LIKE :pattern"
        ),
        {"pattern": LEGACY_PREFIX + "%"},
    ).scalar_one()
    if residual:
        raise RuntimeError(
            f"{residual} uploaded_files rows still hold public-URL file_path "
            f"values. Step 6 normalisation did not complete."
        )


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind):
        print(f"[arch07-step12] {STAGING_TABLE} is already absent; nothing to do.")
        return

    # Check if evidence JSON archive file exists on disk
    evidence_dir = Path("arch07_evidence")
    has_archive_file = evidence_dir.exists() and len(list(evidence_dir.glob("logo-adoption-staging-*.json"))) > 0

    try:
        x_args = op.get_x_argument(as_dictionary=True)
    except Exception:
        x_args = {}

    has_flag = x_args.get("archived") == "1" or "archived=1" in x_args or "archived" in x_args

    if not has_flag and not has_archive_file:
        raise RuntimeError(
            f"Refusing to drop {STAGING_TABLE} without archiving first.\n\n"
            f"Run:\n"
            f"    python scripts/archive_logo_adoption_staging.py\n"
            f"then re-run:\n"
            f"    alembic upgrade head"
        )

    # Reconcile any workspace holding an active uploaded_files logo row
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

    _guard_adoption_is_complete(bind)

    staged = bind.execute(
        sa.text(f"SELECT count(*) FROM {STAGING_TABLE}")
    ).scalar_one()

    op.drop_table(STAGING_TABLE)
    print(
        f"[arch07-step12] dropped {STAGING_TABLE} ({staged} rows). "
        f"Content preserved in arch07_evidence/."
    )


def downgrade() -> None:
    op.create_table(
        STAGING_TABLE,
        sa.Column("storage_key", sa.Text(), primary_key=True, nullable=False),
        sa.Column("workspace_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=False),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=False),
        sa.Column("legacy_url", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    print(
        f"[arch07-step12] recreated {STAGING_TABLE} EMPTY. Reload from "
        f"arch07_evidence/logo-adoption-staging-*.json if required."
    )