"""ARCH-09 Step 8 — webhook API scopes (EXPAND)

Revision ID: arch09_step8_webhook_api_expand
Revises: arch09_step7_breaker_expand
Create Date: 2026-08-17
"""

from __future__ import annotations

from alembic import op

revision = "arch09_step8_webhook_api_expand"
down_revision = "arch09_step7_breaker_expand"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS ck_api_keys_scopes_within_vocabulary")
    op.execute(
        "ALTER TABLE api_keys ADD CONSTRAINT ck_api_keys_scopes_within_vocabulary "
        "CHECK (scopes <@ ARRAY["
        "'organizations:read', 'workspaces:read', 'workspaces:write', 'members:read', "
        "'work_items:read', 'work_items:write', 'audit_logs:read', 'files:read', 'files:write', "
        "'webhooks:read', 'webhooks:write', 'webhooks:admin' "
        "]::text[])"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS ck_api_keys_scopes_within_vocabulary")
    op.execute(
        "ALTER TABLE api_keys ADD CONSTRAINT ck_api_keys_scopes_within_vocabulary "
        "CHECK (scopes <@ ARRAY["
        "'organizations:read', 'workspaces:read', 'workspaces:write', 'members:read', "
        "'work_items:read', 'work_items:write', 'audit_logs:read', 'files:read', 'files:write'"
        "]::text[])"
    )
