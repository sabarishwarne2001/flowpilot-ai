"""ARCH-40 Step 2 — BACKFILL: workspace SMTP into the override, dead AI values archived.

Revision ID: arch40_step2_settings_backfill
Revises: arch40_step1_settings_review

ARCH40-S1:backfill. The release boundary between expand and contract.

This is the middle of the expand/backfill/contract sequence, and the release
boundary. After this migration the application reads neither
`email_settings` nor the three dead `ai_settings` columns. Step 3 drops the
columns; it is deliberately not reached by `alembic upgrade head` without an
explicit authorisation, so the two never run in the same deploy.

ONE OWNER, NOT TWO KEPT IN SYNC
===============================

`email_settings` (workspace, ARCH-02) and `workspace_email_overrides` (ARCH-40)
hold the same thing. Keeping both current would need a trigger or a
disciplined second writer, and the first time one of them lost a write the
answer to "why did this come from the wrong address" would be "because two
tables disagreed".

So: the override owns workspace-level email from here on. Every
`email_settings` row is copied in, verbatim, and archived through the
`settings_migration_archive` pattern so the copy is reversible. The old table
keeps its rows and loses its readers — `verify_arch40.py` gate A5 proves the
runtime has none.

`is_enabled` carries over from `email_settings.is_enabled`, not from a
literal. A workspace that had deliberately disabled its relay must not have it
switched back on by a migration.

WHY from_address IS DERIVED FROM smtp_username
==============================================

`SMTPConfig.sender_address` already falls back to `smtp_username` when
`from_email` is unset, so copying the username in reproduces exactly what the
workspace was sending as. Writing NULL instead would look identical today and
diverge the moment the resolver stopped applying that fallback.

Rows whose username is not an address are left with a NULL `from_address`:
`ck_workspace_email_overrides_from_address_shape` would refuse them, and a
migration that fails on a tenant's legacy data is worse than one that leaves
the pre-ARCH-40 fallback in charge for that row.
"""

from __future__ import annotations

from alembic import op

revision = "arch40_step2_settings_backfill"
down_revision = "arch40_step1_settings_review"
branch_labels = None
depends_on = None

#: The shape `ck_workspace_email_overrides_from_address_shape` will accept.
_ADDRESS_SQL_REGEX = "^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$"

#: Stands in for "no identifiable user" in the archive, whose source_user_id is
#: NOT NULL and deliberately carries no foreign key.
_NIL_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Archive every workspace email_settings row before copying it.
    # ------------------------------------------------------------------
    op.execute(
        f"""
        INSERT INTO settings_migration_archive (
            id, settings_kind, source_row_id, source_user_id, source_user_email,
            workspace_id, winning_row_id, payload, migration_revision, archived_at
        )
        SELECT
            gen_random_uuid(),
            'EMAIL',
            es.id,
            coalesce(es.updated_by_user_id, '{_NIL_UUID}'::uuid),
            coalesce(u.email, 'unknown@migration.invalid'),
            es.workspace_id,
            es.id,
            jsonb_build_object(
                'source_table',        'email_settings',
                'smtp_host',           es.smtp_host,
                'smtp_port',           es.smtp_port,
                'smtp_username',       es.smtp_username,
                'encrypted_password',  es.encrypted_password,
                'sender_name',         es.sender_name,
                'encryption',          es.encryption::text,
                'is_enabled',          es.is_enabled
            ),
            '{revision}',
            now()
        FROM email_settings es
        LEFT JOIN users u ON u.id = es.updated_by_user_id
        WHERE NOT EXISTS (
            SELECT 1 FROM workspace_email_overrides o
            WHERE o.workspace_id = es.workspace_id
        )
        """
    )

    # ------------------------------------------------------------------
    # 2. Copy them into the override, which now owns workspace email.
    # ------------------------------------------------------------------
    op.execute(
        f"""
        INSERT INTO workspace_email_overrides (
            workspace_id, organization_id, is_enabled, smtp_host, smtp_port,
            smtp_username, smtp_password_encrypted, encryption, sender_name,
            from_address, reply_to_address, updated_by_user_id,
            created_at, updated_at
        )
        SELECT
            es.workspace_id,
            w.organization_id,
            es.is_enabled,
            es.smtp_host,
            es.smtp_port,
            es.smtp_username,
            es.encrypted_password,
            es.encryption::text,
            es.sender_name,
            CASE
                WHEN es.smtp_username ~ '{_ADDRESS_SQL_REGEX}' THEN es.smtp_username
                ELSE NULL
            END,
            NULL,
            es.updated_by_user_id,
            es.created_at,
            now()
        FROM email_settings es
        JOIN workspaces w ON w.id = es.workspace_id
        ON CONFLICT (workspace_id) DO NOTHING
        """
    )

    # ------------------------------------------------------------------
    # 3. Archive the three dead ai_settings values, then null them.
    #
    # Nulling is what makes step 3 provably safe: a column that is NULL on
    # every row cannot be feeding a reader that nobody found in the grep.
    # ------------------------------------------------------------------
    op.execute(
        f"""
        INSERT INTO settings_migration_archive (
            id, settings_kind, source_row_id, source_user_id, source_user_email,
            workspace_id, winning_row_id, payload, migration_revision, archived_at
        )
        SELECT
            gen_random_uuid(),
            'AI',
            a.id,
            coalesce(a.updated_by_user_id, '{_NIL_UUID}'::uuid),
            coalesce(u.email, 'unknown@migration.invalid'),
            a.workspace_id,
            a.id,
            jsonb_build_object(
                'source_table',          'ai_settings',
                'dropped_in',            'arch40_step3_contract_ai_settings',
                'system_prompt_version', a.system_prompt_version,
                'prompt_version',        a.prompt_version,
                'enable_token_tracking', a.enable_token_tracking
            ),
            '{revision}',
            now()
        FROM ai_settings a
        LEFT JOIN users u ON u.id = a.updated_by_user_id
        WHERE a.system_prompt_version IS NOT NULL
           OR a.prompt_version IS NOT NULL
           OR a.enable_token_tracking IS NOT NULL
        """
    )
    op.execute(
        "UPDATE ai_settings SET system_prompt_version = NULL, "
        "prompt_version = NULL, enable_token_tracking = NULL"
    )


def downgrade() -> None:
    # Restore the dead values from the archive, then discard the copies this
    # migration made. The archive rows themselves stay: they are the evidence
    # that the copy happened, and ARCH-02 chose this table precisely so that
    # a settings row is never unrecoverable.
    op.execute(
        f"""
        UPDATE ai_settings a
        SET system_prompt_version = coalesce(s.payload ->> 'system_prompt_version', 'v1'),
            prompt_version        = coalesce(s.payload ->> 'prompt_version', 'v1'),
            enable_token_tracking = coalesce((s.payload ->> 'enable_token_tracking')::boolean, true)
        FROM settings_migration_archive s
        WHERE s.source_row_id = a.id
          AND s.settings_kind = 'AI'
          AND s.migration_revision = '{revision}'
        """
    )
    op.execute(
        "UPDATE ai_settings SET system_prompt_version = coalesce(system_prompt_version, 'v1'), "
        "prompt_version = coalesce(prompt_version, 'v1'), "
        "enable_token_tracking = coalesce(enable_token_tracking, true)"
    )
    op.execute(
        f"""
        DELETE FROM workspace_email_overrides o
        USING settings_migration_archive s
        WHERE s.settings_kind = 'EMAIL'
          AND s.migration_revision = '{revision}'
          AND s.workspace_id = o.workspace_id
        """
    )
