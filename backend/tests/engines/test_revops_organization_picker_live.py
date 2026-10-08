"""The contract form picks an organization by name (Phase 3, RevOps console).

The form asked the operator to paste an organization UUID. GET /admin/revops/organizations lists
the organizations a contract can be drafted for: by name, never archived ones, and a search where
"%" and "_" are letters (F-156), not wildcards that match every organization.
"""

from __future__ import annotations

from app.models.organization import Organization, OrganizationStatus
from tests.engines.conftest import Engines

URL = "/api/v1/admin/revops/organizations"


def _superadmin(engines: Engines):
    admin = engines.tenant.non_member
    admin.user.is_superuser = True
    engines.db.commit()
    return admin


def test_the_picker_lists_organizations_by_name_with_their_plan(engines: Engines) -> None:
    admin = _superadmin(engines)
    response = engines.client.get(URL, headers=admin.headers)
    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()}
    mine = rows[str(engines.org)]
    assert mine["name"] == engines.tenant.organization.name
    assert mine["has_active_contract"] is False

    found = engines.client.get(URL, params={"q": engines.tenant.organization.name[:6]}, headers=admin.headers)
    assert str(engines.org) in {row["id"] for row in found.json()}


def test_archived_organizations_are_left_out(engines: Engines) -> None:
    admin = _superadmin(engines)
    organization = engines.db.get(Organization, engines.org)
    organization.status = OrganizationStatus.ARCHIVED
    engines.db.commit()
    ids = {row["id"] for row in engines.client.get(URL, headers=admin.headers).json()}
    assert str(engines.org) not in ids


def test_a_percent_sign_is_searched_for_not_a_wildcard(engines: Engines) -> None:
    admin = _superadmin(engines)
    response = engines.client.get(URL, params={"q": "%"}, headers=admin.headers)
    assert response.status_code == 200
    assert all("%" in row["name"] or "%" in row["slug"] for row in response.json())


def test_tenant_owners_are_refused(engines: Engines) -> None:
    response = engines.client.get(URL, headers=engines.tenant.owner.headers)
    assert response.status_code in (403, 404)
