"""ARCH-09 repair — consolidate the api_keys scope vocabulary CHECK

Revision ID: arch09_scope_ck_repair
Revises: arch09_step10_jobs_expand
Create Date: 2026-08-18
"""

from __future__ import annotations

from alembic import op

revision = "arch09_scope_ck_repair"
down_revision = "arch09_step10_jobs_expand"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "ck_api_keys_scopes_allowed"

SCOPES_12: tuple[str, ...] = (
    "organizations:read",
    "workspaces:read",
    "workspaces:write",
    "members:read",
    "work_items:read",
    "work_items:write",
    "audit_logs:read",
    "files:read",
    "files:write",
    "webhooks:read",
    "webhooks:write",
    "webhooks:admin",
)

SCOPES_9: tuple[str, ...] = SCOPES_12[:9]


def _array_sql(scopes: tuple[str, ...]) -> str:
    return "ARRAY[" + ", ".join(f"'{s}'" for s in scopes) + "]::text[]"


def upgrade() -> None:
    # Drop all historical constraint name variants
    op.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS ck_api_keys_ck_api_keys_scopes_allowed")
    op.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS ck_api_keys_scopes_within_vocabulary")
    op.execute(f"ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}")
    
    # Create the single unified 12-scope constraint
    op.execute(
        f"ALTER TABLE api_keys ADD CONSTRAINT {CONSTRAINT_NAME} "
        f"CHECK (scopes <@ {_array_sql(SCOPES_12)})"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS ck_api_keys_ck_api_keys_scopes_allowed")
    op.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS ck_api_keys_scopes_within_vocabulary")
    op.execute(f"ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}")
    op.execute(
        f"ALTER TABLE api_keys ADD CONSTRAINT {CONSTRAINT_NAME} "
        f"CHECK (scopes <@ {_array_sql(SCOPES_9)})"
    )
