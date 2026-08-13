"""arch08_step1_contract_drop_company_logo_url

ARCH-08 Step 1 — CONTRACT. Drops workspaces.company_logo_url column after
adopting any legacy logo strings into uploaded_files.

Revision ID: 2caf2a1cfca1
Revises: b6e1d94f07ca
Create Date: 2026-08-13 18:33:25.578752
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "2caf2a1cfca1"
down_revision: Union[str, None] = "b6e1d94f07ca"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Adopt legacy unadopted company_logo_url into uploaded_files
    bind.execute(
        sa.text("""
        INSERT INTO uploaded_files (
            id,
            file_path,
            original_filename,
            mime_type,
            file_size,
            checksum_sha256,
            owner_id,
            organization_id,
            workspace_id,
            created_at,
            updated_at
        )
        SELECT
            gen_random_uuid(),
            w.company_logo_url,
            'legacy_logo.png',
            'image/png',
            0,
            '0000000000000000000000000000000000000000000000000000000000000000',
            COALESCE(
                (SELECT om.user_id FROM organization_members om WHERE om.organization_id = w.organization_id AND om.role = 'OWNER' LIMIT 1),
                (SELECT om.user_id FROM organization_members om WHERE om.organization_id = w.organization_id LIMIT 1)
            ),
            w.organization_id,
            w.id,
            NOW(),
            NOW()
        FROM workspaces w
        WHERE w.company_logo_url IS NOT NULL AND w.logo_file_id IS NULL;
        """)
    )

    # 2. Link newly adopted uploaded_files to workspaces.logo_file_id
    bind.execute(
        sa.text("""
        UPDATE workspaces w
        SET logo_file_id = uf.id
        FROM uploaded_files uf
        WHERE uf.workspace_id = w.id AND w.logo_file_id IS NULL AND w.company_logo_url IS NOT NULL;
        """)
    )

    # 3. Drop legacy column
    op.drop_column("workspaces", "company_logo_url")


def downgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("company_logo_url", sa.String(length=500), nullable=True),
    )
    op.execute(
        "UPDATE workspaces "
        "SET company_logo_url = '/api/v1/workspaces/' || id::text || '/logo' "
        "WHERE logo_file_id IS NOT NULL"
    )