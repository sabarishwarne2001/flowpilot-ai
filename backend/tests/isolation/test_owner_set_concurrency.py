"""
ARCH-05 Step 1 — owner-set locking. The verification gate for A.2.1.

Four things are asserted here, and they fail for four different reasons:

  1. test_a21_reproduction_*        — the reported interleaving, end to end.
                                      Fails before the fix; passes after.
  2. test_every_owner_set_mutation_* — one parametrisation per call site, so
                                      "the lock was added to three of the four"
                                      (§D R2) is a failing test rather than a
                                      code review someone has to remember to do.
  3. test_the_lock_is_the_first_statement_* — a refactor that keeps the call
                                      but moves it below a role read passes (2)
                                      and fails here.
  4. test_for_update_appears_only_* — a second FOR UPDATE anywhere reintroduces
                                      the lock-ordering question §D R3 is
                                      currently free of.

These do NOT use the `db_session` fixture. That fixture wraps everything in one
outer transaction that is rolled back, so two sessions bound to it would be one
PostgreSQL backend and no lock could ever contend. Every session here is a real
connection against committed rows, and the tenant fixture cleans up explicitly.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generator

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.exceptions import LastOwnerError
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.user import User
from app.services import organization_member_service as member_service

# tests/conftest.py repoints this at the migrated test database at import time.
from tests.conftest import TEST_DB_URL

SERVICE_PATH = pathlib.Path("app/services/organization_member_service.py")

LOCKED_CALL_SITES = (
    "change_member_role",
    "deactivate_member",
    "leave_organization",
    "transfer_ownership",
)


# ===========================================================================
# Fixtures
# ===========================================================================

@dataclass(frozen=True)
class Tenant:
    """Identifiers only. ORM objects are loaded per session, as requests do."""

    organization_id: uuid.UUID
    owner_user_id: uuid.UUID
    member_user_id: uuid.UUID
    owner_membership_id: uuid.UUID
    member_membership_id: uuid.UUID


@pytest.fixture(scope="module")
def concurrency_engine(test_database) -> Generator:
    """
    NullPool so every connect() is a distinct PostgreSQL backend.

    Pooled connections would let two "concurrent" sessions land on the same
    backend, where a row lock is always immediately available and every
    assertion below would pass for the wrong reason.
    """
    engine = create_engine(TEST_DB_URL, poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture()
def sessions(concurrency_engine):
    return sessionmaker(bind=concurrency_engine, expire_on_commit=False)


@pytest.fixture()
def tenant(concurrency_engine, sessions) -> Generator[Tenant, None, None]:
    """
    A committed organization with exactly one active owner, A, and one member,
    B. This is the shape A.2.1's table describes.
    """
    setup: Session = sessions()
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)

    organization = Organization(
        slug=f"race-{suffix}",
        name="Race Ltd.",
        status=OrganizationStatus.ACTIVE,
    )
    setup.add(organization)
    setup.flush()

    owner_user = User(
        email=f"owner-{suffix}@race.test",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        email_verified_at=now,
    )
    member_user = User(
        email=f"member-{suffix}@race.test",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        email_verified_at=now,
    )
    setup.add_all([owner_user, member_user])
    setup.flush()

    owner_membership = OrganizationMember(
        organization_id=organization.id,
        user_id=owner_user.id,
        role=OrganizationRole.OWNER,
        status=MembershipStatus.ACTIVE,
    )
    member_membership = OrganizationMember(
        organization_id=organization.id,
        user_id=member_user.id,
        role=OrganizationRole.MEMBER,
        status=MembershipStatus.ACTIVE,
    )
    setup.add_all([owner_membership, member_membership])
    setup.commit()

    record = Tenant(
        organization_id=organization.id,
        owner_user_id=owner_user.id,
        member_user_id=member_user.id,
        owner_membership_id=owner_membership.id,
        member_membership_id=member_membership.id,
    )
    setup.close()

    yield record

    cleanup: Session = sessions()
    cleanup.execute(
        text("DELETE FROM organization_members WHERE organization_id = :org"),
        {"org": record.organization_id},
    )
    cleanup.execute(
        text("DELETE FROM organizations WHERE id = :org"),
        {"org": record.organization_id},
    )
    cleanup.execute(
        text("DELETE FROM users WHERE id IN (:owner, :member)"),
        {"owner": record.owner_user_id, "member": record.member_user_id},
    )
    cleanup.commit()
    cleanup.close()


def _active_owners(db: Session, organization_id: uuid.UUID) -> int:
    return db.execute(
        select(func.count())
        .select_from(OrganizationMember)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role == OrganizationRole.OWNER,
            OrganizationMember.status == MembershipStatus.ACTIVE,
        )
    ).scalar_one()


# ===========================================================================
# 0. The assumption the whole analysis rests on
# ===========================================================================

def test_the_database_is_read_committed(sessions):
    """
    A.2.1 is a READ COMMITTED phenomenon. If someone raises the isolation
    level globally, the reproduction below stops reproducing and this test
    says why rather than leaving a silently vacuous suite behind.
    """
    db: Session = sessions()
    level = db.execute(text("SHOW transaction_isolation")).scalar_one()
    db.close()
    assert level == "read committed", level


# ===========================================================================
# 1. The reproduction
# ===========================================================================

def test_a21_reproduction_leave_cannot_strand_the_organization(
    sessions, tenant
):
    """
    A.2.1, on two real connections.

    The load-bearing detail is the order of the reads. T2 resolves B's
    membership BEFORE T1 commits, exactly as get_organization_context does
    before any route handler body runs. A lock taken afterwards serializes T2
    correctly and still decides from that stale role unless the membership is
    refreshed under the lock — which is why the helper does both.
    """
    t2: Session = sessions()  # B's request: leave the organization
    t1: Session = sessions()  # A's request: transfer ownership to B

    # --- T2, step 4 of the table: the stale read -----------------------
    organization_t2 = t2.get(Organization, tenant.organization_id)
    membership_b_t2 = t2.get(OrganizationMember, tenant.member_membership_id)
    assert membership_b_t2.role is OrganizationRole.MEMBER

    # --- T1, steps 1-3 and 7: transfer, then commit ---------------------
    member_service.transfer_ownership(
        t1,
        organization=t1.get(Organization, tenant.organization_id),
        current_owner_membership=t1.get(
            OrganizationMember, tenant.owner_membership_id
        ),
        target_membership=t1.get(
            OrganizationMember, tenant.member_membership_id
        ),
    )
    t1.close()

    # --- T2, steps 5, 6 and 8: proceed from the pre-transfer view -------
    with pytest.raises(LastOwnerError):
        member_service.leave_organization(
            t2, organization=organization_t2, membership=membership_b_t2
        )
    t2.rollback()
    t2.close()

    audit: Session = sessions()
    assert _active_owners(audit, tenant.organization_id) >= 1
    audit.close()


def test_a21_reproduction_role_change_cannot_strand_the_organization(
    sessions, tenant
):
    """
    The same shape on change_member_role, which A.2.1's closing paragraph
    names. A demotes B to MEMBER from a view of the world in which A is still
    the owner and B is not.
    """
    t2: Session = sessions()
    t1: Session = sessions()

    organization_t2 = t2.get(Organization, tenant.organization_id)
    membership_a_t2 = t2.get(OrganizationMember, tenant.owner_membership_id)
    membership_b_t2 = t2.get(OrganizationMember, tenant.member_membership_id)
    assert membership_a_t2.role is OrganizationRole.OWNER

    member_service.transfer_ownership(
        t1,
        organization=t1.get(Organization, tenant.organization_id),
        current_owner_membership=t1.get(
            OrganizationMember, tenant.owner_membership_id
        ),
        target_membership=t1.get(
            OrganizationMember, tenant.member_membership_id
        ),
    )
    t1.close()

    # A is an ADMIN now and no longer outranks B, so the refreshed roles must
    # produce a permission refusal rather than a successful demotion of the
    # organization's only owner.
    with pytest.raises(Exception) as excinfo:
        member_service.change_member_role(
            t2,
            organization=organization_t2,
            actor_membership=membership_a_t2,
            target_membership=membership_b_t2,
            new_role=OrganizationRole.MEMBER,
        )
    assert excinfo.type.__name__ in {
        "OrganizationPermissionDeniedError",
        "LastOwnerError",
    }, excinfo.type
    t2.rollback()
    t2.close()

    audit: Session = sessions()
    assert _active_owners(audit, tenant.organization_id) >= 1
    audit.close()


# ===========================================================================
# 2. One assertion per call site — §D R2
# ===========================================================================

@pytest.mark.parametrize("call_site", LOCKED_CALL_SITES)
def test_every_owner_set_mutation_takes_the_organization_lock(
    concurrency_engine, sessions, tenant, call_site
):
    """
    An external connection holds the organizations row. Every owner-set
    mutation must therefore block, and with lock_timeout set it must fail
    rather than proceed.

    A call site that skipped the lock would not block. It would run to
    completion and this test would fail with "DID NOT RAISE" — naming the
    unlocked function in the parametrisation id.
    """
    blocker = concurrency_engine.connect()
    blocking_tx = blocker.begin()
    blocker.execute(
        text("SELECT id FROM organizations WHERE id = :org FOR UPDATE"),
        {"org": tenant.organization_id},
    )

    db: Session = sessions()
    db.execute(text("SET LOCAL lock_timeout = '750ms'"))

    organization = db.get(Organization, tenant.organization_id)
    membership_a = db.get(OrganizationMember, tenant.owner_membership_id)
    membership_b = db.get(OrganizationMember, tenant.member_membership_id)

    invocations = {
        "transfer_ownership": lambda: member_service.transfer_ownership(
            db,
            organization=organization,
            current_owner_membership=membership_a,
            target_membership=membership_b,
        ),
        "change_member_role": lambda: member_service.change_member_role(
            db,
            organization=organization,
            actor_membership=membership_a,
            target_membership=membership_b,
            new_role=OrganizationRole.ADMIN,
        ),
        "deactivate_member": lambda: member_service.deactivate_member(
            db,
            organization=organization,
            actor_membership=membership_a,
            target_membership=membership_b,
        ),
        "leave_organization": lambda: member_service.leave_organization(
            db, organization=organization, membership=membership_b
        ),
    }

    try:
        with pytest.raises(OperationalError) as excinfo:
            invocations[call_site]()
        assert "lock timeout" in str(excinfo.value).lower(), str(excinfo.value)
    finally:
        db.rollback()
        db.close()
        blocking_tx.rollback()
        blocker.close()


# ===========================================================================
# 3. Structural guards — the lock cannot drift out of position
# ===========================================================================

def _first_statement(node: ast.FunctionDef) -> ast.stmt:
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body[0]


def test_the_lock_is_the_first_statement_of_every_owner_set_mutation():
    """
    §C Step 1 says "called as the first statement". Below a role read it is
    decoration: the stale value has already been used.
    """
    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    observed: dict[str, bool] = {}

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in LOCKED_CALL_SITES:
            first = _first_statement(node)
            observed[node.name] = (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Call)
                and getattr(first.value.func, "id", None)
                == "_lock_organization_for_owner_change"
            )

    missing = set(LOCKED_CALL_SITES) - set(observed)
    assert not missing, f"call sites not found in the module: {missing}"
    assert all(observed.values()), observed


def test_for_update_appears_only_in_the_lock_helper():
    """
    §D R3 holds because there is exactly one lock and therefore no ordering to
    get wrong. A second one anywhere in app/ invalidates that reasoning.
    """
    offenders = [
        str(path).replace("\\", "/")
        for path in pathlib.Path("app").rglob("*.py")
        if "with_for_update(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [str(SERVICE_PATH).replace("\\", "/")], offenders

    occurrences = SERVICE_PATH.read_text(encoding="utf-8").count(
        "with_for_update("
    )
    assert occurrences == 1, occurrences


# ===========================================================================
# 4. The happy paths the lock must not have broken
# ===========================================================================

def test_uncontended_transfer_still_succeeds(sessions, tenant):
    db: Session = sessions()
    promoted = member_service.transfer_ownership(
        db,
        organization=db.get(Organization, tenant.organization_id),
        current_owner_membership=db.get(
            OrganizationMember, tenant.owner_membership_id
        ),
        target_membership=db.get(
            OrganizationMember, tenant.member_membership_id
        ),
    )
    assert promoted.role is OrganizationRole.OWNER
    assert _active_owners(db, tenant.organization_id) == 1
    db.close()


def test_a_non_last_owner_can_still_leave(sessions, tenant):
    db: Session = sessions()

    # Promote B alongside A so the organization has two active owners.
    membership_b = db.get(OrganizationMember, tenant.member_membership_id)
    membership_b.role = OrganizationRole.OWNER
    db.commit()

    left = member_service.leave_organization(
        db,
        organization=db.get(Organization, tenant.organization_id),
        membership=db.get(OrganizationMember, tenant.member_membership_id),
    )
    assert left.status is MembershipStatus.DEACTIVATED
    assert _active_owners(db, tenant.organization_id) == 1
    db.close()