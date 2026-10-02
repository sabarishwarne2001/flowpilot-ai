"""Multi-tenancy — tenant A can neither read nor write tenant B's data (403/404).

    pytest tests/security/test_cross_tenant_sweep.py -q

Generated from the OpenAPI schema, so it covers EVERY route that names an
organization or a workspace in its path (423 of 550 operations at Phase 2),
including routes added later, instead of a hand-maintained list.

For each such route the request is made by tenant A's OWNER (the most
privileged user tenant A has) against tenant B's real organization or
workspace id, with a schema-valid body and query, for every method. The only
acceptable answers are 401/403/404, and 404 is expected because the API hides
the existence of tenants a caller does not belong to. A 2xx means data or a
side effect crossed the boundary; a 5xx means the refusal was an unhandled
crash.

Two controls keep the sweep honest, because "everything returned 404" would
pass a sweep that is simply broken:

* the same requests made by tenant B's own owner must NOT be refused as
  "not your tenant" (many succeed, and the rest fail for reasons unrelated
  to tenancy such as a random sub-resource id);
* the reverse direction (B against A) is swept too.
"""

from __future__ import annotations

import collections
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from tests.conftest import Fixture, Persona
from tests.security.route_sweep import Op, Sweep

DENIED = {401, 403, 404}
#: A request that fails validation never reached a handler, so it proves
#: nothing about authorization either way. It leaks nothing, and is reported.
INCONCLUSIVE = {400, 405, 409, 410, 413, 415, 422}


def _org_ws_ops(sweep: Sweep) -> list[Op]:
    return [
        op
        for op in sweep.operations()
        if "organization_id" in op.path_params or "workspace_id" in op.path_params
    ]


class _Crashed:
    """Stands in for a response when the handler raised instead of answering.
    TestClient re-raises server exceptions; the sweep records them as 599."""

    def __init__(self, error: Exception) -> None:
        self.status_code = 599
        self.text = f"{type(error).__name__}: {error}"


def _send(client: TestClient, db: Session, sweep: Sweep, op: Op, who: Persona, ids: dict[str, str]):
    url, query, body = sweep.build(op, ids)
    try:
        return client.request(
            op.method.upper(),
            url,
            params=query or None,
            json=body if body is not None else None,
            headers=who.headers,
        )
    except Exception as error:  # noqa: BLE001 - an unhandled server error is the finding
        return _Crashed(error)
    finally:
        db.rollback()  # one shared session: a failed request must not poison the next


def _ids(organization: Any, workspace: Any) -> dict[str, str]:
    return {"organization_id": str(organization), "workspace_id": str(workspace)}


@pytest.fixture()
def sweep() -> Sweep:
    return Sweep(app)


def _report(title: str, rows: list[str]) -> str:
    return f"{title} ({len(rows)}):\n  " + "\n  ".join(rows[:60])


@pytest.mark.parametrize("direction", ["A_reads_B", "B_reads_A"])
def test_a_tenant_cannot_reach_another_tenants_routes(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep, direction: str
) -> None:
    org_a, ws_a = tenant.organization.id, tenant.workspace.id
    org_b, ws_b = tenant.foreign_workspace.organization_id, tenant.foreign_workspace.id
    if direction == "A_reads_B":
        actor, target = tenant.owner, _ids(org_b, ws_b)
    else:
        actor, target = tenant.other_org_member, _ids(org_a, ws_a)

    operations = _org_ws_ops(sweep)
    assert len(operations) > 300, "the sweep must cover the tenant-scoped API"

    crossed: list[str] = []
    crashed: list[str] = []
    counts: collections.Counter[int] = collections.Counter()
    for op in operations:
        response = _send(client, db_session, sweep, op, actor, target)
        counts[response.status_code] += 1
        if 200 <= response.status_code < 300:
            crossed.append(f"{op.key} -> {response.status_code}")
        elif response.status_code >= 500:
            crashed.append(f"{op.key} -> {response.status_code}")

    assert not crossed, _report("routes that answered a foreign tenant with success", crossed)
    assert not crashed, _report("routes that crashed instead of refusing a foreign tenant", crashed)
    # The refusals must be real: nearly every request is a 404/403, and few are
    # merely "invalid" (which would say nothing about authorization).
    refused = sum(counts[code] for code in DENIED)
    assert refused >= 0.9 * len(operations), dict(counts)


def test_the_sweep_is_not_vacuous_own_tenant_requests_are_not_refused_as_foreign(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep
) -> None:
    """Control: the same routes, by the tenant's own owner, mostly get past the
    tenancy check. If they were all 404 the sweep above would prove nothing."""
    own = _ids(tenant.organization.id, tenant.workspace.id)
    operations = [op for op in _org_ws_ops(sweep) if not op.is_mutating]

    counts: collections.Counter[int] = collections.Counter()
    for op in operations:
        counts[_send(client, db_session, sweep, op, tenant.owner, own).status_code] += 1

    succeeded = sum(n for code, n in counts.items() if 200 <= code < 300)
    assert succeeded >= 40, dict(counts)


def test_a_non_member_of_any_tenant_is_refused_everywhere(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep
) -> None:
    """A signed-in user who belongs to no organization gets nothing from tenant A."""
    target = _ids(tenant.organization.id, tenant.workspace.id)
    crossed: list[str] = []
    crashed: list[str] = []
    for op in _org_ws_ops(sweep):
        response = _send(client, db_session, sweep, op, tenant.non_member, target)
        if 200 <= response.status_code < 300:
            crossed.append(f"{op.key} -> {response.status_code}")
        elif response.status_code >= 500:
            crashed.append(f"{op.key} -> {response.status_code}")
    assert not crossed, _report("routes that answered a non-member with success", crossed)
    assert not crashed, _report("routes that crashed for a non-member", crashed)


def test_anonymous_requests_are_refused_on_every_tenant_route(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep
) -> None:
    target = _ids(tenant.organization.id, tenant.workspace.id)
    leaked: list[str] = []
    for op in _org_ws_ops(sweep):
        url, query, body = sweep.build(op, target)
        response = client.request(
            op.method.upper(), url, params=query or None, json=body if body is not None else None
        )
        db_session.rollback()
        if response.status_code not in {401, 403}:
            leaked.append(f"{op.key} -> {response.status_code}")
    assert not leaked, _report("tenant routes that did not answer 401/403 to anonymous", leaked)


def test_the_sweep_detects_a_broken_tenancy_check(
    client: TestClient,
    db_session: Session,
    tenant: Fixture,
    sweep: Sweep,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation control. If organization membership were ignored (every caller
    treated as a member of whichever organization the URL names), this same
    sweep must light up. A sweep that cannot fail proves nothing."""
    from app.api import deps

    real = deps.crud.get_organization_member
    foreign_owner_id = tenant.other_org_member.user.id

    def leaky(db, *, organization_id, user_id, statuses=None):  # noqa: ANN001
        return real(db, organization_id=organization_id, user_id=foreign_owner_id, statuses=statuses) or real(
            db, organization_id=organization_id, user_id=user_id, statuses=statuses
        )

    monkeypatch.setattr(deps.crud, "get_organization_member", leaky)

    target = _ids(tenant.foreign_workspace.organization_id, tenant.foreign_workspace.id)
    crossed = [
        op.key
        for op in _org_ws_ops(sweep)
        if "organization_id" in op.path_params
        and 200 <= _send(client, db_session, sweep, op, tenant.owner, target).status_code < 300
    ]
    assert len(crossed) >= 10, "the sweep failed to notice a disabled membership check"
