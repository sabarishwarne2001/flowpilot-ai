"""Campaign session 3 — the audit log can record the enterprise identity objects (F-294).

Revision ID: s3a4_identity_audit_vocabulary
Revises: s3a3_notification_preferences
Create Date: 2026-10-12

The identity services audited the sign-in policy, identity providers, signing certificates, SCIM
keys and groups, verified domains and directory identities under resource types that
`audit_resource_type` did not have, so every one of those writes failed (and was dropped by the
identity helper). This adds the seven values.

`ALTER TYPE ... ADD VALUE` runs outside the migration's transaction (`autocommit_block`), as in
arch12_step6a_audit_enum. `downgrade()` is a documented no-op: PostgreSQL cannot drop an enum
value, and rebuilding the type would rewrite `audit_logs`, which its trigger makes immutable.
`IF NOT EXISTS` keeps upgrade → downgrade → upgrade working.
"""

from __future__ import annotations

from alembic import op

revision = "s3a4_identity_audit_vocabulary"
down_revision = "s3a3_notification_preferences"
branch_labels = None
depends_on = None

NEW_RESOURCE_TYPES: tuple[str, ...] = (
    "TENANT_SECURITY_POLICY",
    "ENTERPRISE_IDP_CONFIG",
    "IDP_CERTIFICATE",
    "SCIM_API_KEY",
    "SCIM_GROUP",
    "VERIFIED_DOMAIN",
    "DIRECTORY_IDENTITY",
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in NEW_RESOURCE_TYPES:
            op.execute(f"ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Intentionally a no-op. See the module docstring.
    pass
