"""Creating a billing account must not freeze the rest of the organization.

ensure_billing_account serialises account creation with a lock on the
organization row, and holds it across the Stripe "create customer" call so a
race cannot leave an orphaned Stripe customer. That lock used to be FOR
UPDATE, which also conflicts with the KEY SHARE lock PostgreSQL takes for
every foreign-key check: while Stripe answered, nothing referencing the
organization (a workspace, a document, a job, an audit row) could be
inserted. FOR NO KEY UPDATE keeps the serialisation without the freeze.

Two real sessions against the test database; Stripe is a stand-in that waits
until the test lets it answer.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.models.billing_account import BillingAccount
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.user import User
from app.models.workspace import Workspace
from app.services.billing import account_service, stripe_gateway
from app.services.billing.stripe_gateway import StripeCustomerSnapshot
from tests.conftest import TEST_DB_URL


class _SlowStripe:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def create_customer(self, *, organization_id, email, name=None, currency=None, metadata=None):
        self.calls += 1
        self.entered.set()
        assert self.release.wait(15), "the test never let Stripe answer"
        return StripeCustomerSnapshot(id=f"cus_test_{uuid.uuid4().hex[:10]}", email=email, currency=currency)


@pytest.fixture
def sessions(db_session):
    engine = create_engine(TEST_DB_URL, poolclass=NullPool)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def organization_id(db_session: Session) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:8]
    owner = User(email=f"owner-{suffix}@acme.test", hashed_password="!x", is_active=True,
                 email_verified_at=datetime.now(timezone.utc))
    org = Organization(slug=f"acme-{suffix}", name="Acme", status=OrganizationStatus.ACTIVE)
    db_session.add_all([owner, org])
    db_session.flush()
    db_session.add(OrganizationMember(organization_id=org.id, user_id=owner.id, role=OrganizationRole.OWNER,
                                      status=MembershipStatus.ACTIVE))
    db_session.commit()
    return org.id


@pytest.fixture
def slow_stripe():
    fake = _SlowStripe()
    previous = stripe_gateway.set_gateway(fake)
    yield fake
    fake.release.set()
    stripe_gateway.set_gateway(previous)


def _create(sessions, organization_id: uuid.UUID, errors: list) -> threading.Thread:
    def run() -> None:
        try:
            with sessions() as db:
                account_service.ensure_billing_account(db, organization_id=organization_id)
                db.commit()
        except Exception as exc:  # noqa: BLE001 - reported by the test
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def test_the_organization_keeps_working_while_stripe_answers(sessions, organization_id, slow_stripe) -> None:
    errors: list = []
    creating = _create(sessions, organization_id, errors)
    assert slow_stripe.entered.wait(10), errors

    with sessions() as other:
        other.execute(text("SET lock_timeout = '2s'"))
        other.add(Workspace(organization_id=organization_id, workspace_name="Created meanwhile",
                            slug=f"meanwhile-{uuid.uuid4().hex[:6]}"))
        other.commit()  # used to wait on the organization row until Stripe answered: lock timeout

    slow_stripe.release.set()
    creating.join(15)
    assert not errors, errors
    with sessions() as db:
        assert db.execute(select(BillingAccount).where(BillingAccount.organization_id == organization_id)).scalar_one()


def test_concurrent_creation_still_makes_one_stripe_customer(sessions, organization_id, slow_stripe) -> None:
    errors: list = []
    first = _create(sessions, organization_id, errors)
    assert slow_stripe.entered.wait(10), errors
    second = _create(sessions, organization_id, errors)
    second.join(1.5)
    assert second.is_alive(), "the second creator did not wait for the first"
    slow_stripe.release.set()
    first.join(15)
    second.join(15)
    assert not errors, errors
    assert slow_stripe.calls == 1
    with sessions() as db:
        rows = db.execute(select(BillingAccount).where(BillingAccount.organization_id == organization_id)).all()
    assert len(rows) == 1
