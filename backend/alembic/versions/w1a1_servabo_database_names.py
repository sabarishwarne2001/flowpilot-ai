"""Servabo: the database's own names follow the rename (website and payments session, Part A4).

The audit-log trigger (`fn_audit_logs_prevent_mutation`) lets a session delete audit rows older
than 400 days only once it has named itself the retention sweeper through a custom setting. The
setting was `flowpilot.audit_sweeper_role`; it becomes `servabo.audit_sweeper_role`, and
`scripts/sweep_arch07.py` names itself under the new one in the same release.

Earlier migrations keep the old name: they are history, and editing them would change what a
database that ran them already holds. The body below is the one `arch08_step5_outcome_contract`
installed, with the setting renamed and nothing else changed. The old name is NOT kept as a second
key: two spellings would be two ways into an append-only log.

Downgrade puts the old setting name back, so the code of the previous release keeps working.

Revision ID: w1a1_servabo_database_names
Revises: h1a4_helm_api_vocabulary
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "w1a1_servabo_database_names"
down_revision: Union[str, None] = "h1a4_helm_api_vocabulary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FUNCTION = """
CREATE OR REPLACE FUNCTION fn_audit_logs_prevent_mutation()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    sweeper_role  text := current_setting('{setting}', true);
BEGIN
    IF (TG_OP = 'UPDATE') THEN
        RAISE EXCEPTION
            'audit_logs is append-only: UPDATE is not permitted (row id=%)', OLD.id
            USING ERRCODE = 'AU001',
                  HINT = 'Corrections are recorded as new audit rows, never as edits to existing ones.';
    END IF;

    IF (TG_OP = 'DELETE') THEN
        IF sweeper_role IS NOT NULL AND session_user = sweeper_role THEN
            IF OLD.created_at >= (now() - interval '400 days') THEN
                RAISE EXCEPTION
                    'The retention sweeper may only delete audit rows older than 400 days (row created_at=%)', OLD.created_at
                    USING ERRCODE = 'AU002';
            END IF;
            RETURN OLD;
        END IF;

        RAISE EXCEPTION
            'audit_logs is append-only: DELETE is not permitted. Only the retention sweeper role may delete rows older than 400 days.'
            USING ERRCODE = 'AU001';
    END IF;

    RETURN NULL;
END;
$$;
"""

NEW_SETTING = "servabo.audit_sweeper_role"
OLD_SETTING = "flowpilot.audit_sweeper_role"


def upgrade() -> None:
    op.execute(_FUNCTION.replace("{setting}", NEW_SETTING))


def downgrade() -> None:
    op.execute(_FUNCTION.replace("{setting}", OLD_SETTING))
