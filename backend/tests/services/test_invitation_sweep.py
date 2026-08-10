"""
ARCH-04 Step 8 -- sweep behavior unit tests.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.organization import Organization, OrganizationStatus, OrganizationRole
from app.models.workspace import Workspace, WorkspaceRole, WorkspaceStatus
from app.models.user import User
from app.models.organization_invitation import OrganizationInvitation, InvitationStatus
from app.services import organization_invitation_service as service
from scripts import sweep_invitations


@pytest.fixture
def db(db_session: Session) -> Session:
    return db_session


def _make_user(db: Session) -> User:
    user = User(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _make_organization(db: Session) -> Organization:
    org = Organization(
        slug=f"org-{uuid.uuid4().hex[:8]}",
        name="Acme Ltd",
        status=OrganizationStatus.ACTIVE,
    )
    db.add(org)
    db.flush()
    return org


def _make_invitation(
    db: Session, *, org: Organization, inviter: User, expires_delta_hours: int, status=InvitationStatus.PENDING
) -> OrganizationInvitation:
    from app.core.tokens import hash_token
    inv = OrganizationInvitation(
        organization_id=org.id,
        inviter_id=inviter.id,
        email=f"target-{uuid.uuid4().hex[:6]}@example.com",
        organization_role=OrganizationRole.MEMBER,
        status=status,
        token_hash=hash_token(uuid.uuid4().hex),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=expires_delta_hours),
        send_count=1,
    )
    db.add(inv)
    db.flush()
    return inv


class TestExpiry:
    def test_expires_only_lapsed_pending_invitations(self, db):
        org = _make_organization(db)
        inviter = _make_user(db)
        
        # 1. Stale pending -> should expire
        stale = _make_invitation(db, org=org, inviter=inviter, expires_delta_hours=-2)
        # 2. Fresh pending -> untouched
        fresh = _make_invitation(db, org=org, inviter=inviter, expires_delta_hours=24)
        db.commit()

        batches = service.sweep_expired_invitations(db)
        assert inviter.id in batches
        assert len(batches[inviter.id].lines) == 1
        
        db.refresh(stale)
        db.refresh(fresh)
        assert stale.status == InvitationStatus.EXPIRED
        assert fresh.status == InvitationStatus.PENDING

    def test_terminal_invitations_are_not_re_expired(self, db):
        org = _make_organization(db)
        inviter = _make_user(db)
        
        # Stale but already ACCEPTED -> untouched
        accepted = _make_invitation(db, org=org, inviter=inviter, expires_delta_hours=-2, status=InvitationStatus.ACCEPTED)
        db.commit()

        batches = service.sweep_expired_invitations(db)
        assert len(batches) == 0
        
        db.refresh(accepted)
        assert accepted.status == InvitationStatus.ACCEPTED


class TestDryRun:
    def test_dry_run_commits_nothing(self, db):
        org = _make_organization(db)
        inviter = _make_user(db)
        stale = _make_invitation(db, org=org, inviter=inviter, expires_delta_hours=-2)
        db.commit()

        # Start a nested savepoint transaction to isolate the uncommitted flush UPDATE
        nested = db.begin_nested()

        # Run sweep with commit=False (Dry-run rehearsal)
        batches = service.sweep_expired_invitations(db, commit=False)
        assert len(batches) == 1

        # Roll back only the nested savepoint to revert the UPDATE, preserving 'stale''s database row
        nested.rollback()

        # Query directly from DB to avoid transaction-level InstanceState refresh errors on Windows/Postgres
        refreshed = db.query(OrganizationInvitation).filter(OrganizationInvitation.id == stale.id).one()
        assert refreshed.status == InvitationStatus.PENDING  # Survived untouched!


class TestLocking:
    def test_second_concurrent_run_is_skipped(self, db_session):
        from sqlalchemy.pool import NullPool
        # Create an entirely isolated engine to guarantee a distinct PostgreSQL session
        isolated_engine = create_engine(settings.sqlalchemy_database_uri, poolclass=NullPool)
        conn1 = isolated_engine.connect()

        # Obtain the session-scoped advisory lock manually on the isolated connection
        conn1.execute(sa.text("SELECT pg_try_advisory_lock(40408)"))

        # Patch sys.argv to isolate the scripts.sweep_invitations argument parser from Pytest args
        with patch("sys.argv", ["scripts/sweep_invitations.py"]):
            exit_code = sweep_invitations.main()

        # Unlock and close the isolated connection
        conn1.execute(sa.text("SELECT pg_advisory_unlock(40408)"))
        conn1.close()
        isolated_engine.dispose()

        assert exit_code == 3