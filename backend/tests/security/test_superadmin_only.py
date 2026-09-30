"""Super-admin routes reject every tenant user, including a tenant's OWNER.

    pytest tests/security/test_superadmin_only.py -q

The Platform section of the console (Unit economics, Sovereign edition, Revenue
operations) and the partner-programme back office are for FlowPilot staff. They
read and change data across every tenant: supplier costs, price books,
contracts, invoices, payouts. A tenant who could reach them could read another
tenant's margins or void an invoice.

The set of super-admin routes is DISCOVERED from the dependency tree (any route
that depends on `require_superadmin`), so a new platform route is covered the
day it is added and cannot be forgotten in a hand-kept list.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import deps
from app.main import app
from tests.conftest import Fixture, Persona, _make_user
from tests.security.route_sweep import Op, Sweep
from tests.security.test_cross_tenant_sweep import _Crashed

DENIED = {401, 403, 404}


def _depends_on_superadmin(dependant: Any) -> bool:
    if dependant is None:
        return False
    if getattr(dependant, "call", None) is deps.require_superadmin:
        return True
    return any(_depends_on_superadmin(child) for child in getattr(dependant, "dependencies", ()))


def _superadmin_keys() -> set[str]:
    keys: set[str] = set()
    for route in app.routes:
        if isinstance(route, APIRoute) and _depends_on_superadmin(route.dependant):
            for method in route.methods or ():
                if method not in {"HEAD", "OPTIONS"}:
                    keys.add(f"{method} {route.path}")
    return keys


def _superadmin_ops(sweep: Sweep) -> list[Op]:
    keys = _superadmin_keys()
    return [op for op in sweep.operations() if op.key in keys]


def _send(client: TestClient, db: Session, sweep: Sweep, op: Op, headers: dict[str, str]):
    url, query, body = sweep.build(op, {})
    try:
        return client.request(
            op.method.upper(), url, params=query or None,
            json=body if body is not None else None, headers=headers,
        )
    except Exception as error:  # noqa: BLE001
        return _Crashed(error)
    finally:
        db.rollback()


@pytest.fixture()
def sweep() -> Sweep:
    return Sweep(app)


@pytest.fixture()
def superadmin(db_session: Session) -> Persona:
    persona = _make_user(db_session, "platform-staff@flowpilot.test")
    persona.user.is_superuser = True
    db_session.commit()
    return persona


def test_the_platform_sections_are_all_discovered(sweep: Sweep) -> None:
    """If discovery silently found nothing, every test below would pass vacuously."""
    found = {op.key for op in _superadmin_ops(sweep)}
    for expected in (
        "GET /api/v1/admin/cogs/margins/summary",
        "GET /api/v1/admin/revops/metrics",
        "GET /api/v1/admin/sovereign",
        "POST /api/v1/admin/revops/invoices/{invoice_id}/void",
        "POST /api/v1/admin/sovereign/licence",
    ):
        assert expected in found, expected
    assert len(found) >= 35, len(found)


@pytest.mark.parametrize("who", ["owner", "org_admin", "ws_admin", "contributor", "viewer", "other_org_member", "non_member"])
def test_every_tenant_role_is_refused_on_every_platform_route(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep, who: str
) -> None:
    persona: Persona = getattr(tenant, who)
    leaked, crashed = [], []
    for op in _superadmin_ops(sweep):
        response = _send(client, db_session, sweep, op, persona.headers)
        if response.status_code in DENIED:
            continue
        (crashed if response.status_code >= 500 else leaked).append(f"{op.key} -> {response.status_code}")
    assert not leaked, f"{who} reached platform routes:\n  " + "\n  ".join(leaked)
    assert not crashed, f"{who} crashed platform routes:\n  " + "\n  ".join(crashed)


def test_anonymous_callers_are_refused_on_every_platform_route(
    client: TestClient, db_session: Session, sweep: Sweep
) -> None:
    leaked = []
    for op in _superadmin_ops(sweep):
        response = _send(client, db_session, sweep, op, {})
        if response.status_code not in {401, 403}:
            leaked.append(f"{op.key} -> {response.status_code}")
    assert not leaked, "\n  ".join(leaked)


def test_a_platform_route_is_reachable_by_a_real_superadmin(
    client: TestClient, db_session: Session, sweep: Sweep, superadmin: Persona
) -> None:
    """Control: refusals above are about the caller, not a broken route."""
    reads = [op for op in _superadmin_ops(sweep) if op.method == "get" and not op.path_params]
    assert len(reads) >= 8, len(reads)
    reached = 0
    for op in reads:
        response = _send(client, db_session, sweep, op, superadmin.headers)
        assert response.status_code not in {401, 403}, (op.key, response.status_code)
        if response.status_code == 200:
            reached += 1
    assert reached >= 5, reached


def test_a_tenant_owner_who_is_not_a_superuser_stays_refused_even_with_the_flag_on_a_different_user(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep, superadmin: Persona
) -> None:
    """Making someone else a superuser must not widen anyone else's access."""
    op = next(o for o in _superadmin_ops(sweep) if o.key == "GET /api/v1/admin/revops/metrics")
    assert _send(client, db_session, sweep, op, superadmin.headers).status_code == 200
    assert _send(client, db_session, sweep, op, tenant.owner.headers).status_code in DENIED


def test_the_superuser_flag_cannot_be_set_through_the_profile_or_registration_api(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    """Mass-assignment guard: a user must not be able to promote themselves."""
    for path, method in (("/api/v1/me/profile", "patch"),):
        response = client.request(
            method, path, json={"is_superuser": True, "is_active": True}, headers=tenant.owner.headers
        )
        db_session.refresh(tenant.owner.user)
        assert tenant.owner.user.is_superuser is False, (path, response.status_code)

    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"escalate-{datetime.now(timezone.utc).timestamp():.0f}@example.com",
            "password": "Sup3r-secret-pass!",
            "is_superuser": True,
        },
    )
    from app.models.user import User

    created = db_session.query(User).filter(User.email.like("escalate-%")).first()
    if created is not None:
        assert created.is_superuser is False, response.status_code
