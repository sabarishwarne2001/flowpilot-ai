"""Joining from an invitation: a new person signs up in one step, an existing one just accepts.

    pytest tests/api/test_invitation_signup.py -q

F-222. Someone invited who had no FlowPilot account had to leave the invitation,
register on the general sign-up page (typing the invited address themselves),
wait for a verification email, verify, sign in, and find their way back to the
invitation link, whose token lived only in the first tab. The invitation already
proves they control the address: POST /auth/register/invitation takes the token
and a password (the address comes from the invitation and cannot be chosen),
creates the verified account, accepts the invitation and signs them in, in one
transaction. The preview says whether the address already has an account, so
the page offers sign-in to an existing user instead.

Case A, an existing user who belongs to other organizations, gets exactly what
the invitation grants and nothing else changes for them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cookies import REFRESH_COOKIE_NAME
from app.models.organization import MembershipStatus, OrganizationMember, OrganizationRole
from app.models.organization_invitation import InvitationStatus, OrganizationInvitation
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole, WorkspaceStatus
from app.services import organization_invitation_service as invitations
from tests.conftest import Fixture

SIGNUP = "/api/v1/auth/register/invitation"
PASSWORD = "harbour lantern quietly 42"


def _invite(db: Session, tenant: Fixture, email: str, *, role=WorkspaceRole.VIEWER, seat_limit=None):
    if seat_limit is not None:
        tenant.organization.seat_limit = seat_limit
    issued = invitations.create_invitation(
        db, organization=tenant.organization, inviter=tenant.owner.user, actor_role=OrganizationRole.OWNER,
        email=email, organization_role=OrganizationRole.MEMBER, grants=[(tenant.workspace.id, role)],
    )
    db.commit()
    return issued


def _user(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


def _status(db: Session, issued) -> InvitationStatus:
    db.expire_all()
    return db.get(OrganizationInvitation, issued.invitation.id).status


def test_the_preview_says_whether_the_invited_address_has_an_account(client, db_session, tenant: Fixture) -> None:
    new = _invite(db_session, tenant, f"new-{uuid.uuid4().hex[:6]}@example.com")
    existing = _invite(db_session, tenant, tenant.other_org_member.user.email)

    first = client.post("/api/v1/invitations/preview", json={"token": new.plaintext_token})
    second = client.post("/api/v1/invitations/preview", json={"token": existing.plaintext_token})

    assert first.status_code == 200 and first.json()["has_account"] is False, first.text
    assert second.status_code == 200 and second.json()["has_account"] is True, second.text
    assert first.json()["workspaces"] == [{"name": "Engineering", "role": "VIEWER"}]


def test_a_new_person_signs_up_joins_and_is_signed_in(client, db_session, tenant: Fixture) -> None:
    email = f"new-{uuid.uuid4().hex[:6]}@example.com"
    issued = _invite(db_session, tenant, email)

    response = client.post(SIGNUP, json={"token": issued.plaintext_token, "password": PASSWORD})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["organization_slug"] == tenant.organization.slug
    assert body["workspace_slug"] == tenant.workspace.slug
    assert body["access_token"] and body["token_type"] == "bearer"
    assert REFRESH_COOKIE_NAME in response.headers.get("set-cookie", "")

    user = _user(db_session, email)
    assert user is not None and user.email_verified_at is not None
    membership = db_session.execute(select(OrganizationMember).where(
        OrganizationMember.organization_id == tenant.organization.id, OrganizationMember.user_id == user.id,
    )).scalar_one()
    assert (membership.role, membership.status) == (OrganizationRole.MEMBER, MembershipStatus.ACTIVE)
    grant = db_session.execute(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == tenant.workspace.id, WorkspaceMember.user_id == user.id,
    )).scalar_one()
    assert grant.role is WorkspaceRole.VIEWER
    assert _status(db_session, issued) is InvitationStatus.ACCEPTED

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == email
    workspace = client.get(f"/api/v1/workspaces/{tenant.workspace.id}/work-items",
                           headers={"Authorization": f"Bearer {body['access_token']}"})
    assert workspace.status_code == 200, workspace.text


def test_the_address_comes_from_the_invitation_and_cannot_be_chosen(client, db_session, tenant: Fixture) -> None:
    issued = _invite(db_session, tenant, f"new-{uuid.uuid4().hex[:6]}@example.com")

    response = client.post(SIGNUP, json={
        "token": issued.plaintext_token, "password": PASSWORD, "email": "someone-else@example.com",
    })

    assert response.status_code == 422, response.text
    assert _user(db_session, "someone-else@example.com") is None
    assert _status(db_session, issued) is InvitationStatus.PENDING


def test_an_address_that_already_has_an_account_is_sent_to_sign_in(client, db_session, tenant: Fixture) -> None:
    existing = tenant.other_org_member.user
    before = existing.hashed_password
    issued = _invite(db_session, tenant, existing.email)

    response = client.post(SIGNUP, json={"token": issued.plaintext_token, "password": PASSWORD})

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "INVITATION_ACCOUNT_EXISTS"
    db_session.expire_all()
    assert db_session.get(User, existing.id).hashed_password == before
    assert _status(db_session, issued) is InvitationStatus.PENDING


def test_a_weak_password_creates_nothing(client, db_session, tenant: Fixture) -> None:
    email = f"new-{uuid.uuid4().hex[:6]}@example.com"
    issued = _invite(db_session, tenant, email)

    response = client.post(SIGNUP, json={"token": issued.plaintext_token, "password": "aaaaaaaaaaaaaaa"})

    assert response.status_code == 422, response.text
    assert _user(db_session, email) is None
    assert _status(db_session, issued) is InvitationStatus.PENDING


def test_an_expired_or_revoked_invitation_creates_nothing(client, db_session, tenant: Fixture) -> None:
    expired_email = f"late-{uuid.uuid4().hex[:6]}@example.com"
    expired = _invite(db_session, tenant, expired_email)
    expired.invitation.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    revoked_email = f"gone-{uuid.uuid4().hex[:6]}@example.com"
    revoked = _invite(db_session, tenant, revoked_email)
    revoked.invitation.status = InvitationStatus.REVOKED
    db_session.commit()

    late = client.post(SIGNUP, json={"token": expired.plaintext_token, "password": PASSWORD})
    gone = client.post(SIGNUP, json={"token": revoked.plaintext_token, "password": PASSWORD})
    bogus = client.post(SIGNUP, json={"token": "not-a-real-token", "password": PASSWORD})

    assert late.status_code == 400 and late.json()["code"] == "INVITATION_EXPIRED", late.text
    assert gone.status_code == 409 and gone.json()["code"] == "INVITATION_ALREADY_PROCESSED", gone.text
    assert bogus.status_code == 400 and bogus.json()["code"] == "INVALID_INVITATION_TOKEN", bogus.text
    assert _user(db_session, expired_email) is None and _user(db_session, revoked_email) is None


def test_no_free_seat_creates_nothing(client, db_session, tenant: Fixture) -> None:
    email = f"new-{uuid.uuid4().hex[:6]}@example.com"
    issued = _invite(db_session, tenant, email, seat_limit=6)
    # A member added directly takes the seat the invitation reserved.
    filler = User(email=f"filler-{uuid.uuid4().hex[:6]}@example.com", hashed_password="x", is_active=True)
    db_session.add(filler)
    db_session.flush()
    db_session.add(OrganizationMember(organization_id=tenant.organization.id, user_id=filler.id,
                                      role=OrganizationRole.MEMBER, status=MembershipStatus.ACTIVE))
    db_session.commit()

    response = client.post(SIGNUP, json={"token": issued.plaintext_token, "password": PASSWORD})

    assert response.status_code == 409 and response.json()["code"] == "SEAT_LIMIT_EXCEEDED", response.text
    assert _user(db_session, email) is None
    assert _status(db_session, issued) is InvitationStatus.PENDING


def test_an_existing_member_of_another_organization_gets_exactly_what_was_granted(
    client, db_session, tenant: Fixture
) -> None:
    """Case A: the owner of Beta accepts an invitation to Acme as a member with one viewer grant."""
    person = tenant.other_org_member
    finance = Workspace(organization_id=tenant.organization.id, slug=f"finance-{uuid.uuid4().hex[:4]}",
                        workspace_name="Finance", status=WorkspaceStatus.ACTIVE)
    db_session.add(finance)
    db_session.commit()
    issued = _invite(db_session, tenant, person.user.email)

    accepted = client.post("/api/v1/invitations/accept", json={"token": issued.plaintext_token},
                           headers=person.headers)

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["workspace_slug"] == tenant.workspace.slug
    roles = {
        row.organization_id: (row.role, row.status)
        for row in db_session.execute(select(OrganizationMember).where(
            OrganizationMember.user_id == person.user.id)).scalars()
    }
    assert roles[tenant.organization.id] == (OrganizationRole.MEMBER, MembershipStatus.ACTIVE)
    assert roles[tenant.foreign_workspace.organization_id] == (OrganizationRole.OWNER, MembershipStatus.ACTIVE)

    def answer(workspace_id) -> int:
        return client.get(f"/api/v1/workspaces/{workspace_id}/work-items", headers=person.headers).status_code

    assert answer(tenant.workspace.id) == 200
    assert answer(finance.id) in (403, 404)
    assert answer(tenant.foreign_workspace.id) == 200
    write = client.post(f"/api/v1/workspaces/{tenant.workspace.id}/upload-sessions",
                        json={"filename": "x.pdf", "mime_type": "application/pdf"}, headers=person.headers)
    assert write.status_code == 403, write.text
