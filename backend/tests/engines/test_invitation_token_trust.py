"""F-226 — an invitation sent through the organization's own mail server "proved" an address.

    pytest tests/engines/test_invitation_token_trust.py -q

An invitation token proves that its holder reads the invited mailbox only when
FlowPilot's relay delivered it. An organization with its own SMTP sends its
invitations through that server (HARDENING-T2:D22), so whoever runs it reads
every accept link. With the token alone, that person could create a verified,
signed-in account for any address they invited (the one-step sign-up, F-222),
or accept as an unverified account they registered for it and have the address
marked verified; the preview also told them whether an address had an account.
A single-sign-on login by the real owner would later attach to that account.

Now the send records, before the token leaves, that it went through a server
FlowPilot does not run; such a token opens no one-step sign-up, verifies
nothing, and the preview does not say whether the address has an account.
"""

from __future__ import annotations

import dataclasses
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core import security
from app.models.organization import MembershipStatus, OrganizationMember, OrganizationRole
from app.models.organization_invitation import OrganizationInvitation
from app.models.user import User
from app.services import email_resolution
from app.services import organization_invitation_service as invitations
from app.services.email_service import email_service
from tests.conftest import Fixture

SIGNUP = "/api/v1/auth/register/invitation"
PASSWORD = "harbour lantern quietly 42"


@pytest.fixture()
def organization_relay(monkeypatch, test_sessions):
    """Invitations leave through a relay the organization runs: what its operator sees."""
    seen: list[dict] = []
    resolve = email_resolution.resolve_email_identity

    def through_the_organization(db, **kwargs):
        return dataclasses.replace(resolve(db, **kwargs), transport_layer=email_resolution.LAYER_ORGANIZATION)

    def operator_reads(*, settings, recipient, subject, html_body, text_body, reply_to=None):
        with test_sessions() as db:
            row = db.execute(
                select(OrganizationInvitation).where(OrganizationInvitation.email == recipient.lower())
            ).scalar_one()
            marked = getattr(row, "delivered_off_platform", None)
        seen.append({
            "to": recipient,
            "token": re.search(r"#token=([A-Za-z0-9_\-]+)", text_body).group(1),
            "marked_before_it_left": marked,
        })
        return True, "relayed"

    monkeypatch.setattr(email_resolution, "resolve_email_identity", through_the_organization)
    monkeypatch.setattr(email_service, "send_html_email", operator_reads)
    return seen


def _invite(client, tenant: Fixture, email: str):
    response = client.post(
        f"/api/v1/organizations/{tenant.organization.id}/invitations",
        json={"email": email, "organization_role": "MEMBER",
              "grants": [{"workspace_id": str(tenant.workspace.id), "role": "VIEWER"}]},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 201, response.text


def test_a_link_the_organizations_server_carried_proves_nothing(
    client, db_session, tenant: Fixture, organization_relay
) -> None:
    victim = f"ceo-{uuid.uuid4().hex[:6]}@bigcorp.example"
    _invite(client, tenant, victim)
    [sent] = organization_relay

    # The operator of the organization's server holds the token. With it alone:
    signup = client.post(SIGNUP, json={"token": sent["token"], "password": PASSWORD})
    assert signup.status_code == 409, signup.text
    assert signup.json()["code"] == "INVITATION_SIGNUP_UNAVAILABLE"
    db_session.expire_all()
    assert db_session.execute(select(User).where(User.email == victim)).scalar_one_or_none() is None

    preview = client.post("/api/v1/invitations/preview", json={"token": sent["token"]})
    assert preview.status_code == 200, preview.text
    assert preview.json()["has_account"] is None

    # Recorded before the token left, not after.
    assert sent["marked_before_it_left"] is True


def test_accepting_such_a_link_does_not_verify_the_address(
    client, db_session, tenant: Fixture, organization_relay
) -> None:
    victim = f"cfo-{uuid.uuid4().hex[:6]}@bigcorp.example"
    squatter = User(email=victim, hashed_password=security.get_password_hash(PASSWORD), is_active=True)
    db_session.add(squatter)
    db_session.commit()
    _invite(client, tenant, victim)
    [sent] = organization_relay

    accepted = client.post(
        "/api/v1/invitations/accept", json={"token": sent["token"]},
        headers={"Authorization": f"Bearer {security.create_access_token(subject=str(squatter.id))}"},
    )

    assert accepted.status_code == 200, accepted.text
    db_session.expire_all()
    assert db_session.get(User, squatter.id).email_verified_at is None
    membership = db_session.execute(select(OrganizationMember).where(
        OrganizationMember.organization_id == tenant.organization.id, OrganizationMember.user_id == squatter.id,
    )).scalar_one()
    assert membership.status is MembershipStatus.ACTIVE


def test_a_resent_token_starts_unmarked(db_session, tenant: Fixture) -> None:
    issued = invitations.create_invitation(
        db_session, organization=tenant.organization, inviter=tenant.owner.user,
        actor_role=OrganizationRole.OWNER, email=f"later-{uuid.uuid4().hex[:6]}@example.com",
        organization_role=OrganizationRole.MEMBER,
    )
    issued.invitation.delivered_off_platform = True
    issued.invitation.last_sent_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.flush()

    resent = invitations.resend_invitation(
        db_session, organization=tenant.organization, invitation_id=issued.invitation.id,
        actor_role=OrganizationRole.OWNER,
    )

    # The new token has not been sent anywhere yet; its own send decides.
    assert resent.invitation.delivered_off_platform is False
