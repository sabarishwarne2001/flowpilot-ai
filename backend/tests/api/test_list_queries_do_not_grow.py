"""A list costs the same number of database queries for 3 rows as for 30 (campaign session 4, §B.3).

    pytest tests/api/test_list_queries_do_not_grow.py -q

An "N+1" list runs one more query for each row it shows (each document's uploader, each member's
person, each audit entry's actor), so a page that is quick with a handful of rows slows down for a
customer with thousands. Each busy list here is read with 3 rows and again with 30; the number of
SQL statements the request runs must not grow with the rows.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Callable, Iterator

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.organization import MembershipStatus, OrganizationMember, OrganizationRole
from app.models.user import User
from app.models.work_item import WorkItem


@contextmanager
def _counted(session: Session) -> Iterator[list[int]]:
    engine = session.get_bind()
    count = [0]

    def before(*_args, **_kwargs) -> None:  # noqa: ANN002, ANN003 - SQLAlchemy's hook
        count[0] += 1

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield count
    finally:
        event.remove(engine, "before_cursor_execute", before)


def _documents(db: Session, tenant, n: int) -> None:
    for i in range(n):
        db.add(WorkItem(workspace_id=tenant.workspace.id, created_by_user_id=tenant.contributor.user.id,
                        original_filename=f"doc-{i}.pdf", stored_filename=f"stored-{uuid.uuid4().hex}",
                        file_type="application/pdf", file_size=10))
    db.commit()


def _members(db: Session, tenant, n: int) -> None:
    for _ in range(n):
        user = User(email=f"m-{uuid.uuid4().hex[:10]}@acme.com", hashed_password="!x", is_active=True)
        db.add(user)
        db.flush()
        db.add(OrganizationMember(organization_id=tenant.organization.id, user_id=user.id,
                                  role=OrganizationRole.MEMBER, status=MembershipStatus.ACTIVE))
    db.commit()


def _audit(db: Session, tenant, n: int) -> None:
    from app.services import audit_service

    for _ in range(n):
        audit_service.record(db, organization_id=tenant.organization.id, actor_id=tenant.owner.user.id,
                             resource_type="WORKSPACE", resource_id=tenant.workspace.id, action="UPDATED",
                             details={"field": "name"})
    db.commit()


def _cost(client, db: Session, path: str, headers: dict[str, str]) -> int:
    client.get(path, headers=headers)  # warm caches (plans, entitlements) so only the list is measured
    with _counted(db) as count:
        response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    return count[0]


LISTS: list[tuple[str, Callable, Callable]] = [
    ("documents", _documents, lambda t: f"/api/v1/workspaces/{t.workspace.id}/work-items?limit=100"),
    ("members", _members, lambda t: f"/api/v1/organizations/{t.organization.id}/members"),
    ("audit log", _audit, lambda t: f"/api/v1/organizations/{t.organization.id}/audit-logs?limit=100"),
]


@pytest.mark.parametrize(("name", "seed", "path"), LISTS, ids=[name for name, _, _ in LISTS])
def test_a_list_costs_the_same_queries_for_three_rows_as_for_thirty(
    client, db_session: Session, tenant, name: str, seed: Callable, path: Callable
) -> None:
    headers = tenant.owner.headers
    seed(db_session, tenant, 3)
    few = _cost(client, db_session, path(tenant), headers)
    seed(db_session, tenant, 27)
    many = _cost(client, db_session, path(tenant), headers)
    assert many <= few + 1, f"{name}: {few} queries for 3 more rows, {many} for 30: one query per row"
