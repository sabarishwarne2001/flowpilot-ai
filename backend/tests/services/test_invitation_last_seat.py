"""F-221 — the last seat could never be filled by an invitation.

    pytest tests/services/test_invitation_last_seat.py -q

A pending invitation reserves a seat: the seat count is members plus pending
invitations, so invitations cannot walk past the limit. Accepting and resending
then checked "is there a free seat?" against that count, which already includes
the invitation itself. An organization with one seat left could send the
invitation, but the invitee could never accept it ("no seats available") and the
inviter could never resend it. Accepting turns the invitation's own reserved
seat into a member's; resending reserves nothing new.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core import exceptions
from app.crud.organization_members import create_organization_member
from app.models.organization import MembershipStatus, OrganizationRole
from app.models.organization_invitation import InvitationStatus
from app.services import organization_invitation_service as service
from tests.services.test_organization_invitation_service import _make_organization, _make_user


def _org_with_one_free_seat(db: Session):
    organization = _make_organization(db, seat_limit=2)
    owner = _make_user(db)
    create_organization_member(
        db, organization_id=organization.id, user_id=owner.id,
        role=OrganizationRole.OWNER, status=MembershipStatus.ACTIVE,
    )
    issued = service.create_invitation(
        db, organization=organization, inviter=owner, actor_role=OrganizationRole.OWNER,
        email="last-seat@example.com", organization_role=OrganizationRole.MEMBER,
    )
    return organization, owner, issued


def test_the_invitation_for_the_last_seat_can_be_accepted(db_session: Session) -> None:
    organization, _, issued = _org_with_one_free_seat(db_session)
    invitee = _make_user(db_session, email="last-seat@example.com")

    accepted = service.accept_invitation(db_session, token=issued.plaintext_token, actor=invitee)

    assert accepted.organization_id == organization.id
    db_session.refresh(issued.invitation)
    assert issued.invitation.status is InvitationStatus.ACCEPTED
    assert service.count_reserved_seats(db_session, organization_id=organization.id) == 2


def test_the_invitation_for_the_last_seat_can_be_resent(db_session: Session) -> None:
    organization, _, issued = _org_with_one_free_seat(db_session)
    issued.invitation.last_sent_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.flush()

    resent = service.resend_invitation(
        db_session, organization=organization, invitation_id=issued.invitation.id,
        actor_role=OrganizationRole.OWNER,
    )

    assert resent.invitation.id == issued.invitation.id


def test_a_further_invitation_is_still_refused_when_the_seats_are_reserved(db_session: Session) -> None:
    organization, owner, _ = _org_with_one_free_seat(db_session)

    with pytest.raises(exceptions.SeatLimitExceededError, match="no seats available"):
        service.create_invitation(
            db_session, organization=organization, inviter=owner, actor_role=OrganizationRole.OWNER,
            email="one-too-many@example.com", organization_role=OrganizationRole.MEMBER,
        )


def test_acceptance_is_still_refused_when_the_seats_were_filled_meanwhile(db_session: Session) -> None:
    organization, _, issued = _org_with_one_free_seat(db_session)
    added_directly = _make_user(db_session)
    create_organization_member(db_session, organization_id=organization.id, user_id=added_directly.id)
    invitee = _make_user(db_session, email="last-seat@example.com")

    with pytest.raises(exceptions.SeatLimitExceededError, match="no seats available"):
        service.accept_invitation(db_session, token=issued.plaintext_token, actor=invitee)
