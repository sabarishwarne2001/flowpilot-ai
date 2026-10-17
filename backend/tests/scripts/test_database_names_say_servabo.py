"""Database-level names follow the rename to Servabo (website and payments session, Part A4).

The audit-log trigger lets a session delete old audit rows only after it has named itself the
retention sweeper through a custom setting. The setting was called `flowpilot.audit_sweeper_role`;
the migration `w1a1_servabo_database_names` makes the trigger read `servabo.audit_sweeper_role`, and
the nightly sweep names itself under the new setting. The old name must mean nothing afterwards,
or two spellings would each open the append-only log.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditLog, AuditResourceType
from scripts import sweep_arch07
from tests.conftest import Fixture


def _old_entry(db: Session, organization_id) -> None:
    db.add(AuditLog(
        created_at=datetime.now(UTC) - timedelta(days=900), organization_id=organization_id,
        resource_type=AuditResourceType.WORKSPACE, action=AuditAction.UPDATED,
    ))
    db.commit()


def test_the_audit_trigger_reads_the_servabo_setting(db_session: Session) -> None:
    body = db_session.execute(
        text("SELECT pg_get_functiondef('fn_audit_logs_prevent_mutation'::regproc)")
    ).scalar_one()
    assert "servabo.audit_sweeper_role" in body
    assert "flowpilot." not in body


def test_the_old_setting_name_no_longer_opens_the_log(db_session: Session, tenant: Fixture) -> None:
    _old_entry(db_session, tenant.organization.id)
    db_session.execute(text("SELECT set_config('flowpilot.audit_sweeper_role', session_user, true)"))
    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(
            text("DELETE FROM audit_logs WHERE organization_id = :o"), {"o": tenant.organization.id}
        )
    db_session.rollback()


def test_the_sweep_names_itself_under_the_servabo_setting(db_session: Session, tenant: Fixture) -> None:
    _old_entry(db_session, tenant.organization.id)
    session = sweep_arch07._audit_sweeper_session()  # noqa: SLF001
    try:
        deleted = sweep_arch07.delete_as_sweeper(
            session, "organization_id = :organization", {"organization": tenant.organization.id}
        )
        assert deleted >= 1
        named = session.execute(text("SELECT current_setting('servabo.audit_sweeper_role', true)")).scalar()
        assert named == session.execute(text("SELECT session_user")).scalar()
    finally:
        session.rollback()
        session.close()
