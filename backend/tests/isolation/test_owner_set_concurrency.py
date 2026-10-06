"""
ARCH-05 Step 1 — owner-set locking. The verification gate for A.2.1.
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

from tests.conftest import TEST_DB_URL

SERVICE_PATH = pathlib.Path("app/services/organization_member_service.py")

LOCKED_CALL_SITES = (
    "change_member_role",
    "deactivate_member",
    "leave_organization",
    "transfer_ownership",
)


@dataclass(frozen=True)
class Tenant:
    organization_id: uuid.UUID
    owner_user_id: uuid.UUID
    member_user_id: uuid.UUID
    owner_membership_id: uuid.UUID
    member_membership_id: uuid.UUID


@pytest.fixture(scope="module")
def concurrency_engine(test_database) -> Generator:
    engine = create_engine(TEST_DB_URL, poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture()
def sessions(concurrency_engine):
    return sessionmaker(bind=concurrency_engine, expire_on_commit=False)


@pytest.fixture()
def tenant(concurrency_engine, sessions) -> Generator[Tenant, None, None]:
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
    cleanup.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_immutable;"))
    cleanup.execute(
        text("DELETE FROM audit_logs WHERE organization_id = :org"),
        {"org": record.organization_id},
    )
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
    cleanup.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER trg_audit_logs_immutable;"))
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


def test_the_database_is_read_committed(sessions):
    db: Session = sessions()
    level = db.execute(text("SHOW transaction_isolation")).scalar_one()
    db.close()
    assert level == "read committed", level


def test_a21_reproduction_leave_cannot_strand_the_organization(
    sessions, tenant
):
    t2: Session = sessions()
    t1: Session = sessions()

    organization_t2 = t2.get(Organization, tenant.organization_id)
    membership_b_t2 = t2.get(OrganizationMember, tenant.member_membership_id)
    assert membership_b_t2.role is OrganizationRole.MEMBER

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


@pytest.mark.parametrize("call_site", LOCKED_CALL_SITES)
def test_every_owner_set_mutation_takes_the_organization_lock(
    concurrency_engine, sessions, tenant, call_site
):
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
    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    observed: dict[str, bool] = {}

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in LOCKED_CALL_SITES:
            first = _first_statement(node)
            observed[node.name] = (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Call)
                and getattr(first.value.func, "id", None)
                == "lock_organization_for_owner_change"
            )

    missing = set(LOCKED_CALL_SITES) - set(observed)
    assert not missing, f"call sites not found in the module: {missing}"
    assert all(observed.values()), observed


# Owner decision N-025 (F-099): the ARCH-05 invariant is about the rows the
# owner set lives on. Later features lock their own rows (work queues, spend
# limits, postings ...) and that is legitimate; what must stay rare, reasoned
# and FK-safe is a lock on an `organizations` or `organization_members` row.
#
# Every such lock is listed here with the reason it exists, and every one must
# be FOR NO KEY UPDATE (`key_share=True`): plain FOR UPDATE on the organization
# row also blocks the KEY SHARE lock every foreign-key check takes, which is
# how F-078 hung a request and locked the organization.
OWNER_SET_TABLES = frozenset({"organizations", "organization_members"})
OWNER_SET_MODELS = frozenset({"Organization", "OrganizationMember"})
ALLOWED_OWNER_SET_LOCKS = {
    "app/services/organization_member_service.py": (
        "ARCH-05: the owner-change helper; every owner-set mutation starts with it"
    ),
    "app/services/billing/account_service.py": (
        "F-082: two concurrent billing-account creations must not both create a customer"
    ),
    "app/services/billing/dodo_reconcile_service.py": (
        "binding a Dodo customer to the organization is serialised per organization"
    ),
    "app/services/revops/contracts.py": (
        "an invoiced contract and a live gateway subscription must not both start"
    ),
}


def _select_target(node: ast.AST, assignments: dict[str, ast.AST]) -> str | None:
    """The model a `select(...)` chain reads from, following `stmt = ...`."""
    seen: set[int] = set()
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        if isinstance(node, ast.Name):
            node = assignments.get(node.id)
            continue
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "select" and node.args:
                first = node.args[0]
                while isinstance(first, ast.Attribute):
                    first = first.value
                return first.id if isinstance(first, ast.Name) else None
            if isinstance(func, ast.Attribute):
                node = func.value
                continue
        return None
    return None


def _owner_set_locks() -> list[tuple[str, int, bool]]:
    """(file, line, is_key_share) for every lock on an owner-set row in app/."""
    found: list[tuple[str, int, bool]] = []
    for path in sorted(pathlib.Path("app").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        rel = str(path).replace("\\", "/")
        tree = ast.parse(source)
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            assignments: dict[str, ast.AST] = {}
            for statement in ast.walk(function):
                if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                    target = statement.targets[0]
                    value = statement.value
                    if isinstance(target, ast.Name) and not (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Attribute)
                        and value.func.attr.startswith("with_for_update")
                    ):
                        assignments.setdefault(target.id, value)
            for call in ast.walk(function):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "with_for_update"
                ):
                    model = _select_target(call.func.value, assignments)
                    assert model is not None, (
                        f"{rel}:{call.lineno}: cannot tell which rows this lock "
                        "takes; select the model directly so the ARCH-05 check can read it"
                    )
                    if model in OWNER_SET_MODELS:
                        key_share = any(
                            kw.arg == "key_share"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is True
                            for kw in call.keywords
                        )
                        found.append((rel, call.lineno, key_share))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                sql = " ".join(node.value.lower().split())
                if " for update" not in sql and " for no key update" not in sql:
                    continue
                if any(f"from {table} " in f"{sql} " for table in OWNER_SET_TABLES):
                    found.append((rel, node.lineno, " for no key update" in sql))
    return found


def test_owner_set_rows_are_locked_only_where_reasoned_and_fk_safe():
    locks = _owner_set_locks()
    unexpected = sorted({f"{rel}:{line}" for rel, line, _ in locks if rel not in ALLOWED_OWNER_SET_LOCKS})
    assert unexpected == [], (
        f"new lock on an organization/member row: {unexpected}. Use "
        "lock_organization_for_owner_change or add the file with its reason"
    )
    blocking = sorted(f"{rel}:{line}" for rel, line, key_share in locks if not key_share)
    assert blocking == [], f"plain FOR UPDATE on an owner-set row blocks FK checks: {blocking}"
    stale = sorted(set(ALLOWED_OWNER_SET_LOCKS) - {rel for rel, _, _ in locks})
    assert stale == [], f"allowed locks that no longer exist (drop them): {stale}"


def test_the_member_service_locks_the_owner_set_only_in_the_helper():
    service = str(SERVICE_PATH).replace("\\", "/")
    in_service = [line for rel, line, _ in _owner_set_locks() if rel == service]
    assert len(in_service) == 1, in_service
    assert SERVICE_PATH.read_text(encoding="utf-8").count("with_for_update(") == 1


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
