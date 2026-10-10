"""Owner decisions N-034 to N-037 on invitations, and the repeated "no seats" notice (F-230 to F-234).

    pytest tests/api/test_invitation_owner_decisions.py -q

Decided 2026-10-10 under founder authority (NEEDS-OWNER.md):

* N-037 (F-230): an invitation link lasts 7 days.
* N-036 (F-231): accepting an invitation never lowers an existing member's role;
  it only raises it. A deactivated member rejoins with the invitation's role.
* N-034 (F-232): an invitation FlowPilot's relay delivered proves the mailbox, so
  its one-step sign-up takes over an account whose address was never verified
  (new password, verified, every old session ended). A verified account still
  signs in instead.
* N-035 (F-233): an organization that requires single sign-on is joined through
  single sign-on: the preview says so and the password sign-up is refused.
* F-234: while the organization is full, a refused sign-up or acceptance tells
  the inviter once a day per invitation, not on every attempt.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core import security
from app.models.identity import TenantSecurityPolicy
from app.models.organization import MembershipStatus, OrganizationMember, OrganizationRole
from app.models.organization_invitation import InvitationStatus, OrganizationInvitation
from app.models.user import User
from app.models.user_session import UserSession
from app.models.workspace import WorkspaceRole
from app.services import organization_invitation_service as invitations
from tests.conftest import Fixture

SIGNUP = "/api/v1/auth/register/invitation"
PASSWORD = "harbour lantern quietly 42"


def _invite(db, tenant: Fixture, email: str, *, role=OrganizationRole.MEMBER):
    issued = invitations.create_invitation(
        db, organization=tenant.organization, inviter=tenant.owner.user, actor_role=OrganizationRole.OWNER,
        email=email, organization_role=role, grants=[(tenant.workspace.id, WorkspaceRole.VIEWER)],
    )
    db.commit()
    return issued


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {security.create_access_token(subject=str(user.id))}"}


# --------------------------------------------------------------------------- N-037


def test_an_invitation_link_lasts_seven_days(db_session, tenant: Fixture) -> None:
    issued = _invite(db_session, tenant, f"week-{uuid.uuid4().hex[:6]}@example.com")
    left = issued.invitation.expires_at - datetime.now(UTC)
    assert timedelta(days=7) - timedelta(minutes=5) < left <= timedelta(days=7)


# --------------------------------------------------------------------------- N-036


def _member(db, tenant: Fixture, email: str, role: OrganizationRole, status=MembershipStatus.ACTIVE) -> User:
    user = User(email=email, hashed_password=security.get_password_hash(PASSWORD), is_active=True,
                email_verified_at=datetime.now(UTC))
    db.add(user)
    db.flush()
    db.add(OrganizationMember(organization_id=tenant.organization.id, user_id=user.id, role=role, status=status))
    db.commit()
    return user


def _role(db, tenant: Fixture, user: User) -> OrganizationRole:
    db.expire_all()
    return db.execute(select(OrganizationMember.role).where(
        OrganizationMember.organization_id == tenant.organization.id, OrganizationMember.user_id == user.id,
    )).scalar_one()


def test_accepting_never_lowers_an_existing_role(client, db_session, tenant: Fixture) -> None:
    email = f"promoted-{uuid.uuid4().hex[:6]}@example.com"
    issued = _invite(db_session, tenant, email, role=OrganizationRole.MEMBER)
    admin = _member(db_session, tenant, email, OrganizationRole.ADMIN)  # promoted before accepting

    accepted = client.post("/api/v1/invitations/accept", json={"token": issued.plaintext_token},
                           headers=_headers(admin))

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["organization_role"] == "ADMIN"
    assert _role(db_session, tenant, admin) is OrganizationRole.ADMIN


def test_accepting_raises_a_lower_role(client, db_session, tenant: Fixture) -> None:
    email = f"raised-{uuid.uuid4().hex[:6]}@example.com"
    issued = _invite(db_session, tenant, email, role=OrganizationRole.ADMIN)
    member = _member(db_session, tenant, email, OrganizationRole.MEMBER)

    accepted = client.post("/api/v1/invitations/accept", json={"token": issued.plaintext_token},
                           headers=_headers(member))

    assert accepted.status_code == 200, accepted.text
    assert _role(db_session, tenant, member) is OrganizationRole.ADMIN


def test_a_deactivated_member_rejoins_with_the_invitations_role(client, db_session, tenant: Fixture) -> None:
    email = f"rejoin-{uuid.uuid4().hex[:6]}@example.com"
    issued = _invite(db_session, tenant, email, role=OrganizationRole.MEMBER)
    former = _member(db_session, tenant, email, OrganizationRole.ADMIN, status=MembershipStatus.DEACTIVATED)

    accepted = client.post("/api/v1/invitations/accept", json={"token": issued.plaintext_token},
                           headers=_headers(former))

    assert accepted.status_code == 200, accepted.text
    assert _role(db_session, tenant, former) is OrganizationRole.MEMBER


# --------------------------------------------------------------------------- N-034


def test_the_sign_up_takes_over_an_account_whose_address_was_never_verified(
    client, db_session, tenant: Fixture
) -> None:
    email = f"squatted-{uuid.uuid4().hex[:6]}@example.com"
    squatter = User(email=email, hashed_password=security.get_password_hash("someone else's password 9"),
                    is_active=True)
    db_session.add(squatter)
    db_session.flush()
    db_session.add(UserSession(
        user_id=squatter.id, family_id=uuid.uuid4(), token_hash=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    ))
    db_session.commit()
    issued = _invite(db_session, tenant, email)

    preview = client.post("/api/v1/invitations/preview", json={"token": issued.plaintext_token})
    assert preview.json()["has_account"] is False, preview.text

    signup = client.post(SIGNUP, json={"token": issued.plaintext_token, "password": PASSWORD})

    assert signup.status_code == 201, signup.text
    db_session.expire_all()
    user = db_session.get(User, squatter.id)
    assert user.email_verified_at is not None
    assert security.verify_password(PASSWORD, user.hashed_password)
    assert user.sessions_revoked_at is not None
    old = db_session.execute(select(UserSession).where(
        UserSession.user_id == squatter.id, UserSession.revoked_at.is_(None))).scalars().all()
    assert len(old) == 1, "only the new sign-up's session is live"
    assert db_session.get(OrganizationInvitation, issued.invitation.id).status is InvitationStatus.ACCEPTED


def test_a_verified_account_is_still_sent_to_sign_in(client, db_session, tenant: Fixture) -> None:
    issued = _invite(db_session, tenant, tenant.other_org_member.user.email)
    signup = client.post(SIGNUP, json={"token": issued.plaintext_token, "password": PASSWORD})
    assert signup.status_code == 409 and signup.json()["code"] == "INVITATION_ACCOUNT_EXISTS", signup.text


# --------------------------------------------------------------------------- N-035


def test_an_organization_that_requires_sso_is_joined_through_sso(client, db_session, tenant: Fixture) -> None:
    db_session.add(TenantSecurityPolicy(organization_id=tenant.organization.id, require_sso=True))
    db_session.commit()
    email = f"sso-{uuid.uuid4().hex[:6]}@example.com"
    issued = _invite(db_session, tenant, email)

    preview = client.post("/api/v1/invitations/preview", json={"token": issued.plaintext_token})
    assert preview.status_code == 200 and preview.json()["sso_required"] is True, preview.text

    signup = client.post(SIGNUP, json={"token": issued.plaintext_token, "password": PASSWORD})
    assert signup.status_code == 409 and signup.json()["code"] == "INVITATION_SSO_REQUIRED", signup.text
    db_session.expire_all()
    assert db_session.execute(select(User).where(User.email == email)).scalar_one_or_none() is None


def test_an_organization_without_the_requirement_says_so(client, db_session, tenant: Fixture) -> None:
    issued = _invite(db_session, tenant, f"plain-{uuid.uuid4().hex[:6]}@example.com")
    preview = client.post("/api/v1/invitations/preview", json={"token": issued.plaintext_token})
    assert preview.json()["sso_required"] is False


# --------------------------------------------------------------------------- F-234


def test_a_full_organization_tells_the_inviter_once_not_on_every_attempt(
    client, db_session, tenant: Fixture, monkeypatch
) -> None:
    from app.api.v1 import organization_invitations as routes

    sent: list[str] = []
    monkeypatch.setattr(routes.invitation_mail, "send_invitation_seat_blocked",
                        lambda **kwargs: sent.append(kwargs["invited_email"]) or True)
    email = f"full-{uuid.uuid4().hex[:6]}@example.com"
    issued = _invite(db_session, tenant, email)
    tenant.organization.seat_limit = 5  # the five seeded members fill it
    db_session.commit()

    for _ in range(3):
        refused = client.post(SIGNUP, json={"token": issued.plaintext_token, "password": PASSWORD})
        assert refused.status_code == 409 and refused.json()["code"] == "SEAT_LIMIT_EXCEEDED", refused.text

    assert sent == [email]
