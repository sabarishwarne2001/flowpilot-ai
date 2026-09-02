"""arch07_step11_harden_audit_trigger

Revision ID: a8b3f5c02d47
Revises: e2a84c7b60f1
Create Date: 2026-08-13 22:00:00.000000

ARCH-07 Step 11 — harden audit immutability trigger (removes GUC bypass).
"""

from __future__ import annotations
from typing import Union,Sequence

import re
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a8b3f5c02d47"
down_revision: Union[str, None] = "e2a84c7b60f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RETENTION_DAYS = 400
IMMUTABILITY_ERRCODE = "AU001"


def _hardened_function(sweeper_role: str | None) -> str:
    if sweeper_role is None:
        carve_out = """
        -- No sweeper role configured at migration time. DELETE is refused unconditionally.
        """
    else:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", sweeper_role):
            raise RuntimeError(
                f"Refusing to inline role name {sweeper_role!r} into a function body."
            )
        carve_out = f"""
        IF pg_has_role(current_user, '{sweeper_role}', 'MEMBER')
           AND OLD.created_at < (now() - interval '{RETENTION_DAYS} days')
        THEN
            RETURN OLD;
        END IF;
        """

    return f"""
CREATE OR REPLACE FUNCTION fn_audit_logs_prevent_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF (TG_OP = 'UPDATE') THEN
        RAISE EXCEPTION
            'audit_logs is append-only: UPDATE is not permitted (row id=%)',
            OLD.id
            USING ERRCODE = '{IMMUTABILITY_ERRCODE}',
                  HINT = 'Corrections are recorded as new audit rows.';
    END IF;

    IF (TG_OP = 'DELETE') THEN
        {carve_out}

        RAISE EXCEPTION
            'audit_logs is append-only: DELETE is not permitted (row id=%, created_at=%)',
            OLD.id, OLD.created_at
            USING ERRCODE = '{IMMUTABILITY_ERRCODE}',
                  HINT = 'Only the retention sweeper role may delete rows older than {RETENTION_DAYS} days.';
    END IF;

    RETURN NULL;
END;
$$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    try:
        x_args = op.get_x_argument(as_dictionary=True)
    except Exception:
        x_args = {}

    sweeper_role = x_args.get("sweeper_role") if isinstance(x_args, dict) else None

    if sweeper_role:
        exists = bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name"),
            {"name": sweeper_role},
        ).scalar()
        if not exists:
            print(f"[WARN] Role {sweeper_role!r} does not exist. Skipping inlined role definition.")

    op.execute(_hardened_function(sweeper_role))

    database = bind.execute(sa.text("SELECT current_database()")).scalar_one()
    op.execute(f'ALTER DATABASE "{database}" RESET flowpilot.audit_sweeper_role')

    if sweeper_role:
        op.execute(f'GRANT SELECT, DELETE ON audit_logs TO "{sweeper_role}"')


def downgrade() -> None:
    try:
        x_args = op.get_x_argument(as_dictionary=True)
    except Exception:
        x_args = {}

    sweeper_role = x_args.get("sweeper_role") if isinstance(x_args, dict) else None
    if sweeper_role:
        op.execute(f'REVOKE DELETE ON audit_logs FROM "{sweeper_role}"')
