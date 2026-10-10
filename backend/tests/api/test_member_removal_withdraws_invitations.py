"""F-217 — removing a member left their pending invitation alive.

    pytest tests/api/test_member_removal_withdraws_invitations.py -q

A person can hold an ACTIVE membership and still have a PENDING invitation when
they joined without using it (single sign-on just-in-time, SCIM, or a second
invitation sent before they accepted the first). Removing them (Members page,
leaving, or directory deprovisioning) left that invitation pending: it kept a
seat reserved, and its link let the removed person straight back in.

Removal now withdraws every invitation still pending for their address, with an
audit record for each; accepted invitations stay as history.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tokens import hash_token
from app.crud.organization_members import create_organization_member
from app.models.audit_log import AuditLog
from app.models.organization import MembershipStatus, OrganizationMember, OrganizationRole
from app.models.organization_invitation import InvitationStatus, OrganizationInvitation
from app.models.user import User
from app.core.security import create_access_token
from tests.conftest import Fixture


def _invitation(
    db: Session, tenant: Fixture, email: str, status: InvitationStatus, token: str | None = None
) -> OrganizationInvitation:
    inv = OrganizationInvitation(
        organization_id=tenant.organization.id,
        inviter_id=tenant.owner.user.id,
        email=email,
        organization_role=OrganizationRole.MEMBER,
        status=status,
        token_hash=hash_token(token or uuid.uuid4().hex),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
        send_count=1,
    )
    if status is InvitationStatus.ACCEPTED:
        inv.accepted_at = datetime.now(timezone.utc)
    db.add(inv)
    db.flush()
    return inv


def _member(db: Session, tenant: Fixture, prefix: str) -> tuple[User, OrganizationMember]:
    person = User(
        email=f"{prefix}-{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="x",
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(person)
    db.flush()
    membership = create_organization_member(
        db, organization_id=tenant.organization.id, user_id=person.id, role=OrganizationRole.MEMBER
    )
    return person, membership


def _url(tenant: Fixture) -> str:
    return f"/api/v1/organizations/{tenant.organization.id}/invitations"


def test_removing_a_member_withdraws_invitations_still_pending_for_their_address(
    client, db_session: Session, tenant: Fixture
) -> None:
    person, membership = _member(db_session, tenant, "leaver")
    accepted = _invitation(db_session, tenant, person.email, InvitationStatus.ACCEPTED)
    # A second invitation (say, to add a workspace) was sent before the removal.
    still_pending = _invitation(db_session, tenant, person.email, InvitationStatus.PENDING)
    other = _invitation(db_session, tenant, "someone-else@example.com", InvitationStatus.PENDING)
    db_session.commit()

    response = client.post(
        f"/api/v1/organizations/{tenant.organization.id}/members/{membership.id}/deactivate",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    assert db_session.get(OrganizationMember, membership.id).status is MembershipStatus.DEACTIVATED
    assert db_session.get(OrganizationInvitation, accepted.id).status is InvitationStatus.ACCEPTED
    withdrawn = db_session.get(OrganizationInvitation, still_pending.id)
    assert withdrawn.status is InvitationStatus.REVOKED
    assert withdrawn.revoked_by_id == tenant.owner.user.id
    assert db_session.get(OrganizationInvitation, other.id).status is InvitationStatus.PENDING

    listed = client.get(_url(tenant), params={"status": "PENDING"}, headers=tenant.owner.headers).json()
    assert person.email not in [row["email"] for row in listed["items"]]

    revoked = db_session.execute(
        select(AuditLog).where(AuditLog.resource_id == still_pending.id)
    ).scalars().all()
    assert [str(getattr(r.action, "value", r.action)) for r in revoked] == ["REVOKED"]
    assert (revoked[0].details or {}).get("reason") == "MEMBER_REMOVED"


def test_a_removed_member_cannot_rejoin_with_a_link_sent_before_the_removal(
    client, db_session: Session, tenant: Fixture
) -> None:
    person, membership = _member(db_session, tenant, "rejoiner")
    _invitation(db_session, tenant, person.email, InvitationStatus.PENDING, token="LINK-SENT-EARLIER")
    db_session.commit()

    removed = client.post(
        f"/api/v1/organizations/{tenant.organization.id}/members/{membership.id}/deactivate",
        headers=tenant.owner.headers,
    )
    assert removed.status_code == 200, removed.text

    accepted = client.post(
        "/api/v1/invitations/accept",
        json={"token": "LINK-SENT-EARLIER"},
        headers={"Authorization": f"Bearer {create_access_token(subject=str(person.id))}"},
    )
    assert accepted.status_code == 409, accepted.text
    db_session.expire_all()
    assert db_session.get(OrganizationMember, membership.id).status is MembershipStatus.DEACTIVATED


def test_directory_deprovisioning_also_withdraws_pending_invitations(
    db_session: Session, tenant: Fixture
) -> None:
    from app.services.identity.deprovision_service import deprovision_member

    person, _ = _member(db_session, tenant, "scim-leaver")
    pending = _invitation(db_session, tenant, person.email.upper(), InvitationStatus.PENDING)
    db_session.commit()

    deprovision_member(db_session, organization_id=tenant.organization.id, user_id=person.id)

    db_session.expire_all()
    assert db_session.get(OrganizationInvitation, pending.id).status is InvitationStatus.REVOKED
