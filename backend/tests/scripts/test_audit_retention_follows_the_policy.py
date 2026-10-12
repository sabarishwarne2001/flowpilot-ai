"""The audit sweep keeps each organization's audit log as long as it chose (campaign session 3, F-304).

`Organization > Compliance > Retention` offers "Audit log (days)", minimum 400, and says "Leave a
field blank to keep that data indefinitely". Nothing read the setting: the nightly ARCH-07 sweep
deleted every organization's audit entries older than a fixed 400 days, so an organization that
kept them for seven years (or forever) lost them at 400 days, and one that chose 500 kept nothing
past 400.

The sweep now works per organization: a period that is set is honoured, a blank keeps the log.
The test database refuses audit deletions outright (the sweeper role exists only in production),
so this proves the cutoffs and the dry-run count, which uses the same filter as the delete.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit_log import AuditAction, AuditLog, AuditResourceType
from app.models.compliance import RetentionPolicy
from scripts import sweep_arch07
from tests.conftest import Fixture


def _policy(db: Session, organization_id, days) -> None:
    db.add(RetentionPolicy(organization_id=organization_id, audit_retention_days=days))
    db.commit()


def _entry(db: Session, organization_id, age_days: int) -> None:
    db.add(AuditLog(
        created_at=datetime.now(UTC) - timedelta(days=age_days), organization_id=organization_id,
        resource_type=AuditResourceType.WORKSPACE, action=AuditAction.UPDATED,
    ))
    db.commit()


def test_each_organization_gets_its_own_cutoff(db_session: Session, tenant: Fixture) -> None:
    keeps_500 = tenant.organization.id
    keeps_forever = tenant.foreign_workspace.organization_id
    _policy(db_session, keeps_500, 500)
    now = datetime.now(UTC)
    cutoffs = dict(sweep_arch07.audit_retention_cutoffs(db_session, now=now))
    assert cutoffs[keeps_500] == now - timedelta(days=500)
    assert keeps_forever not in cutoffs, "no period chosen: the screen promises the log is kept"


def test_a_blank_policy_keeps_the_log(db_session: Session, tenant: Fixture) -> None:
    _policy(db_session, tenant.organization.id, None)
    cutoffs = dict(sweep_arch07.audit_retention_cutoffs(db_session, now=datetime.now(UTC)))
    assert tenant.organization.id not in cutoffs


@pytest.fixture
def sweeper_reads_the_test_database(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUDIT_SWEEPER_DATABASE_URL", SecretStr(settings.sqlalchemy_database_uri))


def test_the_sweep_counts_only_entries_past_their_organizations_period(
    db_session: Session, tenant: Fixture, sweeper_reads_the_test_database
) -> None:
    keeps_500 = tenant.organization.id
    keeps_forever = tenant.foreign_workspace.organization_id
    _policy(db_session, keeps_500, 500)

    def due() -> int:
        result = sweep_arch07.sweep_audit_retention(dry_run=True)
        assert result.failed == 0, result.notes
        return result.examined

    before = due()
    _entry(db_session, keeps_500, 450)       # inside its 500 days
    _entry(db_session, keeps_forever, 900)   # no period: kept
    assert due() == before, "nothing here is past its organization's period"
    _entry(db_session, keeps_500, 600)       # past its 500 days
    assert due() == before + 1
