"""arch04_step4_migrate_invitation_backfill

Revision ID: f3c137bf53d5
Revises: cb521957f5f0
Create Date: 2026-08-10 12:19:10.996294

ARCH-04 Step 4 (MIGRATE) - backfill organization_invitations and
invitation_workspace_grants from workspace_invitations

Data only. No DDL. workspace_invitations is read from and never written to —
it remains fully functional for the live invitation service until Step 5.

Every source row becomes one organization_invitations row carrying the same
primary key (§M4.2), plus exactly one invitation_workspace_grants row built
from its single (workspace_id, role) pair (§M4.7).

Every backfilled invitation gets organization_role = MEMBER (§M4.1). Mapping
workspace ADMIN to organization ADMIN would be a privilege escalation
performed by a migration: organization ADMIN manages members and every
workspace in the tenant, which is not what any of these invitations conveyed.

Idempotent: primary keys carry across, so ON CONFLICT (id) DO NOTHING makes a
re-run a no-op.
"""

from __future__ import annotations
from typing import Sequence, Union

import logging

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f3c137bf53d5'
down_revision: Union[str, None] = 'cb521957f5f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.arch04.step4")


# ============================================================================
# Backfill queries
# ============================================================================

INSERT_INVITATIONS = sa.text("""
    INSERT INTO organization_invitations (
        id,
        organization_id,
        inviter_id,
        invited_user_id,
        email,
        organization_role,
        status,
        token_hash,
        expires_at,
        accepted_at,
        rejected_at,
        revoked_at,
        revoked_by_id,
        last_sent_at,
        send_count,
        created_at,
        updated_at
    )
    SELECT
        wi.id,
        wi.organization_id,
        wi.inviter_id,
        u.id,                          -- §M4.5, NULL when no account matches
        lower(wi.email),               -- §M4.4
        'MEMBER'::organization_role,   -- §M4.1, unconditionally
        wi.status,
        wi.token_hash,                 -- §M4.3, verbatim
        wi.expires_at,
        wi.accepted_at,
        wi.rejected_at,
        wi.revoked_at,
        NULL,                          -- §M4.6, revoker was never recorded
        wi.created_at,                 -- §M4.8, last_sent_at
        1,                             -- §M4.8, send_count
        wi.created_at,
        wi.updated_at
    FROM workspace_invitations wi
    LEFT JOIN users u ON lower(u.email) = lower(wi.email)
    ON CONFLICT (id) DO NOTHING
""")

INSERT_GRANTS = sa.text("""
    INSERT INTO invitation_workspace_grants (
        id, invitation_id, workspace_id, role, created_at, updated_at
    )
    SELECT
        gen_random_uuid(),
        wi.id,
        wi.workspace_id,
        wi.role,                       -- §M4.7, the WorkspaceRole lands here
        wi.created_at,
        wi.updated_at
    FROM workspace_invitations wi
    WHERE NOT EXISTS (
        SELECT 1 FROM invitation_workspace_grants g
        WHERE g.invitation_id = wi.id
    )
""")


# ============================================================================
# Pre-commit assertions
# ============================================================================

def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"ARCH-04 Step 4 MIGRATE aborted: {message}")


def upgrade() -> None:
    conn = op.get_bind()

    # --- Guard the preconditions before writing anything ------------------
    source_total = conn.execute(
        sa.text("SELECT count(*) FROM workspace_invitations")
    ).scalar_one()

    null_org = conn.execute(sa.text(
        "SELECT count(*) FROM workspace_invitations WHERE organization_id IS NULL"
    )).scalar_one()
    _assert(
        null_org == 0,
        f"{null_org} source row(s) have a NULL organization_id, which is NOT "
        f"NULL on the target. Populate them from workspaces.organization_id "
        f"and re-run. This migration will not invent a tenant."
    )

    null_hash = conn.execute(sa.text(
        "SELECT count(*) FROM workspace_invitations WHERE token_hash IS NULL"
    )).scalar_one()
    _assert(null_hash == 0, f"{null_hash} source row(s) have a NULL token_hash.")

    orphan_ws = conn.execute(sa.text("""
        SELECT count(*) FROM workspace_invitations wi
        LEFT JOIN workspaces w ON w.id = wi.workspace_id
        WHERE w.id IS NULL
    """)).scalar_one()
    _assert(
        orphan_ws == 0,
        f"{orphan_ws} source row(s) reference a workspace that no longer "
        f"exists; their grant rows would violate the FK."
    )

    logger.info(
        "ARCH-04 Step 4: %s source invitation(s) to migrate.", source_total
    )

    # --- Backfill ---------------------------------------------------------
    conn.execute(INSERT_INVITATIONS)
    conn.execute(INSERT_GRANTS)

    # --- Per-row log
    rows = conn.execute(sa.text("""
        SELECT
            oi.id,
            oi.email,
            oi.status,
            oi.organization_role,
            g.workspace_id,
            g.role AS workspace_role,
            oi.invited_user_id IS NOT NULL AS user_linked
        FROM organization_invitations oi
        JOIN invitation_workspace_grants g ON g.invitation_id = oi.id
        ORDER BY oi.created_at
    """)).mappings().all()

    for row in rows:
        logger.info(
            "MIGRATED | invitation=%s | email=%s | status=%s | org_role=%s | "
            "workspace=%s | ws_role=%s | user_linked=%s",
            row["id"], row["email"], row["status"], row["organization_role"],
            row["workspace_id"], row["workspace_role"], row["user_linked"],
        )

    # --- Assertions -------------------------------------------------------
    target_total = conn.execute(
        sa.text("SELECT count(*) FROM organization_invitations")
    ).scalar_one()
    _assert(
        target_total == source_total,
        f"row count mismatch: {source_total} source, {target_total} target."
    )

    grant_total = conn.execute(
        sa.text("SELECT count(*) FROM invitation_workspace_grants")
    ).scalar_one()
    _assert(
        grant_total == source_total,
        f"expected exactly one grant per invitation: {source_total} source, "
        f"{grant_total} grants."
    )

    mismatched_status = conn.execute(sa.text("""
        SELECT count(*) FROM workspace_invitations wi
        JOIN organization_invitations oi ON oi.id = wi.id
        WHERE oi.status IS DISTINCT FROM wi.status
    """)).scalar_one()
    _assert(
        mismatched_status == 0,
        f"{mismatched_status} row(s) changed status during migration."
    )

    non_member = conn.execute(sa.text("""
        SELECT count(*) FROM organization_invitations oi
        JOIN workspace_invitations wi ON wi.id = oi.id
        WHERE oi.organization_role <> 'MEMBER'
    """)).scalar_one()
    _assert(
        non_member == 0,
        f"{non_member} backfilled row(s) carry an organization_role other "
        f"than MEMBER."
    )

    mismatched_hash = conn.execute(sa.text("""
        SELECT count(*) FROM workspace_invitations wi
        JOIN organization_invitations oi ON oi.id = wi.id
        WHERE oi.token_hash IS DISTINCT FROM wi.token_hash
    """)).scalar_one()
    _assert(mismatched_hash == 0, f"{mismatched_hash} token_hash mismatch(es).")

    dup_hash = conn.execute(sa.text("""
        SELECT count(*) FROM (
            SELECT token_hash FROM organization_invitations
            GROUP BY token_hash HAVING count(*) > 1
        ) d
    """)).scalar_one()
    _assert(dup_hash == 0, f"{dup_hash} duplicate token_hash value(s) on target.")

    mismatched_grant = conn.execute(sa.text("""
        SELECT count(*) FROM workspace_invitations wi
        JOIN invitation_workspace_grants g ON g.invitation_id = wi.id
        WHERE g.workspace_id IS DISTINCT FROM wi.workspace_id
           OR g.role IS DISTINCT FROM wi.role
    """)).scalar_one()
    _assert(
        mismatched_grant == 0,
        f"{mismatched_grant} grant row(s) do not match their source."
    )

    summary = conn.execute(sa.text("""
        SELECT status, count(*) AS n
        FROM organization_invitations
        GROUP BY status ORDER BY status
    """)).mappings().all()
    logger.info(
        "ARCH-04 Step 4 complete: %s invitation(s), %s grant(s). Status: %s",
        target_total,
        grant_total,
        ", ".join(f"{r['status']}={r['n']}" for r in summary),
    )


def downgrade() -> None:
    conn = op.get_bind()

    grants_deleted = conn.execute(sa.text("""
        DELETE FROM invitation_workspace_grants g
        USING workspace_invitations wi
        WHERE g.invitation_id = wi.id
    """)).rowcount

    invitations_deleted = conn.execute(sa.text("""
        DELETE FROM organization_invitations oi
        USING workspace_invitations wi
        WHERE oi.id = wi.id
    """)).rowcount

    logger.info(
        "ARCH-04 Step 4 downgrade: removed %s grant(s), %s invitation(s).",
        grants_deleted, invitations_deleted,
    )