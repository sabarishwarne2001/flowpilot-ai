"""The platform security wall: super-admin consoles refuse every tenant user.

Unit economics (COGS / margins), RevOps (MRR, price books, promo codes,
contracts) and the Sovereign edition are platform-operator consoles. Every
route behind `require_superadmin` is called here, from the live route table,
as an organization OWNER and ADMIN of a paying tenant: each must refuse
(404 - the consoles do not admit they exist - or 403), and none may run.
The same routes answer a platform super-admin. Any route mounted under
/admin that is NOT behind the guard fails the test.

Then the numbers: unit economics never claims a margin without a known cost
basis (and when it does, margin = attributed revenue - cost basis; the
arithmetic itself is covered by tests/services/test_cogs_margin_service.py),
and RevOps reports ARR = 12 x MRR in every currency.
"""

from __future__ import annotations

import re
import uuid

from fastapi.routing import APIRoute

from app.api.deps import require_superadmin
from app.main import app
from tests.engines.conftest import Engines


def _guarded(route: APIRoute) -> bool:
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is require_superadmin:
            return True
        stack.extend(dependant.dependencies)
    return False


def _routes() -> list[tuple[str, str, APIRoute]]:
    out = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                out.append((method, route.path, route))
    return out


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)


def test_every_platform_route_refuses_tenant_owners_and_admins(engines: Engines) -> None:
    guarded = [(m, p) for m, p, r in _routes() if _guarded(r)]
    assert len(guarded) >= 25, guarded  # COGS, RevOps, Sovereign
    unguarded_admin = [(m, p) for m, p, r in _routes() if p.startswith("/api/v1/admin") and not _guarded(r)]
    assert unguarded_admin == [], unguarded_admin

    for persona in (engines.tenant.owner, engines.tenant.org_admin):
        for method, path in guarded:
            response = engines.client.request(method, _fill(path), headers=persona.headers, json={})
            assert response.status_code in (403, 404), (persona.user.email, method, path, response.status_code)


def test_super_admins_get_through_and_the_numbers_add_up(engines: Engines) -> None:
    admin = engines.tenant.non_member
    admin.user.is_superuser = True
    engines.db.commit()

    summary = engines.client.get("/api/v1/admin/cogs/margins/summary", headers=admin.headers)
    assert summary.status_code == 200, summary.text
    figures = summary.json()["figures"]
    if figures["gross_margin_micros"] is not None:  # the margin is never claimed without a known cost basis
        assert figures["gross_margin_micros"] == figures["attributed_revenue_micros"] - figures["cost_basis_micros"]
    else:
        assert figures["is_trustworthy"] is False

    metrics = engines.client.get("/api/v1/admin/revops/metrics", headers=admin.headers)
    assert metrics.status_code == 200, metrics.text
    for currency, row in metrics.json()["currencies"].items():
        assert row["arr_micros"] == row["mrr_micros"] * 12, (currency, row)

    sovereign = engines.client.get("/api/v1/admin/sovereign/release", headers=admin.headers)
    assert sovereign.status_code == 200, sovereign.text
