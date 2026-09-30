"""Partner programme — a partner's book, keys and payouts belong to its members only.

    pytest tests/security/test_partner_isolation.py -q

`/partners/{partner_id}/...` has 25 routes whose only route-level requirement is
"any signed-in user"; membership of the PARTNER is checked inside each handler.
That is the shape where an insecure direct object reference (IDOR) hides: a
tenant user who learns a partner id (from a log, an email, a URL) must get
nothing, whichever tenant they belong to.

A partner owner holds standing over a book of organizations, so the blast
radius is larger than a tenant's. These tests use a REAL partner and real
memberships, not random ids, so a refusal means "you are not a member", not
"no such partner".
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.partner import Partner, PartnerMember
from tests.conftest import Fixture, Persona, _make_user
from tests.security.route_sweep import Op, Sweep
from tests.security.test_cross_tenant_sweep import _Crashed

DENIED = {401, 403, 404}
PLATFORM_ONLY = {  # documented as [platform]: refused to every partner member too
    "POST /api/v1/partners",
    "POST /api/v1/partners/{partner_id}/agreements",
    "POST /api/v1/partners/{partner_id}/payouts/{period_id}/seal",
    "POST /api/v1/partners/{partner_id}/payouts/{period_id}/paid",
}


@pytest.fixture()
def sweep() -> Sweep:
    return Sweep(app)


@pytest.fixture()
def partner_world(db_session: Session, tenant: Fixture) -> dict[str, Any]:
    partner = Partner(
        slug=f"resell-{uuid.uuid4().hex[:8]}",
        name="Resell Co",
        owner_organization_id=tenant.foreign_workspace.organization_id,
    )
    db_session.add(partner)
    db_session.flush()
    people: dict[str, Persona] = {}
    for role in ("OWNER", "ADMIN", "ANALYST"):
        persona = _make_user(db_session, f"partner-{role.lower()}-{uuid.uuid4().hex[:6]}@resell.test")
        db_session.add(PartnerMember(partner_id=partner.id, user_id=persona.user.id, role=role, status="ACTIVE"))
        people[role] = persona
    db_session.commit()
    return {"partner": partner, **people}


def _partner_ops(sweep: Sweep) -> list[Op]:
    return [op for op in sweep.operations() if "{partner_id}" in op.path]


def _send(client: TestClient, db: Session, sweep: Sweep, op: Op, who: Persona, partner_id: Any):
    url, query, body = sweep.build(op, {"partner_id": str(partner_id)})
    try:
        return client.request(
            op.method.upper(), url, params=query or None,
            json=body if body is not None else None, headers=who.headers,
        )
    except Exception as error:  # noqa: BLE001
        return _Crashed(error)
    finally:
        db.rollback()


@pytest.mark.parametrize("attacker", ["owner", "org_admin", "contributor", "viewer", "other_org_member", "non_member"])
def test_a_signed_in_user_who_is_not_a_partner_member_gets_nothing(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep, partner_world, attacker: str
) -> None:
    who: Persona = getattr(tenant, attacker)
    partner = partner_world["partner"]
    operations = _partner_ops(sweep)
    assert len(operations) >= 25, len(operations)

    leaked, crashed = [], []
    for op in operations:
        response = _send(client, db_session, sweep, op, who, partner.id)
        if response.status_code in DENIED:
            continue
        # A 422 never reached the handler's membership check but leaks nothing.
        if response.status_code == 422:
            continue
        (crashed if response.status_code >= 500 else leaked).append(f"{op.key} -> {response.status_code}")
    assert not leaked, f"{attacker} reached a partner's routes:\n  " + "\n  ".join(leaked)
    assert not crashed, f"{attacker} crashed a partner route:\n  " + "\n  ".join(crashed)


def test_the_partner_routes_do_work_for_a_member(
    client: TestClient, db_session: Session, sweep: Sweep, partner_world
) -> None:
    """Control: the attackers above are refused because they are not members."""
    partner, analyst = partner_world["partner"], partner_world["ANALYST"]
    ok = 0
    for op in _partner_ops(sweep):
        if op.method == "get" and op.path in (
            "/api/v1/partners/{partner_id}",
            "/api/v1/partners/{partner_id}/members",
            "/api/v1/partners/{partner_id}/book",
        ):
            response = _send(client, db_session, sweep, op, analyst, partner.id)
            assert response.status_code == 200, (op.key, response.status_code, response.text[:200])
            ok += 1
    assert ok == 3


#: Routes documented as [P-OWNER] or [P-ADMIN]; an ANALYST must be refused.
ANALYST_FORBIDDEN = (
    "PATCH /api/v1/partners/{partner_id}",
    "POST /api/v1/partners/{partner_id}/members",
    "DELETE /api/v1/partners/{partner_id}/members/{user_id}",
    "POST /api/v1/partners/{partner_id}/book",
    "POST /api/v1/partners/{partner_id}/signing-keys",
    "POST /api/v1/partners/{partner_id}/signing-keys/{key_id}/revoke",
    "POST /api/v1/partners/{partner_id}/payouts",
    "POST /api/v1/partners/{partner_id}/catalog",
    "POST /api/v1/partners/{partner_id}/catalog/{item_id}/manifests",
    "PATCH /api/v1/partners/{partner_id}/catalog/{item_id}",
)
#: [P-OWNER] only; an ADMIN must be refused as well.
ADMIN_FORBIDDEN = (
    "PATCH /api/v1/partners/{partner_id}",
    "POST /api/v1/partners/{partner_id}/members",
    "DELETE /api/v1/partners/{partner_id}/members/{user_id}",
    "POST /api/v1/partners/{partner_id}/signing-keys",
    "POST /api/v1/partners/{partner_id}/signing-keys/{key_id}/revoke",
)


@pytest.mark.parametrize(("role", "forbidden"), [("ANALYST", ANALYST_FORBIDDEN), ("ADMIN", ADMIN_FORBIDDEN)])
def test_a_lower_partner_role_cannot_use_a_higher_roles_routes(
    client: TestClient, db_session: Session, sweep: Sweep, partner_world, role: str, forbidden: tuple[str, ...]
) -> None:
    partner, who = partner_world["partner"], partner_world[role]
    operations = {op.key: op for op in _partner_ops(sweep)}
    escaped = []
    for key in forbidden:
        response = _send(client, db_session, sweep, operations[key], who, partner.id)
        if response.status_code not in DENIED and response.status_code != 422:
            escaped.append(f"{key} -> {response.status_code}")
    assert not escaped, f"{role} used routes above their role:\n  " + "\n  ".join(escaped)


def test_only_the_platform_can_use_platform_routes_even_for_a_partner_owner(
    client: TestClient, db_session: Session, sweep: Sweep, partner_world
) -> None:
    partner, owner = partner_world["partner"], partner_world["OWNER"]
    operations = {op.key: op for op in sweep.operations()}
    escaped = []
    for key in sorted(PLATFORM_ONLY):
        response = _send(client, db_session, sweep, operations[key], owner, partner.id)
        if response.status_code not in DENIED and response.status_code != 422:
            escaped.append(f"{key} -> {response.status_code}")
    assert not escaped, "\n  ".join(escaped)


def test_every_partner_route_checks_partner_membership_or_requires_the_platform() -> None:
    """Structural backstop for routes whose dynamic probe is a 422 (their body
    is validated before the handler runs) and for routes added later: each
    handler under /partners/{partner_id} must call `_partner_ctx`, the one place
    that resolves the caller's partner role, or depend on `require_superadmin`."""
    import inspect

    from fastapi.routing import APIRoute

    from tests.security.test_superadmin_only import _depends_on_superadmin

    unguarded = []
    for route in app.routes:
        if isinstance(route, APIRoute) and "{partner_id}" in route.path:
            source = inspect.getsource(route.endpoint)
            if "_partner_ctx(" not in source and not _depends_on_superadmin(route.dependant):
                unguarded.append(f"{sorted(route.methods)} {route.path}")
    assert not unguarded, unguarded
