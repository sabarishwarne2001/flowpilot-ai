import threading
import uuid

from sqlalchemy import text as sql_text

from app.db.session import SessionLocal
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.user import User
from app.services.identity import deprovision_service
from app.services.identity.errors import LastOwnerProtected


def _make_org_with_two_owners(db):
    suffix = uuid.uuid4().hex[:8]
    org = Organization(slug=f"race-org-{suffix}", name="Race Test Org", status=OrganizationStatus.ACTIVE)
    db.add(org)
    db.flush()

    owner_a = User(email=f"owner-a-{suffix}@example.com", hashed_password="x", is_active=True)
    owner_b = User(email=f"owner-b-{suffix}@example.com", hashed_password="x", is_active=True)
    db.add_all([owner_a, owner_b])
    db.flush()

    db.add_all([
        OrganizationMember(organization_id=org.id, user_id=owner_a.id, role=OrganizationRole.OWNER, status=MembershipStatus.ACTIVE),
        OrganizationMember(organization_id=org.id, user_id=owner_b.id, role=OrganizationRole.OWNER, status=MembershipStatus.ACTIVE),
    ])
    db.commit()
    return org.id, owner_a.id, owner_b.id


def test_concurrent_deprovision_cannot_strand_an_org(engine):
    with SessionLocal() as setup_db:
        org_id, owner_a, owner_b = _make_org_with_two_owners(setup_db)

    barrier = threading.Barrier(2)
    errors = []

    def deprovision(user_id):
        with SessionLocal() as db:
            try:
                barrier.wait(timeout=5)
                deprovision_service.deprovision_member(
                    db, organization_id=org_id, user_id=user_id
                )
            except Exception as exc:
                errors.append(exc)

    threads = [
        threading.Thread(target=deprovision, args=(owner_a,)),
        threading.Thread(target=deprovision, args=(owner_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with SessionLocal() as check_db:
        remaining = check_db.execute(
            sql_text(
                "SELECT count(*) FROM organization_members "
                "WHERE organization_id = :oid AND status='ACTIVE' AND role='OWNER'"
            ),
            {"oid": str(org_id)},
        ).scalar()

    assert remaining >= 1, "both owners were deprovisioned -- the race is open"
    assert any(isinstance(e, LastOwnerProtected) for e in errors), (
        "one of the two concurrent deprovisions should have been refused"
    )
