"""
ARCH-05 Step 1.5 — accept_invitation as a writer of the owner set.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.tokens import generate_secure_token, hash_token
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.organization_invitation import (
    InvitationStatus,
    OrganizationInvitation,
)
from app.models.user import User
from app.services import organization_invitation_service as invitation_service

from tests.conftest import TEST_DB_URL


@dataclass(frozen=True)
class Scene:
    organization_id: uuid.UUID
    owner_user_id: uuid.UUID
    alice_user_id: uuid.UUID
    owner_membership_id: uuid.UUID
    alice_membership_id: uuid.UUID
    alice_email: str
    token: str


@pytest.fixture(scope="module")
def engine_(test_database) -> Generator:
    engine = create_engine(TEST_DB_URL, poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture()
def sessions(engine_):
    return sessionmaker(bind=engine_, expire_on_commit=False)


@pytest.fixture()
def scene(sessions) -> Generator[Scene, None, None]:
    db: Session = sessions()
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)

    organization = Organization(
        slug=f"fifth-{suffix}",
        name="Fifth Ltd.",
        status=OrganizationStatus.ACTIVE,
    )
    db.add(organization)
    db.flush()

    founder = User(
        email=f"founder-{suffix}@fifth.test",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        email_verified_at=now,
    )
    alice_email = f"alice-{suffix}@fifth.test"
    alice = User(
        email=alice_email,
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        email_verified_at=now,
    )
    db.add_all([founder, alice])
    db.flush()

    founder_membership = OrganizationMember(
        organization_id=organization.id,
        user_id=founder.id,
        role=OrganizationRole.ADMIN,
        status=MembershipStatus.ACTIVE,
    )
    alice_membership = OrganizationMember(
        organization_id=organization.id,
        user_id=alice.id,
        role=OrganizationRole.OWNER,
        status=MembershipStatus.ACTIVE,
    )
    db.add_all([founder_membership, alice_membership])
    db.flush()

    token = generate_secure_token()
    invitation = OrganizationInvitation(
        organization_id=organization.id,
        inviter_id=founder.id,
        email=alice_email,
        organization_role=OrganizationRole.ADMIN,
        token_hash=hash_token(token),
        status=InvitationStatus.PENDING,
        expires_at=now + timedelta(hours=72),
    )
    db.add(invitation)
    db.commit()

    record = Scene(
        organization_id=organization.id,
        owner_user_id=founder.id,
        alice_user_id=alice.id,
        owner_membership_id=founder_membership.id,
        alice_membership_id=alice_membership.id,
        alice_email=alice_email,
        token=token,
    )
    db.close()

    yield record

    cleanup: Session = sessions()
    cleanup.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_immutable;"))
    cleanup.execute(
        text("DELETE FROM audit_logs WHERE organization_id = :o"),
        {"o": record.organization_id},
    )
    cleanup.execute(
        text("DELETE FROM organization_invitations WHERE organization_id = :o"),
        {"o": record.organization_id},
    )
    cleanup.execute(
        text("DELETE FROM organization_members WHERE organization_id = :o"),
        {"o": record.organization_id},
    )
    cleanup.execute(
        text("DELETE FROM organizations WHERE id = :o"),
        {"o": record.organization_id},
    )
    cleanup.execute(
        text("DELETE FROM users WHERE id IN (:a, :b)"),
        {"a": record.owner_user_id, "b": record.alice_user_id},
    )
    cleanup.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER trg_audit_logs_immutable;"))
    cleanup.commit()
    cleanup.close()


def _active_owners(db: Session, organization_id: uuid.UUID) -> int:
    return db.execute(
        select(func.count())
        .select_from(OrganizationMember)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role == OrganizationRole.OWNER,
            OrganizationMember.status == MembershipStatus.ACTIVE,
        )
    ).scalar_one()


def test_accepting_an_invitation_does_not_demote_an_active_owner(
    sessions, scene
):
    db: Session = sessions()
    alice = db.get(User, scene.alice_user_id)

    result = invitation_service.accept_invitation(
        db, token=scene.token, actor=alice
    )

    membership = db.get(OrganizationMember, scene.alice_membership_id)
    db.refresh(membership)

    assert membership.role is OrganizationRole.OWNER
    assert membership.status is MembershipStatus.ACTIVE
    assert _active_owners(db, scene.organization_id) == 1
    db.close()


def test_the_returned_role_reports_what_was_written(sessions, scene):
    db: Session = sessions()
    alice = db.get(User, scene.alice_user_id)
    result = invitation_service.accept_invitation(
        db, token=scene.token, actor=alice
    )
    assert result.organization_role is OrganizationRole.OWNER
    db.close()


def test_the_invitation_is_still_marked_accepted(sessions, scene):
    db: Session = sessions()
    alice = db.get(User, scene.alice_user_id)
    invitation_service.accept_invitation(db, token=scene.token, actor=alice)

    row = db.execute(
        select(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == scene.organization_id
        )
    ).scalar_one()
    db.refresh(row)
    assert row.status is InvitationStatus.ACCEPTED
    db.close()


def test_accept_invitation_takes_the_organization_lock(engine_, sessions, scene):
    blocker = engine_.connect()
    blocking_tx = blocker.begin()
    blocker.execute(
        text("SELECT id FROM organizations WHERE id = :o FOR UPDATE"),
        {"o": scene.organization_id},
    )

    db: Session = sessions()
    db.execute(text("SET LOCAL lock_timeout = '750ms'"))
    alice = db.get(User, scene.alice_user_id)

    try:
        with pytest.raises(OperationalError) as excinfo:
            invitation_service.accept_invitation(
                db, token=scene.token, actor=alice
            )
        assert "lock timeout" in str(excinfo.value).lower(), str(excinfo.value)
    finally:
        db.rollback()
        db.close()
        blocking_tx.rollback()
        blocker.close()


def test_a_non_owner_still_receives_the_invited_role(sessions, scene):
    db: Session = sessions()

    membership = db.get(OrganizationMember, scene.alice_membership_id)
    membership.role = OrganizationRole.MEMBER
    founder_membership = db.get(
        OrganizationMember, scene.owner_membership_id
    )
    founder_membership.role = OrganizationRole.OWNER
    db.commit()

    alice = db.get(User, scene.alice_user_id)
    result = invitation_service.accept_invitation(
        db, token=scene.token, actor=alice
    )

    db.refresh(membership)
    assert membership.role is OrganizationRole.ADMIN
    assert result.organization_role is OrganizationRole.ADMIN
    assert _active_owners(db, scene.organization_id) == 1
    db.close()


def test_a_deactivated_owner_row_is_not_silently_reinstated(sessions, scene):
    db: Session = sessions()

    alice_membership = db.get(OrganizationMember, scene.alice_membership_id)
    alice_membership.status = MembershipStatus.DEACTIVATED
    founder_membership = db.get(OrganizationMember, scene.owner_membership_id)
    founder_membership.role = OrganizationRole.OWNER
    db.commit()

    alice = db.get(User, scene.alice_user_id)
    result = invitation_service.accept_invitation(
        db, token=scene.token, actor=alice
    )

    db.refresh(alice_membership)
    assert alice_membership.role is OrganizationRole.ADMIN
    assert alice_membership.status is MembershipStatus.ACTIVE
    assert result.organization_role is OrganizationRole.ADMIN
    db.close()
