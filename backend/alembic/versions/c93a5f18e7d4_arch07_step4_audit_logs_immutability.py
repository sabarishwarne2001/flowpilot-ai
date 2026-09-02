"""arch07_step4_audit_logs_immutability

Revision ID: c93a5f18e7d4
Revises: b1d7c4e9a052
Create Date: 2026-08-13 15:00:00.000000

ARCH-07 Step 4 — audit_logs immutability (§B.3 Option C).

Installs BEFORE UPDATE OR DELETE trigger (trg_audit_logs_immutable) and
BEFORE TRUNCATE statement trigger (trg_audit_logs_no_truncate) on audit_logs.
"""

from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c93a5f18e7d4'
down_revision: Union[str, None] = 'b1d7c4e9a052'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RETENTION_DAYS = 400
IMMUTABILITY_ERRCODE = "AU001"


TRIGGER_FUNCTION = f"""
CREATE OR REPLACE FUNCTION fn_audit_logs_prevent_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    sweeper_role text := current_setting('flowpilot.audit_sweeper_role', true);
BEGIN
    IF (TG_OP = 'UPDATE') THEN
        RAISE EXCEPTION
            'audit_logs is append-only: UPDATE is not permitted (row id=%)',
            OLD.id
            USING ERRCODE = '{IMMUTABILITY_ERRCODE}',
                  HINT = 'Corrections are recorded as new audit rows, never as edits to existing ones.';
    END IF;

    IF (TG_OP = 'DELETE') THEN
        IF sweeper_role IS NOT NULL
           AND sweeper_role <> ''
           AND pg_has_role(current_user, sweeper_role, 'MEMBER')
           AND OLD.created_at < (now() - interval '{RETENTION_DAYS} days')
        THEN
            RETURN OLD;
        END IF;

        RAISE EXCEPTION
            'audit_logs is append-only: DELETE is not permitted (row id=%, created_at=%)',
            OLD.id, OLD.created_at
            USING ERRCODE = '{IMMUTABILITY_ERRCODE}',
                  HINT = 'Only the configured retention sweeper role may delete rows older than {RETENTION_DAYS} days.';
    END IF;

    RETURN NULL;
END;
$$;
"""


TRUNCATE_FUNCTION = f"""
CREATE OR REPLACE FUNCTION fn_audit_logs_prevent_truncate()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only: TRUNCATE is not permitted'
        USING ERRCODE = '{IMMUTABILITY_ERRCODE}';
END;
$$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    
    # Safely fetch x_argument via op helper
    try:
        x_args = op.get_x_argument(as_dictionary=True)
    except Exception:
        x_args = {}

    app_role = x_args.get("app_role") if isinstance(x_args, dict) else None
    sweeper_role = x_args.get("sweeper_role") if isinstance(x_args, dict) else None

    op.execute(TRIGGER_FUNCTION)
    op.execute(TRUNCATE_FUNCTION)

    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_immutable
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION fn_audit_logs_prevent_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_no_truncate
        BEFORE TRUNCATE ON audit_logs
        FOR EACH STATEMENT
        EXECUTE FUNCTION fn_audit_logs_prevent_truncate();
        """
    )

    if sweeper_role:
        database = bind.execute(sa.text("SELECT current_database()")).scalar_one()
        op.execute(
            f'ALTER DATABASE "{database}" '
            f"SET flowpilot.audit_sweeper_role = '{sweeper_role}'"
        )
        exists = bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name"),
            {"name": sweeper_role},
        ).scalar()
        if exists:
            op.execute(f'GRANT SELECT, DELETE ON audit_logs TO "{sweeper_role}"')
        else:
            print(f"[WARN] sweeper role '{sweeper_role}' does not exist; GRANT skipped.")

    if app_role:
        exists = bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name"),
            {"name": app_role},
        ).scalar()
        if exists:
            op.execute(
                f'REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM "{app_role}"'
            )
            op.execute(f'GRANT SELECT, INSERT ON audit_logs TO "{app_role}"')


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_truncate ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_immutable ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS fn_audit_logs_prevent_truncate()")
    op.execute("DROP FUNCTION IF EXISTS fn_audit_logs_prevent_mutation()")
