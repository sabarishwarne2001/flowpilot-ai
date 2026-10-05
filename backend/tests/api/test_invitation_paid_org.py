"""F-051 — accepting an invitation on a paid organization must not fail.

    pytest tests/api/test_invitation_paid_org.py -q

Accepting an invitation records "a seat was added" for billing as the internal
outbox event `billing.seat_added`. The database only admits internal event
names listed in `ck_outbox_events_visibility_vocabulary`, and later migrations
rebuilt that list without the seat events, so the insert was refused and the
whole acceptance rolled back with HTTP 500 — but only for organizations with a
billing account, i.e. every paying customer.

Two tests:

* the journey: a paid organization, a pending invitation, accept -> 200, the
  membership is ACTIVE and exactly one `billing.seat_added` row is written;
* the root cause, for the whole class: every name in the Python vocabulary
  `INTERNAL_EVENT_TYPES` is admitted by the database as INTERNAL, and refused
  as PUBLIC. A future event added to one list and not the other fails here,
  not in a customer's browser.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.automation_events import INTERNAL_EVENT_TYPES
from app.core.security import create_access_token
from app.core.tokens import hash_token
from app.crud.organization_members import create_organization_member
from app.models.billing_account import BillingAccount
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.organization_invitation import InvitationStatus, OrganizationInvitation
from app.models.outbox_event import OutboxEvent
from app.models.user import User


@pytest.fixture
def paid_org(db_session: Session) -> Organization:
    org = Organization(
        slug=f"paid-{uuid.uuid4().hex[:8]}",
        name="Paid Inc.",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    db_session.flush()
    customer = f"cus_test_{uuid.uuid4().hex[:10]}"
    db_session.add(
        BillingAccount(
            organization_id=org.id,
            stripe_customer_id=customer,
            gateway_customer_id=customer,
            currency="USD",
            billing_email=f"billing-{uuid.uuid4().hex[:6]}@paid.example",
        )
    )
    owner = User(email=f"owner-{uuid.uuid4().hex[:8]}@paid.example", hashed_password="x", is_active=True)
    db_session.add(owner)
    db_session.flush()
    create_organization_member(
        db_session, organization_id=org.id, user_id=owner.id, role=OrganizationRole.OWNER
    )
    db_session.commit()
    org.owner_id_for_test = owner.id  # type: ignore[attr-defined]
    return org


def test_accepting_an_invitation_on_a_paid_organization_succeeds(client, db_session: Session, paid_org) -> None:
    email = f"invitee-{uuid.uuid4().hex[:8]}@paid.example"
    invitee = User(
        email=email,
        hashed_password="x",
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    db_session.add(invitee)
    token = "F051-PAID-ORG-TOKEN-" + uuid.uuid4().hex
    db_session.add(
        OrganizationInvitation(
            organization_id=paid_org.id,
            inviter_id=paid_org.owner_id_for_test,
            email=email,
            organization_role=OrganizationRole.MEMBER,
            status=InvitationStatus.PENDING,
            token_hash=hash_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
            send_count=1,
        )
    )
    db_session.commit()

    response = client.post(
        "/api/v1/invitations/accept",
        json={"token": token},
        headers={"Authorization": f"Bearer {create_access_token(subject=str(invitee.id))}"},
    )

    assert response.status_code == 200, response.text
    member = db_session.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == paid_org.id,
            OrganizationMember.user_id == invitee.id,
        )
    ).scalar_one()
    assert member.status == MembershipStatus.ACTIVE
    seat_events = db_session.execute(
        select(OutboxEvent).where(
            OutboxEvent.organization_id == paid_org.id,
            OutboxEvent.event_type == "billing.seat_added",
        )
    ).scalars().all()
    assert len(seat_events) == 1
    assert seat_events[0].visibility == "INTERNAL"


@pytest.mark.parametrize("event_type", sorted(INTERNAL_EVENT_TYPES))
def test_database_admits_every_internal_event_type(db_session: Session, event_type: str) -> None:
    org = Organization(slug=f"vocab-{uuid.uuid4().hex[:8]}", name="Vocab", status=OrganizationStatus.ACTIVE)
    db_session.add(org)
    db_session.flush()

    def _row(visibility: str) -> OutboxEvent:
        return OutboxEvent(
            organization_id=org.id, event_type=event_type, payload={}, visibility=visibility, depth=0
        )

    with db_session.begin_nested():
        db_session.add(_row("INTERNAL"))
        db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(_row("PUBLIC"))
            db_session.flush()
    db_session.rollback()
