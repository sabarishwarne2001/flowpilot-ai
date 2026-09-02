"""
ARCH-05 Step 6 -- ownership transfer concurrency gate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core import exceptions
from app.core.security import get_password_hash
from app.crud.organization_members import create_organization_member
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.ownership_transfer import OwnershipTransfer, OwnershipTransferStatus
from app.models.user import User
from app.services import ownership_transfer_service as service

from tests.conftest import TEST_DB_URL

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(scope="module")
def engine_(test_database) -> Generator:
    engine = create_engine(TEST_DB_URL, poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture()
def sessions(engine_):
    return sessionmaker(bind=engine_, expire_on_commit=False)


@pytest.fixture()
def scene(sessions):
    setup: Session = sessions()
    suffix = uuid.uuid4().hex[:8]

    org = Organization(
        slug=f"race-transfer-{suffix}", name="Race Ltd", status=OrganizationStatus.ACTIVE
    )
    setup.add(org)
    setup.flush()

    owner = User(
        email=f"owner-{suffix}@race.test",
        hashed_password=get_password_hash(PASSWORD),
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    target_a = User(
        email=f"target-a-{suffix}@race.test",
        hashed_password=get_password_hash(PASSWORD),
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    target_b = User(
        email=f"target-b-{suffix}@race.test",
        hashed_password=get_password_hash(PASSWORD),
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    setup.add_all([owner, target_a, target_b])
    setup.flush()

    owner_m = create_organization_member(
        setup, organization_id=org.id, user_id=owner.id, role=OrganizationRole.OWNER
    )
    target_a_m = create_organization_member(
        setup, organization_id=org.id, user_id=target_a.id
    )
    target_b_m = create_organization_member(
        setup, organization_id=org.id, user_id=target_b.id
    )
    setup.commit()

    ids = {
        "org_id": org.id, "owner_id": owner.id,
        "target_a_id": target_a.id, "target_b_id": target_b.id,
        "owner_m_id": owner_m.id, "target_a_m_id": target_a_m.id,
        "target_b_m_id": target_b_m.id,
    }
    setup.close()
    yield ids

    cleanup: Session = sessions()
    cleanup.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_immutable;"))
    cleanup.execute(text("DELETE FROM audit_logs WHERE organization_id = :o"), {"o": ids["org_id"]})
    cleanup.execute(text("DELETE FROM ownership_transfers WHERE organization_id = :o"), {"o": ids["org_id"]})
    cleanup.execute(text("DELETE FROM organization_members WHERE organization_id = :o"), {"o": ids["org_id"]})
    cleanup.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": ids["org_id"]})
    cleanup.execute(
        text("DELETE FROM users WHERE id IN (:a, :b, :c)"),
        {"a": ids["owner_id"], "b": ids["target_a_id"], "c": ids["target_b_id"]},
    )
    cleanup.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER trg_audit_logs_immutable;"))
    cleanup.commit()
    cleanup.close()


def test_two_concurrent_initiations_only_one_succeeds(sessions, scene):
    db_a: Session = sessions()
    db_b: Session = sessions()

    org_a = db_a.get(Organization, scene["org_id"])
    owner_a = db_a.get(User, scene["owner_id"])
    owner_m_a = db_a.get(OrganizationMember, scene["owner_m_id"])

    org_b = db_b.get(Organization, scene["org_id"])
    owner_b = db_b.get(User, scene["owner_id"])
    owner_m_b = db_b.get(OrganizationMember, scene["owner_m_id"])

    results = {"a": None, "b": None}

    try:
        results["a"] = service.initiate_transfer(
            db_a, organization=org_a, actor=owner_a, initiator_membership=owner_m_a,
            target_membership_id=scene["target_a_m_id"], current_password=PASSWORD,
        )
    except exceptions.PendingTransferExistsError:
        results["a"] = "rejected"

    try:
        results["b"] = service.initiate_transfer(
            db_b, organization=org_b, actor=owner_b, initiator_membership=owner_m_b,
            target_membership_id=scene["target_b_m_id"], current_password=PASSWORD,
        )
    except exceptions.PendingTransferExistsError:
        results["b"] = "rejected"

    db_a.close()
    db_b.close()

    outcomes = [results["a"], results["b"]]
    succeeded = [r for r in outcomes if r != "rejected"]
    rejected = [r for r in outcomes if r == "rejected"]

    assert len(succeeded) == 1
    assert len(rejected) == 1

    audit: Session = sessions()
    count = audit.execute(
        text("SELECT count(*) FROM ownership_transfers WHERE organization_id = :o AND status = 'PENDING'"),
        {"o": scene["org_id"]},
    ).scalar_one()
    assert count == 1
    audit.close()


def test_two_concurrent_accepts_of_the_same_transfer_only_one_succeeds(sessions, scene):
    setup: Session = sessions()
    org = setup.get(Organization, scene["org_id"])
    owner = setup.get(User, scene["owner_id"])
    owner_m = setup.get(OrganizationMember, scene["owner_m_id"])

    initiated = service.initiate_transfer(
        setup, organization=org, actor=owner, initiator_membership=owner_m,
        target_membership_id=scene["target_a_m_id"], current_password=PASSWORD,
    )
    setup.close()

    db_a: Session = sessions()
    db_b: Session = sessions()
    org_a = db_a.get(Organization, scene["org_id"])
    org_b = db_b.get(Organization, scene["org_id"])
    target_a_db_a = db_a.get(User, scene["target_a_id"])
    target_a_db_b = db_b.get(User, scene["target_a_id"])

    results = {"a": None, "b": None}
    try:
        results["a"] = service.accept_transfer(
            db_a, organization=org_a, transfer_id=initiated.transfer_id, actor=target_a_db_a,
        )
    except exceptions.TransferNotPendingError:
        results["a"] = "rejected"

    try:
        results["b"] = service.accept_transfer(
            db_b, organization=org_b, transfer_id=initiated.transfer_id, actor=target_a_db_b,
        )
    except exceptions.TransferNotPendingError:
        results["b"] = "rejected"

    db_a.close()
    db_b.close()

    outcomes = [results["a"], results["b"]]
    succeeded = [r for r in outcomes if r != "rejected"]
    assert len(succeeded) == 1

    audit: Session = sessions()
    row = audit.get(OwnershipTransfer, initiated.transfer_id)
    assert row.status is OwnershipTransferStatus.ACCEPTED

    active_owners = audit.execute(
        text(
            "SELECT count(*) FROM organization_members "
            "WHERE organization_id = :o AND role = 'OWNER' AND status = 'ACTIVE'"
        ),
        {"o": scene["org_id"]},
    ).scalar_one()
    assert active_owners == 1
    audit.close()
