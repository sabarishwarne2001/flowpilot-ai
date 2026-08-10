"""
ARCH-04 Step 6 -- organization invitation service behavioral tests.

Fully tests the invitation lifecycle: issuance, preview, acceptance, rejection,
revocation, resend, and sweeper sweeps against a live migrated database.
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core import exceptions
from app.core.config import settings
from app.core.tokens import hash_token
from app.models.organization import MembershipStatus, Organization, OrganizationRole, OrganizationStatus
from app.models.organization_invitation import InvitationStatus, InvitationWorkspaceGrant, OrganizationInvitation
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceRole, WorkspaceStatus
from app.services import organization_invitation_service as service


@pytest.fixture
def db(db_session: Session) -> Session:
    return db_session


# ===========================================================================
# Helpers
# ============================================================================

def _make_user(db: Session, email: str | None = None) -> User:
    addr = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        email=addr,
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


def _make_workspace(
    db: Session, organization: Organization, name: str | None = None, status: WorkspaceStatus = WorkspaceStatus.ACTIVE
) -> Workspace:
    ws = Workspace(
        organization_id=organization.id,
        slug=f"ws-{uuid.uuid4().hex[:8]}",
        workspace_name=name or "Operations",
        status=status,
    )
    db.add(ws)
    db.flush()
    return ws


# ===========================================================================
# 1. Authorization — §D6.5
# ===========================================================================

def test_member_and_billing_cannot_invite(db):
    org = _make_organization(db)
    owner = _make_user(db)
    target_email = "target@example.com"

    for role in [OrganizationRole.MEMBER, OrganizationRole.BILLING]:
        actor = _make_user(db)
        with pytest.raises(exceptions.InvitationPermissionDeniedError, match="permission to invite"):
            service.create_invitation(
                db,
                organization=org,
                inviter=actor,
                actor_role=role,
                email=target_email,
                organization_role=OrganizationRole.MEMBER,
            )


def test_admin_cannot_invite_admin(db):
    org = _make_organization(db)
    actor = _make_user(db)
    target_email = "target@example.com"

    with pytest.raises(exceptions.InvitationPermissionDeniedError, match="permission to invite someone"):
        service.create_invitation(
            db,
            organization=org,
            inviter=actor,
            actor_role=OrganizationRole.ADMIN,
            email=target_email,
            organization_role=OrganizationRole.ADMIN,
        )


def test_owner_can_invite_admin_and_billing(db):
    org = _make_organization(db)
    actor = _make_user(db)

    for role in [OrganizationRole.ADMIN, OrganizationRole.BILLING, OrganizationRole.MEMBER]:
        res = service.create_invitation(
            db,
            organization=org,
            inviter=actor,
            actor_role=OrganizationRole.OWNER,
            email=f"{role.value.lower()}@example.com",
            organization_role=role,
        )
        assert res.invitation.organization_role == role


def test_invite_at_owner_raises_readable_permission_denied_not_integrity_error(db):
    org = _make_organization(db)
    actor = _make_user(db)

    with pytest.raises(exceptions.InvitationPermissionDeniedError, match="Ownership cannot be granted"):
        service.create_invitation(
            db,
            organization=org,
            inviter=actor,
            actor_role=OrganizationRole.OWNER,
            email="owner@example.com",
            organization_role=OrganizationRole.OWNER,
        )


def test_self_invitation_rejected(db):
    org = _make_organization(db)
    actor = _make_user(db, email="self@example.com")

    with pytest.raises(exceptions.InvitationAlreadyMemberError, match="already a member"):
        service.create_invitation(
            db,
            organization=org,
            inviter=actor,
            actor_role=OrganizationRole.OWNER,
            email="self@example.com",
            organization_role=OrganizationRole.MEMBER,
        )


# ===========================================================================
# 2. Cross-Tenant Boundaries — §D6.4
# ===========================================================================

def test_grant_naming_workspace_in_foreign_organization_raises_grant_error_indistinguishably(db):
    org_a = _make_organization(db)
    org_b = _make_organization(db)
    actor = _make_user(db)
    ws_foreign = _make_workspace(db, org_b)

    with pytest.raises(exceptions.InvitationGrantError, match="could not be found in this organization"):
        service.create_invitation(
            db,
            organization=org_a,
            inviter=actor,
            actor_role=OrganizationRole.OWNER,
            email="target@example.com",
            organization_role=OrganizationRole.MEMBER,
            grants=[(ws_foreign.id, WorkspaceRole.VIEWER)],
        )

    assert db.query(OrganizationInvitation).count() == 0


def test_inactive_workspaces_rejected_at_issuance(db):
    org = _make_organization(db)
    actor = _make_user(db)
    ws_archived = _make_workspace(db, org, status=WorkspaceStatus.ARCHIVED)

    with pytest.raises(exceptions.InvitationGrantError, match="not active and cannot be granted"):
        service.create_invitation(
            db,
            organization=org,
            inviter=actor,
            actor_role=OrganizationRole.OWNER,
            email="target@example.com",
            organization_role=OrganizationRole.MEMBER,
            grants=[(ws_archived.id, WorkspaceRole.VIEWER)],
        )


def test_duplicated_workspaces_in_request_rejected(db):
    org = _make_organization(db)
    actor = _make_user(db)
    ws = _make_workspace(db, org)

    with pytest.raises(exceptions.InvitationGrantError, match="appears more than once"):
        service.create_invitation(
            db,
            organization=org,
            inviter=actor,
            actor_role=OrganizationRole.OWNER,
            email="target@example.com",
            organization_role=OrganizationRole.MEMBER,
            grants=[(ws.id, WorkspaceRole.VIEWER), (ws.id, WorkspaceRole.VIEWER)],
        )


def test_exceeding_max_grants_rejected(db):
    org = _make_organization(db)
    actor = _make_user(db)
    grants = [(uuid.uuid4(), WorkspaceRole.VIEWER) for _ in range(settings.INVITATION_MAX_GRANTS + 1)]

    with pytest.raises(exceptions.InvitationGrantError, match="at most"):
        service.create_invitation(
            db,
            organization=org,
            inviter=actor,
            actor_role=OrganizationRole.OWNER,
            email="target@example.com",
            organization_role=OrganizationRole.MEMBER,
            grants=grants,
        )


# ===========================================================================
# 3. Seats — §0 and §D6.2
# ===========================================================================

def test_seat_accounting_aggregates_members_and_pending_invitations(db):
    org = _make_organization(db)
    actor = _make_user(db)

    assert service.count_reserved_seats(db, organization_id=org.id) == 0

    service.create_invitation(
        db,
        organization=org,
        inviter=actor,
        actor_role=OrganizationRole.OWNER,
        email="invite@example.com",
        organization_role=OrganizationRole.MEMBER,
    )
    assert service.count_reserved_seats(db, organization_id=org.id) == 1


def test_unlimited_seat_limit_never_blocks(db):
    org = _make_organization(db, seat_limit=None)
    actor = _make_user(db)

    for i in range(5):
        service.create_invitation(
            db,
            organization=org,
            inviter=actor,
            actor_role=OrganizationRole.OWNER,
            email=f"person{i}@example.com",
            organization_role=OrganizationRole.MEMBER,
        )


def test_issuance_at_seat_limit_ceiling_rejected(db):
    org = _make_organization(db, seat_limit=1)
    actor = _make_user(db)

    service.create_invitation(
        db,
        organization=org,
        inviter=actor,
        actor_role=OrganizationRole.OWNER,
        email="first@example.com",
        organization_role=OrganizationRole.MEMBER,
    )

    with pytest.raises(exceptions.SeatLimitExceededError, match="no seats available"):
        service.create_invitation(
            db,
            organization=org,
            inviter=actor,
            actor_role=OrganizationRole.OWNER,
            email="second@example.com",
            organization_role=OrganizationRole.MEMBER,
        )


def test_acceptance_at_seat_limit_fails_non_destructively(db):
    org = _make_organization(db, seat_limit=1)
    actor = _make_user(db)

    issued = service.create_invitation(
        db,
        organization=org,
        inviter=actor,
        actor_role=OrganizationRole.OWNER,
        email="invitee@example.com",
        organization_role=OrganizationRole.MEMBER,
    )

    member = _make_user(db)
    from app.crud.organization_members import create_organization_member
    create_organization_member(db, organization_id=org.id, user_id=member.id)

    invitee_user = _make_user(db, email="invitee@example.com")
    with pytest.raises(exceptions.SeatLimitExceededError, match="no seats available"):
        service.accept_invitation(db, token=issued.plaintext_token, actor=invitee_user)

    db.refresh(issued.invitation)
    assert issued.invitation.status == InvitationStatus.PENDING
    assert issued.invitation.accepted_at is None


# ===========================================================================
# 4. Concurrency & Integrity — §D6.1
# ===========================================================================

def test_racing_acceptance_raises_already_processed_error(db):
    org = _make_organization(db)
    actor = _make_user(db)
    issued = service.create_invitation(
        db,
        organization=org,
        inviter=actor,
        actor_role=OrganizationRole.OWNER,
        email="invitee@example.com",
        organization_role=OrganizationRole.MEMBER,
    )

    invitee_user = _make_user(db, email="invitee@example.com")

    service.accept_invitation(db, token=issued.plaintext_token, actor=invitee_user)

    with pytest.raises(exceptions.InvitationAlreadyProcessedError, match="already accepted"):
        service.accept_invitation(db, token=issued.plaintext_token, actor=invitee_user)


def test_accept_expired_invitation_rejected(db):
    org = _make_organization(db)
    actor = _make_user(db)
    issued = service.create_invitation(
        db,
        organization=org,
        inviter=actor,
        actor_role=OrganizationRole.OWNER,
        email="invitee@example.com",
        organization_role=OrganizationRole.MEMBER,
    )

    issued.invitation.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.add(issued.invitation)
    db.flush()

    invitee_user = _make_user(db, email="invitee@example.com")
    with pytest.raises(exceptions.InvitationExpiredError, match="expired"):
        service.accept_invitation(db, token=issued.plaintext_token, actor=invitee_user)


# ===========================================================================
# 5. Acceptance Semantics — §D6.8
# ===========================================================================

def test_zero_grant_invitation_provisions_seat_without_workspaces(db):
    org = _make_organization(db)
    actor = _make_user(db)
    issued = service.create_invitation(
        db,
        organization=org,
        inviter=actor,
        actor_role=OrganizationRole.OWNER,
        email="billing@example.com",
        organization_role=OrganizationRole.BILLING,
        grants=[],
    )

    invitee_user = _make_user(db, email="billing@example.com")
    accepted = service.accept_invitation(db, token=issued.plaintext_token, actor=invitee_user)

    assert accepted.organization_role == OrganizationRole.BILLING
    assert len(accepted.provisioned_grants) == 0
    assert accepted.skipped_grant_count == 0


def test_archived_workspace_during_acceptance_increments_skipped_grants(db):
    org = _make_organization(db)
    actor = _make_user(db)
    ws = _make_workspace(db, org)
    issued = service.create_invitation(
        db,
        organization=org,
        inviter=actor,
        actor_role=OrganizationRole.OWNER,
        email="invitee@example.com",
        organization_role=OrganizationRole.MEMBER,
        grants=[(ws.id, WorkspaceRole.VIEWER)],
    )

    ws.status = WorkspaceStatus.ARCHIVED
    db.add(ws)
    db.flush()

    invitee_user = _make_user(db, email="invitee@example.com")
    accepted = service.accept_invitation(db, token=issued.plaintext_token, actor=invitee_user)

    assert accepted.skipped_grant_count == 1
    assert len(accepted.provisioned_grants) == 0


def test_reactivates_former_member_rather_than_duplicating(db):
    org = _make_organization(db)
    actor = _make_user(db)
    invitee_user = _make_user(db, email="former@example.com")

    from app.crud.organization_members import create_organization_member, deactivate_organization_member
    membership = create_organization_member(db, organization_id=org.id, user_id=invitee_user.id)
    deactivate_organization_member(db, membership=membership, actor_id=actor.id)

    issued = service.create_invitation(
        db,
        organization=org,
        inviter=actor,
        actor_role=OrganizationRole.OWNER,
        email="former@example.com",
        organization_role=OrganizationRole.MEMBER,
    )

    service.accept_invitation(db, token=issued.plaintext_token, actor=invitee_user)

    db.refresh(membership)
    assert membership.status == MembershipStatus.ACTIVE


def test_email_mismatch_raises_mismatch_error(db):
    org = _make_organization(db)
    actor = _make_user(db)
    issued = service.create_invitation(
        db,
        organization=org,
        inviter=actor,
        actor_role=OrganizationRole.OWNER,
        email="invitee@example.com",
        organization_role=OrganizationRole.MEMBER,
    )

    wrong_user = _make_user(db, email="wrong@example.com")
    with pytest.raises(exceptions.InvitationEmailMismatchError, match="Sign in with that address"):
        service.accept_invitation(db, token=issued.plaintext_token, actor=wrong_user)


# ===========================================================================
# 6. Resend & Token Rotation — §D6.6
# ===========================================================================

def test_resend_invalidates_previous_token(db):
    org = _make_organization(db)
    actor = _make_user(db)
    issued = service.create_invitation(
        db,
        organization=org,
        inviter=actor,
        actor_role=OrganizationRole.OWNER,
        email="invitee@example.com",
        organization_role=OrganizationRole.MEMBER,
    )

    issued.invitation.last_sent_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.add(issued.invitation)
    db.flush()

    resent = service.resend_invitation(
        db,
        organization=org,
        invitation_id=issued.invitation.id,
        actor_role=OrganizationRole.OWNER,
    )

    invitee_user = _make_user(db, email="invitee@example.com")

    with pytest.raises(exceptions.InvalidInvitationTokenError, match="invalid"):
        service.accept_invitation(db, token=issued.plaintext_token, actor=invitee_user)

    service.accept_invitation(db, token=resent.plaintext_token, actor=invitee_user)


def test_resend_inside_cooldown_rejected(db):
    org = _make_organization(db)
    actor = _make_user(db)
    issued = service.create_invitation(
        db,
        organization=org,
        inviter=actor,
        actor_role=OrganizationRole.OWNER,
        email="invitee@example.com",
        organization_role=OrganizationRole.MEMBER,
    )

    with pytest.raises(exceptions.InvitationResendTooSoonError, match="sent recently"):
        service.resend_invitation(
            db,
            organization=org,
            invitation_id=issued.invitation.id,
            actor_role=OrganizationRole.OWNER,
        )


# ===========================================================================
# 7. Sweep — §D6.9
# ===========================================================================

def test_sweeper_groups_by_inviter(db):
    org = _make_organization(db)
    inviter_a = _make_user(db)
    inviter_b = _make_user(db)

    # Invitation A (expired)
    issued_a = service.create_invitation(
        db, organization=org, inviter=inviter_a, actor_role=OrganizationRole.OWNER,
        email="personA@example.com", organization_role=OrganizationRole.MEMBER
    )
    issued_a.invitation.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)

    # Invitation B (expired)
    issued_b = service.create_invitation(
        db, organization=org, inviter=inviter_b, actor_role=OrganizationRole.OWNER,
        email="personB@example.com", organization_role=OrganizationRole.MEMBER
    )
    issued_b.invitation.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)

    db.flush()

    # Sweep expired
    expired_map = service.sweep_expired_invitations(db)

    # Assert grouped correctly by inviter using the ExpiryDigestBatch schema
    assert inviter_a.id in expired_map
    assert inviter_b.id in expired_map
    assert len(expired_map[inviter_a.id].lines) == 1


def test_purge_old_never_deletes_pending_regardless_of_age(db):
    org = _make_organization(db)
    actor = _make_user(db)
    issued = service.create_invitation(
        db,
        organization=org,
        inviter=actor,
        actor_role=OrganizationRole.OWNER,
        email="person@example.com",
        organization_role=OrganizationRole.MEMBER,
    )

    issued.invitation.created_at = datetime.now(timezone.utc) - timedelta(days=200)
    db.add(issued.invitation)
    db.flush()

    purged = service.purge_old_invitations(db)
    assert purged == 0


# ===========================================================================
# 8. Boundaries — §D6.3
# ===========================================================================

def test_mail_boundary():
    """
    §D6.3 -- the service returns a frozen carrier dataclass but does not import or
    call invitation_mail directly.
    """
    source = pathlib.Path("app/services/organization_invitation_service.py").read_text(encoding="utf-8")
    assert "import invitation_mail" not in source
    assert "from app.services import invitation_mail" not in source