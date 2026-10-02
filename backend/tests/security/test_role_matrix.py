"""Role matrix — a role below the one a route requires is refused.

    pytest tests/security/test_role_matrix.py -q

The role a route needs is DISCOVERED from its dependency tree (`RequireOrgRole`
and `RequireWorkspaceRole` instances), then every persona below that role calls
it with a schema-valid body. Only 401/403/404 pass (a 422 means the body was
rejected before authorization could be observed and is only counted).

Personas (tenant A): organization OWNER, ADMIN, and three organization MEMBERS
whose workspace roles are ADMIN, CONTRIBUTOR and VIEWER. Mutating and reading
routes are both swept, because a viewer reading an owner-only console is a leak
too.
"""

from __future__ import annotations

import collections
from typing import Any, Optional

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import deps
from app.core.workspace_permissions import is_at_least
from app.main import app
from app.models.organization import OrganizationRole
from app.models.workspace import WorkspaceRole
from tests.conftest import Fixture
from tests.security.plans import put_on_plan
from tests.security.route_sweep import Op, Sweep
from tests.security.test_cross_tenant_sweep import _Crashed, _ids
from tests.security.test_plan_gating_server_side import BODY_OVERRIDES

DENIED = {401, 403, 404}
DESTRUCTIVE_SUFFIXES = ("/archive", "/leave", "/restore")

#: Organization roles by rank, lowest first.
ORG_RANK = {OrganizationRole.MEMBER: 0, OrganizationRole.ADMIN: 1, OrganizationRole.OWNER: 2}


def _walk(dependant: Any):
    yield dependant
    for child in getattr(dependant, "dependencies", ()):
        yield from _walk(child)


def _requirements() -> dict[str, tuple[Optional[frozenset], Optional[WorkspaceRole]]]:
    """`METHOD path` -> (allowed organization roles, minimum workspace role)."""
    found: dict[str, tuple[Optional[frozenset], Optional[WorkspaceRole]]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        org_roles: Optional[frozenset] = None
        ws_role: Optional[WorkspaceRole] = None
        for node in _walk(route.dependant):
            call = getattr(node, "call", None)
            if isinstance(call, deps.RequireOrgRole):
                org_roles = call.allowed_roles if org_roles is None else org_roles & call.allowed_roles
            elif isinstance(call, deps.RequireWorkspaceRole):
                ws_role = call.minimum_role if ws_role is None else max(ws_role, call.minimum_role, key=lambda r: list(WorkspaceRole).index(r))
        if org_roles is not None or ws_role is not None:
            for method in route.methods or ():
                if method not in {"HEAD", "OPTIONS"}:
                    found[f"{method} {route.path}"] = (org_roles, ws_role)
    return found


@pytest.fixture()
def sweep() -> Sweep:
    return Sweep(app)


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


def test_the_requirements_are_discovered(sweep: Sweep) -> None:
    requirements = _requirements()
    org_bound = [k for k, (o, _) in requirements.items() if o]
    ws_bound = [k for k, (_, w) in requirements.items() if w]
    assert len(org_bound) > 100, len(org_bound)
    assert len(ws_bound) > 200, len(ws_bound)


def test_an_organization_member_cannot_use_admin_or_owner_routes(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep
) -> None:
    """A plain organization MEMBER (three personas, differing only in workspace
    role) is refused on every route that needs organization ADMIN or OWNER."""
    put_on_plan(db_session, tenant.organization, "enterprise")
    ids = _ids(tenant.organization.id, tenant.workspace.id)
    requirements = _requirements()
    members = {"ws_admin": tenant.ws_admin, "contributor": tenant.contributor, "viewer": tenant.viewer}

    escaped: list[str] = []
    counts: collections.Counter[int] = collections.Counter()
    probed = 0
    for op in sweep.operations():
        org_roles, _ = requirements.get(op.key, (None, None))
        if not org_roles or OrganizationRole.MEMBER in org_roles:
            continue
        if op.path.endswith(DESTRUCTIVE_SUFFIXES) or "organization_id" not in op.path_params:
            continue
        for name, persona in members.items():
            response = _call(client, db_session, sweep, op, persona, ids)
            counts[response.status_code] += 1
            probed += 1
            if response.status_code in DENIED or response.status_code == 422:
                continue
            escaped.append(f"{name}: {op.key.replace('/api/v1', '')} -> {response.status_code}")
    assert probed > 150, probed
    assert not escaped, "a MEMBER used an admin/owner route:\n  " + "\n  ".join(escaped)
    assert counts[403] + counts[404] >= 0.85 * probed, dict(counts)


def test_an_organization_admin_cannot_use_owner_only_routes(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep
) -> None:
    put_on_plan(db_session, tenant.organization, "enterprise")
    ids = _ids(tenant.organization.id, tenant.workspace.id)
    requirements = _requirements()
    escaped, probed = [], 0
    for op in sweep.operations():
        org_roles, _ = requirements.get(op.key, (None, None))
        if org_roles != frozenset({OrganizationRole.OWNER}) or op.path.endswith(DESTRUCTIVE_SUFFIXES):
            continue
        response = _call(client, db_session, sweep, op, tenant.org_admin, ids)
        probed += 1
        if response.status_code not in DENIED and response.status_code != 422:
            escaped.append(f"{op.key.replace('/api/v1', '')} -> {response.status_code}")
    assert probed >= 30, probed
    assert not escaped, "an ADMIN used an OWNER-only route:\n  " + "\n  ".join(escaped)


def test_a_workspace_viewer_cannot_use_contributor_or_admin_routes(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep
) -> None:
    put_on_plan(db_session, tenant.organization, "enterprise")
    ids = _ids(tenant.organization.id, tenant.workspace.id)
    requirements = _requirements()
    escaped, probed = [], 0
    for op in sweep.operations():
        _, ws_role = requirements.get(op.key, (None, None))
        if ws_role is None or ws_role == WorkspaceRole.VIEWER or "workspace_id" not in op.path_params:
            continue
        if op.path.endswith(DESTRUCTIVE_SUFFIXES):
            continue
        response = _call(client, db_session, sweep, op, tenant.viewer, ids)
        probed += 1
        if response.status_code not in DENIED and response.status_code != 422:
            escaped.append(f"viewer: {op.key.replace('/api/v1', '')} (needs {ws_role.value}) -> {response.status_code}")
    assert probed > 100, probed
    assert not escaped, "a VIEWER used a route above their role:\n  " + "\n  ".join(escaped)


def test_a_workspace_contributor_cannot_use_workspace_admin_routes(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep
) -> None:
    put_on_plan(db_session, tenant.organization, "enterprise")
    ids = _ids(tenant.organization.id, tenant.workspace.id)
    requirements = _requirements()
    escaped, probed = [], 0
    for op in sweep.operations():
        _, ws_role = requirements.get(op.key, (None, None))
        if ws_role != WorkspaceRole.ADMIN or "workspace_id" not in op.path_params or op.path.endswith(DESTRUCTIVE_SUFFIXES):
            continue
        response = _call(client, db_session, sweep, op, tenant.contributor, ids)
        probed += 1
        if response.status_code not in DENIED and response.status_code != 422:
            escaped.append(f"contributor: {op.key.replace('/api/v1', '')} -> {response.status_code}")
    assert probed >= 30, probed
    assert not escaped, "a CONTRIBUTOR used a workspace-admin route:\n  " + "\n  ".join(escaped)


def test_the_owner_is_not_refused_by_the_role_layer(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep
) -> None:
    """Control: the OWNER passes the role checks these tests exercise, so a 403
    for everyone would not pass the sweeps above."""
    put_on_plan(db_session, tenant.organization, "enterprise")
    ids = _ids(tenant.organization.id, tenant.workspace.id)
    requirements = _requirements()
    passed = 0
    for op in sweep.operations():
        org_roles, ws_role = requirements.get(op.key, (None, None))
        if op.method != "get" or not (org_roles or ws_role):
            continue
        if _call(client, db_session, sweep, op, tenant.owner, ids).status_code == 200:
            passed += 1
    assert passed >= 30, passed
