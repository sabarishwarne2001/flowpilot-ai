"""
ARCH-04 Step 7 -- endpoint tests.

Tests the HTTP surface using a self-contained test client setup.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.organization import Organization, OrganizationStatus, OrganizationRole
from app.models.workspace import Workspace, WorkspaceRole, WorkspaceStatus
from app.models.user import User
from app.models.organization_invitation import OrganizationInvitation, InvitationStatus, InvitationWorkspaceGrant
from app.core.security import create_access_token


# ===========================================================================
# Local Helper Fixtures (Self-Contained)
# ===========================================================================

@pytest.fixture
def test_org(db_session: Session) -> Organization:
    org = Organization(
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        name="Acme Inc.",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    db_session.flush()
    return org


@pytest.fixture
def seat_full_org(db_session: Session) -> Organization:
    org = Organization(
        slug=f"full-{uuid.uuid4().hex[:8]}",
        name="Full Inc.",
        status=OrganizationStatus.ACTIVE,
        seat_limit=1,
    )
    db_session.add(org)
    db_session.flush()
    return org


@pytest.fixture
def workspace(db_session: Session, test_org: Organization) -> Workspace:
    ws = Workspace(
        organization_id=test_org.id,
        slug=f"ws-{uuid.uuid4().hex[:8]}",
        workspace_name="Operations",
        status=WorkspaceStatus.ACTIVE,
    )
    db_session.add(ws)
    db_session.flush()
    return ws


@pytest.fixture
def foreign_workspace(db_session: Session) -> Workspace:
    org_other = Organization(
        slug=f"other-{uuid.uuid4().hex[:8]}",
        name="Other Inc.",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org_other)
    db_session.flush()

    ws = Workspace(
        organization_id=org_other.id,
        slug=f"foreign-{uuid.uuid4().hex[:8]}",
        workspace_name="Foreign Ops",
        status=WorkspaceStatus.ACTIVE,
    )
    db_session.add(ws)
    db_session.flush()
    return ws


@pytest.fixture
def org_admin_context(db_session: Session, test_org: Organization) -> dict:
    user = User(
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),  # Verified to pass tenant gate
    )
    db_session.add(user)
    db_session.flush()

    from app.crud.organization_members import create_organization_member
    create_organization_member(
        db_session,
        organization_id=test_org.id,
        user_id=user.id,
        role=OrganizationRole.ADMIN,
    )
    db_session.commit()

    token = create_access_token(subject=str(user.id))
    return {
        "auth_headers": {"Authorization": f"Bearer {token}"},
        "organization_id": test_org.id,
        "organization_slug": test_org.slug,
        "user_id": user.id,
    }


@pytest.fixture
def invitee_context(db_session: Session) -> dict:
    user = User(
        email="invitee@example.com",
        hashed_password="x",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.commit()

    token = create_access_token(subject=str(user.id))
    return {
        "auth_headers": {"Authorization": f"Bearer {token}"},
        "user_id": user.id,
        "email": user.email,
    }


@pytest.fixture
def pending_invitation(db_session: Session, test_org: Organization) -> OrganizationInvitation:
    inviter = User(
        email=f"inviter-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    db_session.add(inviter)
    db_session.flush()

    from app.crud.organization_members import create_organization_member
    create_organization_member(
        db_session,
        organization_id=test_org.id,
        user_id=inviter.id,
        role=OrganizationRole.OWNER,
    )

    from app.core.tokens import hash_token
    plaintext = "SECRET-PENDING-TOKEN-123"
    inv = OrganizationInvitation(
        organization_id=test_org.id,
        inviter_id=inviter.id,
        email="invitee@example.com",
        organization_role=OrganizationRole.MEMBER,
        status=InvitationStatus.PENDING,
        token_hash=hash_token(plaintext),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
        send_count=1,
    )
    db_session.add(inv)
    db_session.flush()
    db_session.commit()

    inv.plaintext_token = plaintext
    return inv


# ===========================================================================
# API Tests
# ===========================================================================

class TestAcceptSeatBlocked:
    def test_returns_409_and_leaves_invitation_pending(self, client, db_session, pending_invitation, seat_full_org, invitee_context):
        pending_invitation.organization_id = seat_full_org.id
        db_session.add(pending_invitation)
        
        member = User(email="member-full@example.com", hashed_password="x", is_active=True)
        db_session.add(member)
        db_session.flush()

        from app.crud.organization_members import create_organization_member
        create_organization_member(db_session, organization_id=seat_full_org.id, user_id=member.id)
        db_session.commit()

        response = client.post(
            "/api/v1/invitations/accept",
            json={"token": pending_invitation.plaintext_token},
            headers=invitee_context["auth_headers"],
        )
        assert response.status_code == 409
        
        db_session.refresh(pending_invitation)
        assert pending_invitation.status == InvitationStatus.PENDING


    def test_seat_blocked_acceptance_still_dispatches_notice(
        self, client, db_session, pending_invitation, seat_full_org, invitee_context
    ):
        pending_invitation.organization_id = seat_full_org.id
        db_session.add(pending_invitation)

        member = User(email="member-full@example.com", hashed_password="x", is_active=True)
        db_session.add(member)
        db_session.flush()

        from app.crud.organization_members import create_organization_member
        create_organization_member(db_session, organization_id=seat_full_org.id, user_id=member.id)
        db_session.commit()

        with patch(
            "app.api.v1.organization_invitations.invitation_mail.send_invitation_seat_blocked"
        ) as mock_send:
            response = client.post(
                "/api/v1/invitations/accept",
                json={"token": pending_invitation.plaintext_token},
                headers=invitee_context["auth_headers"],
            )
            assert response.status_code == 409
            mock_send.assert_called_once()
            call_kwargs = mock_send.call_args.kwargs
            assert call_kwargs["invited_email"] == pending_invitation.email
            assert call_kwargs["organization_name"] == seat_full_org.name


class TestAcceptSuccess:
    def test_dispatches_accepted_notice_to_inviter(self, client, invitee_context, pending_invitation):
        with patch(
            "app.api.v1.organization_invitations.invitation_mail.send_invitation_accepted"
        ) as mock_send:
            response = client.post(
                "/api/v1/invitations/accept",
                json={"token": pending_invitation.plaintext_token},
                headers=invitee_context["auth_headers"],
            )
            assert response.status_code == 200
            mock_send.assert_called_once()

    def test_response_carries_organization_slug(self, client, invitee_context, pending_invitation):
        response = client.post(
            "/api/v1/invitations/accept",
            json={"token": pending_invitation.plaintext_token},
            headers=invitee_context["auth_headers"],
        )
        assert response.status_code == 200
        assert response.json()["organization_slug"]


class TestCreateInvitation:
    def test_cross_organization_grant_rejected_with_400(
        self, client, org_admin_context, foreign_workspace
    ):
        response = client.post(
            f"/api/v1/organizations/{org_admin_context['organization_id']}/invitations",
            json={
                "email": "new@example.com",
                "organization_role": "MEMBER",
                "grants": [{"workspace_id": str(foreign_workspace.id), "role": "VIEWER"}],
            },
            headers=org_admin_context["auth_headers"],
        )
        assert response.status_code == 400

    def test_dispatches_invitation_mail_on_success(self, client, org_admin_context, workspace):
        with patch(
            "app.api.v1.organization_invitations.invitation_mail.send_invitation"
        ) as mock_send:
            response = client.post(
                f"/api/v1/organizations/{org_admin_context['organization_id']}/invitations",
                json={"email": "new@example.com", "organization_role": "MEMBER", "grants": []},
                headers=org_admin_context["auth_headers"],
            )
            assert response.status_code == 201
            mock_send.assert_called_once()

    def test_response_never_contains_a_token_field(self, client, org_admin_context):
        response = client.post(
            f"/api/v1/organizations/{org_admin_context['organization_id']}/invitations",
            json={"email": "new2@example.com", "organization_role": "MEMBER", "grants": []},
            headers=org_admin_context["auth_headers"],
        )
        assert "token" not in response.json()
        assert "token_hash" not in response.json()


class TestMembersListSeatCount:
    def test_seats_consumed_includes_pending_invitations(
        self, client, org_admin_context, pending_invitation
    ):
        response = client.get(
            f"/api/v1/organizations/{org_admin_context['organization_id']}/members",
            headers=org_admin_context["auth_headers"],
        )
        body = response.json()
        assert body["seats_consumed"] >= 1


class TestMyInvitations:
    def test_response_carries_no_actionable_token_or_id(self, client, invitee_context, pending_invitation):
        response = client.get("/api/v1/me/invitations", headers=invitee_context["auth_headers"])
        assert response.status_code == 200
        for item in response.json()["items"]:
            assert "id" not in item
            assert "token" not in item


class TestRouteCollisions:
    def test_no_duplicate_route_for_preview_accept_reject(self, client):
        app_instance = client.app
        paths = [
            (r.path, tuple(r.methods))
            for r in app_instance.routes
            if getattr(r, "path", "").startswith("/api/v1/invitations/")
        ]
        assert len(paths) == len(set(paths)), f"Duplicate route registrations: {paths}"
