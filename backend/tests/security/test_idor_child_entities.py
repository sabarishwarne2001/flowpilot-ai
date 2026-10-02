"""IDOR — tenant A's OWN organization in the URL, tenant B's object id in the URL.

    pytest tests/security/test_idor_child_entities.py -q

The path-level sweep (test_cross_tenant_sweep.py) proves tenant A cannot name
tenant B's organization or workspace. The classic insecure direct object
reference is different: the caller names THEIR OWN organization, which passes
the membership check, and then supplies an object id that belongs to somebody
else (`/workspaces/<mine>/work-items/<theirs>`). Whether that works depends on
each handler scoping its lookup, so it has to be tried per object type.

Objects are created in tenant B two ways, both real:

* through the API, by B's owner: every collection whose POST accepts a
  schema-valid body (workspaces, invitations, conversations, retention holds,
  egress rules, destinations, procurement policies, ...), and
* through the ORM for the sensitive types that need real rows (work items,
  API keys, automation rules, memberships).

Then A's owner calls EVERY route that addresses a child of one of those
collections, with every method, using B's id. Only 401/403/404 pass. Afterwards
B's rows must still exist and be unchanged: a 404 that also deleted the row
would be a worse bug than a leak.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models.api_key import ApiKey
from app.models.automation import AutomationRule
from app.models.organization import OrganizationMember
from app.models.work_item import WorkItem
from tests.conftest import Fixture
from tests.security.plans import put_on_plan
from tests.security.route_sweep import Op, Sweep
from tests.security.test_cross_tenant_sweep import _Crashed, _ids, _org_ws_ops
from tests.security.test_plan_gating_server_side import BODY_OVERRIDES

DENIED = {401, 403, 404}
SCOPE_PARAMS = ("organization_id", "workspace_id")
DESTRUCTIVE = re.compile(r"/(archive|leave|restore)$")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _child_param(op: Op) -> str | None:
    extra = [name for name in op.path_params if name not in SCOPE_PARAMS]
    return extra[0] if extra else None


def _collection_of(op: Op, child: str) -> str:
    return op.path.split("/{" + child + "}", 1)[0]


def _call(client: TestClient, db: Session, sweep: Sweep, op: Op, who: Any, ids: dict[str, str]):
    url, query, body = sweep.build(op, ids)
    body = BODY_OVERRIDES.get(op.key, body)
    try:
        return client.request(
            op.method.upper(), url, params=query or None,
            json=body if body is not None else None, headers=who.headers,
        )
    except Exception as error:  # noqa: BLE001
        return _Crashed(error)
    finally:
        db.rollback()


def _harvest_through_the_api(client, db, sweep, actor, ids) -> dict[str, str]:
    found: dict[str, str] = {}
    for op in _org_ws_ops(sweep):
        if op.method != "post" or DESTRUCTIVE.search(op.path):
            continue
        if any(name not in SCOPE_PARAMS for name in op.path_params):
            continue
        response = _call(client, db, sweep, op, actor, ids)
        if 200 <= response.status_code < 300:
            try:
                body = response.json()
            except Exception:  # noqa: BLE001
                continue
            value = body.get("id") if isinstance(body, dict) else None
            if isinstance(value, str) and UUID_RE.fullmatch(value):
                found[op.path] = value
    return found


@pytest.fixture()
def world(db_session: Session, client: TestClient, tenant: Fixture):
    put_on_plan(db_session, tenant.organization, "enterprise")
    put_on_plan(db_session, tenant.foreign_workspace.organization, "enterprise")
    sweep = Sweep(app)
    ids_a = _ids(tenant.organization.id, tenant.workspace.id)
    ids_b = _ids(tenant.foreign_workspace.organization_id, tenant.foreign_workspace.id)

    victims = _harvest_through_the_api(client, db_session, sweep, tenant.other_org_member, ids_b)

    # Real rows for the sensitive types.
    item = WorkItem(
        workspace_id=tenant.foreign_workspace.id,
        created_by_user_id=tenant.other_org_member.user.id,
        original_filename="beta-secret.pdf",
        stored_filename=f"{tenant.foreign_workspace.organization_id}/{uuid.uuid4()}.pdf",
        file_type="application/pdf",
        file_size=10,
        status="PROCESSED",
        summary="Beta Ltd confidential summary",
        extracted_entities={},
        extraction_metadata={},
    )
    rule = AutomationRule(
        name="beta rule", priority=1, event="WORK_ITEM_COMPLETED", conditions=[], actions=[],
        logic_operator="AND", is_active=True, workspace_id=tenant.foreign_workspace.id,
        created_by_user_id=tenant.other_org_member.user.id,
    )
    db_session.add_all([item, rule])
    db_session.flush()

    from app.services import api_key_service

    membership = db_session.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == tenant.foreign_workspace.organization_id,
            OrganizationMember.user_id == tenant.other_org_member.user.id,
        )
    ).scalar_one()
    key, _token = api_key_service.issue_api_key(
        db_session, organization_id=tenant.foreign_workspace.organization_id, actor=membership,
        name="beta-key", scopes=["organizations:read"], expires_at=None,
    )
    db_session.commit()

    victims.update(
        {
            "/api/v1/workspaces/{workspace_id}/work-items": str(item.id),
            "/api/v1/workspaces/{workspace_id}/automation/rules": str(rule.id),
            "/api/v1/organizations/{organization_id}/api-keys": str(key.id),
            "/api/v1/organizations/{organization_id}/members": str(membership.id),
        }
    )
    return {"sweep": sweep, "ids_a": ids_a, "victims": victims, "item": item, "rule": rule, "key": key,
            "membership": membership}


def _child_ops(sweep: Sweep, victims: dict[str, str]) -> list[tuple[Op, str]]:
    out = []
    for op in _org_ws_ops(sweep):
        child = _child_param(op)
        if child is None or DESTRUCTIVE.search(op.path):
            continue
        collection = _collection_of(op, child)
        if collection in victims:
            out.append((op, child))
    return out


def test_the_world_covers_the_sensitive_object_types(world) -> None:
    """If harvesting silently produced nothing, the sweep below would pass vacuously."""
    victims = world["victims"]
    for must_have in (
        "/api/v1/workspaces/{workspace_id}/work-items",
        "/api/v1/workspaces/{workspace_id}/automation/rules",
        "/api/v1/organizations/{organization_id}/api-keys",
        "/api/v1/organizations/{organization_id}/members",
    ):
        assert must_have in victims
    assert len(victims) >= 12, sorted(victims)
    assert len(_child_ops(world["sweep"], victims)) >= 40


def test_tenant_a_cannot_touch_tenant_b_objects_through_its_own_organization(
    client: TestClient, db_session: Session, tenant: Fixture, world
) -> None:
    sweep, ids_a, victims = world["sweep"], world["ids_a"], world["victims"]
    operations = _child_ops(sweep, victims)

    leaked, crashed = [], []
    for op, child in operations:
        ids = {**ids_a, child: victims[_collection_of(op, child)]}
        response = _call(client, db_session, sweep, op, tenant.owner, ids)
        if response.status_code in DENIED:
            continue
        # 400/409/422 come from validation or state and hand back nothing of B's.
        if response.status_code in (400, 405, 409, 410, 413, 415, 422):
            continue
        (crashed if response.status_code >= 500 else leaked).append(
            f"{op.key.replace('/api/v1', '')} -> {response.status_code}"
        )

    assert not leaked, "tenant A reached tenant B's objects:\n  " + "\n  ".join(leaked)
    assert not crashed, "tenant A crashed a route with tenant B's id:\n  " + "\n  ".join(crashed)


def test_tenant_bs_rows_are_untouched_afterwards(
    client: TestClient, db_session: Session, tenant: Fixture, world
) -> None:
    """A 404 that still deleted or changed the row would be worse than a leak."""
    sweep, ids_a, victims = world["sweep"], world["ids_a"], world["victims"]
    for op, child in _child_ops(sweep, victims):
        ids = {**ids_a, child: victims[_collection_of(op, child)]}
        _call(client, db_session, sweep, op, tenant.owner, ids)

    db_session.expire_all()
    item = db_session.get(WorkItem, world["item"].id)
    assert item is not None and item.summary == "Beta Ltd confidential summary"
    assert db_session.get(AutomationRule, world["rule"].id) is not None
    key = db_session.get(ApiKey, world["key"].id)
    assert key is not None and key.deactivated_at is None
    membership = db_session.get(OrganizationMember, world["membership"].id)
    assert membership is not None and membership.status.value == "ACTIVE"


def test_the_same_requests_succeed_for_the_rightful_owner(
    client: TestClient, db_session: Session, tenant: Fixture, world
) -> None:
    """Control: with tenant B's own organization in the URL the ids resolve, so
    the refusals above are about ownership and not about bad ids."""
    victims = world["victims"]
    ids_b = _ids(tenant.foreign_workspace.organization_id, tenant.foreign_workspace.id)
    sweep = world["sweep"]
    reads = [
        (op, child)
        for op, child in _child_ops(sweep, victims)
        if op.method == "get" and len(op.path_params) == 2
    ]
    assert reads
    resolved = 0
    for op, child in reads:
        ids = {**ids_b, child: victims[_collection_of(op, child)]}
        response = _call(client, db_session, sweep, op, tenant.other_org_member, ids)
        if response.status_code == 200:
            resolved += 1
    assert resolved >= 4, resolved
