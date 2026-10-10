"""F-216 — "Pending invitations" listed invitations that were no longer pending.

    pytest tests/api/test_pending_invitations_are_only_pending.py -q

Reported live: a person accepted an invitation and was later removed in
Organization -> Members, and Workspace Settings still listed their address under
"Pending invitations" with Resend and Revoke, both of which failed with "This
invitation was already accepted." GET /organizations/{id}/invitations returned
every invitation (accepted, revoked, expired), and the workspace page rendered
them all as pending.

The list now takes `?status=` (the clients ask for PENDING); without it the full
history is still listed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.core.tokens import hash_token
from app.models.organization import OrganizationRole
from app.models.organization_invitation import InvitationStatus, OrganizationInvitation
from tests.conftest import Fixture


def _invitation(db: Session, tenant: Fixture, email: str, status: InvitationStatus) -> OrganizationInvitation:
    inv = OrganizationInvitation(
        organization_id=tenant.organization.id,
        inviter_id=tenant.owner.user.id,
        email=email,
        organization_role=OrganizationRole.MEMBER,
        status=status,
        token_hash=hash_token(uuid.uuid4().hex),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
        send_count=1,
    )
    if status is InvitationStatus.ACCEPTED:
        inv.accepted_at = datetime.now(timezone.utc)
    if status is InvitationStatus.REVOKED:
        inv.revoked_at = datetime.now(timezone.utc)
    db.add(inv)
    db.flush()
    return inv


def _url(tenant: Fixture) -> str:
    return f"/api/v1/organizations/{tenant.organization.id}/invitations"


def test_the_pending_filter_returns_only_pending_invitations(
    client, db_session: Session, tenant: Fixture
) -> None:
    pending = _invitation(db_session, tenant, "pending@example.com", InvitationStatus.PENDING)
    _invitation(db_session, tenant, "accepted@example.com", InvitationStatus.ACCEPTED)
    _invitation(db_session, tenant, "revoked@example.com", InvitationStatus.REVOKED)
    db_session.commit()

    response = client.get(_url(tenant), params={"status": "PENDING"}, headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["email"] for row in body["items"]] == [pending.email]
    assert body["total"] == 1


def test_without_a_filter_the_full_history_is_still_listed(
    client, db_session: Session, tenant: Fixture
) -> None:
    for email, status in (
        ("a@example.com", InvitationStatus.PENDING),
        ("b@example.com", InvitationStatus.ACCEPTED),
        ("c@example.com", InvitationStatus.REVOKED),
    ):
        _invitation(db_session, tenant, email, status)
    db_session.commit()

    response = client.get(_url(tenant), headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    assert {row["status"] for row in response.json()["items"]} == {"PENDING", "ACCEPTED", "REVOKED"}


def test_an_unknown_status_is_refused(client, db_session: Session, tenant: Fixture) -> None:
    response = client.get(_url(tenant), params={"status": "BOGUS"}, headers=tenant.owner.headers)
    assert response.status_code == 422, response.text
