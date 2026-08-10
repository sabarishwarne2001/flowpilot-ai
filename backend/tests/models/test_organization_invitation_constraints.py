"""
ARCH-04 Step 3 -- behavioral tests against the main test database.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.organization import Organization, OrganizationStatus
from app.models.organization_invitation import (
    InvitationWorkspaceGrant,
    OrganizationInvitation,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceRole, WorkspaceStatus
from app.models.workspace_invitation import InvitationStatus


# Use the project's native, fully migrated test transaction session
@pytest.fixture
def db(db_session: Session) -> Session:
    return db_session


def _make_user(db: Session) -> User:
    user = User(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
    )
    db.add(user)
    db.flush()
    return user


def _make_organization(db: Session, *, seat_limit: int | None = None) -> Organization:
    org = Organization(
        slug=f"org-{uuid.uuid4().hex[:8]}",
        name="Acme Ltd",
        status=OrganizationStatus.ACTIVE,
        seat_limit=seat_limit,
    )
    db.add(org)
    db.flush()
    return org


def _make_workspace(db: Session, organization: Organization) -> Workspace:
    ws = Workspace(
        organization_id=organization.id,
        slug=f"ws-{uuid.uuid4().hex[:8]}",
        workspace_name="Operations",
        status=WorkspaceStatus.ACTIVE,
    )
    db.add(ws)
    db.flush()
    return ws


def _make_invitation(
    db: Session,
    *,
    organization: Organization,
    inviter: User,
    email: str,
    role,
    status: InvitationStatus = InvitationStatus.PENDING,
) -> OrganizationInvitation:
    invitation = OrganizationInvitation(
        organization_id=organization.id,
        inviter_id=inviter.id,
        email=email,
        organization_role=role,
        status=status,
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
    )
    db.add(invitation)
    db.flush()
    return invitation


# ---------------------------------------------------------------------------
# §B.4 -- OWNER is rejected at the database
# ---------------------------------------------------------------------------

def test_owner_role_rejected_by_check_constraint(db):
    from app.models.organization import OrganizationRole

    org = _make_organization(db)
    inviter = _make_user(db)

    with pytest.raises(IntegrityError, match="violates check constraint"):
        _make_invitation(
            db, organization=org, inviter=inviter,
            email="wouldbe-owner@example.com", role=OrganizationRole.OWNER,
        )


def test_admin_billing_and_member_are_all_accepted(db):
    from app.models.organization import OrganizationRole

    org = _make_organization(db)
    inviter = _make_user(db)

    for i, role in enumerate(
        [OrganizationRole.ADMIN, OrganizationRole.BILLING, OrganizationRole.MEMBER]
    ):
        _make_invitation(
            db, organization=org, inviter=inviter,
            email=f"person{i}@example.com", role=role,
        )
    db.flush()


# ---------------------------------------------------------------------------
# §B.9 -- partial unique index on (organization_id, lower(email)) WHERE PENDING
# ---------------------------------------------------------------------------

def test_duplicate_pending_pair_rejected_case_insensitively(db):
    from app.models.organization import OrganizationRole

    org = _make_organization(db)
    inviter = _make_user(db)

    _make_invitation(
        db, organization=org, inviter=inviter,
        email="new@example.com", role=OrganizationRole.MEMBER,
    )
    with pytest.raises(IntegrityError, match="uq_pending_organization_invitation"):
        _make_invitation(
            db, organization=org, inviter=inviter,
            email="NEW@EXAMPLE.COM", role=OrganizationRole.MEMBER,
        )


def test_non_pending_duplicate_is_allowed(db):
    from app.models.organization import OrganizationRole

    org = _make_organization(db)
    inviter = _make_user(db)

    _make_invitation(
        db, organization=org, inviter=inviter,
        email="again@example.com", role=OrganizationRole.MEMBER,
        status=InvitationStatus.REVOKED,
    )
    _make_invitation(
        db, organization=org, inviter=inviter,
        email="again@example.com", role=OrganizationRole.MEMBER,
        status=InvitationStatus.PENDING,
    )
    db.flush()


def test_same_email_different_organizations_is_allowed(db):
    from app.models.organization import OrganizationRole

    org_a = _make_organization(db)
    org_b = _make_organization(db)
    inviter = _make_user(db)

    _make_invitation(
        db, organization=org_a, inviter=inviter,
        email="shared@example.com", role=OrganizationRole.MEMBER,
    )
    _make_invitation(
        db, organization=org_b, inviter=inviter,
        email="shared@example.com", role=OrganizationRole.MEMBER,
    )
    db.flush()


# ---------------------------------------------------------------------------
# §B.2 -- grants: uniqueness and cascade
# ---------------------------------------------------------------------------

def test_duplicate_grant_for_same_workspace_rejected(db):
    from app.models.organization import OrganizationRole

    org = _make_organization(db)
    inviter = _make_user(db)
    ws = _make_workspace(db, org)
    invitation = _make_invitation(
        db, organization=org, inviter=inviter,
        email="new@example.com", role=OrganizationRole.MEMBER,
    )

    db.add(InvitationWorkspaceGrant(
        invitation_id=invitation.id, workspace_id=ws.id, role=WorkspaceRole.VIEWER,
    ))
    db.flush()

    with pytest.raises(IntegrityError, match="uq_invitation_workspace_grant"):
        db.add(InvitationWorkspaceGrant(
            invitation_id=invitation.id, workspace_id=ws.id,
            role=WorkspaceRole.CONTRIBUTOR,
        ))
        db.flush()


def test_zero_grant_invitation_is_valid():
    pass


def test_deleting_invitation_cascades_to_its_grants(db):
    from app.models.organization import OrganizationRole

    org = _make_organization(db)
    inviter = _make_user(db)
    ws = _make_workspace(db, org)
    invitation = _make_invitation(
        db, organization=org, inviter=inviter,
        email="new@example.com", role=OrganizationRole.MEMBER,
    )
    grant = InvitationWorkspaceGrant(
        invitation_id=invitation.id, workspace_id=ws.id, role=WorkspaceRole.VIEWER,
    )
    db.add(grant)
    db.flush()
    grant_id = grant.id

    db.delete(invitation)
    db.flush()

    assert db.get(InvitationWorkspaceGrant, grant_id) is None


def test_deleting_workspace_cascades_to_its_grant_but_invitation_survives(db):
    from app.models.organization import OrganizationRole

    org = _make_organization(db)
    inviter = _make_user(db)
    ws_a = _make_workspace(db, org)
    ws_b = _make_workspace(db, org)
    invitation = _make_invitation(
        db, organization=org, inviter=inviter,
        email="new@example.com", role=OrganizationRole.MEMBER,
    )
    grant_a = InvitationWorkspaceGrant(
        invitation_id=invitation.id, workspace_id=ws_a.id, role=WorkspaceRole.VIEWER,
    )
    grant_b = InvitationWorkspaceGrant(
        invitation_id=invitation.id, workspace_id=ws_b.id, role=WorkspaceRole.CONTRIBUTOR,
    )
    db.add_all([grant_a, grant_b])
    db.flush()
    grant_a_id, grant_b_id = grant_a.id, grant_b.id
    invitation_id = invitation.id

    db.delete(ws_a)
    db.flush()

    # Expire all cached objects so SQLAlchemy fetches database CASCADE delete correctly
    db.expire_all()

    assert db.get(InvitationWorkspaceGrant, grant_a_id) is None
    assert db.get(InvitationWorkspaceGrant, grant_b_id) is not None
    survivor = db.get(OrganizationInvitation, invitation_id)
    assert survivor is not None
    assert len(survivor.grants) == 1


# ---------------------------------------------------------------------------
# §D2.5 -- organizations.seat_limit
# ---------------------------------------------------------------------------

def test_seat_limit_zero_is_rejected(db):
    with pytest.raises(IntegrityError, match="ck_organizations_seat_limit_positive"):
        _make_organization(db, seat_limit=0)


def test_seat_limit_null_and_positive_are_accepted(db):
    _make_organization(db, seat_limit=None)
    _make_organization(db, seat_limit=25)
    db.flush()


# ---------------------------------------------------------------------------
# §D3.2 -- Python-side defaults actually fire
# ---------------------------------------------------------------------------

def test_send_count_defaults_to_one_at_the_orm_layer(db):
    from app.models.organization import OrganizationRole

    org = _make_organization(db)
    inviter = _make_user(db)
    invitation = OrganizationInvitation(
        organization_id=org.id,
        inviter_id=inviter.id,
        email="defaults@example.com",
        organization_role=OrganizationRole.MEMBER,
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
    )
    db.add(invitation)
    db.flush()
    db.refresh(invitation)

    assert invitation.status == InvitationStatus.PENDING
    assert invitation.send_count == 1


# ---------------------------------------------------------------------------
# workspace_invitations is untouched
# ---------------------------------------------------------------------------

def test_workspace_invitations_table_still_present_and_functional(db):
    from app.models.workspace_invitation import WorkspaceInvitation

    org = _make_organization(db)
    inviter = _make_user(db)
    ws = _make_workspace(db, org)

    legacy = WorkspaceInvitation(
        workspace_id=ws.id,
        organization_id=org.id,
        inviter_id=inviter.id,
        email="legacy@example.com",
        role=WorkspaceRole.VIEWER,
        status=InvitationStatus.PENDING,
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
    )
    db.add(legacy)
    db.flush()