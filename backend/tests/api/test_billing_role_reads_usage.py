"""The BILLING role sees the organization's usage (F-192).

BILLING exists for a finance contact who must see the plan, invoices and usage and never the
documents (RoleGuide: "Sees the plan, invoices and usage"). The Billing page reads usage from
/usage/summary, /usage/series and /usage/limits, which admitted OWNER and ADMIN only: signed in as
BILLING the page showed "couldn't be loaded" under Usage and Limits, with three 403s. Reading the
spend limits in force was refused the same way. Changing a spend limit stays OWNER and ADMIN.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.organization import OrganizationRole
from tests.conftest import Fixture, _make_user, _seat


def _billing_persona(db: Session, tenant: Fixture):
    persona = _make_user(db, f"billing-{tenant.organization.slug}@acme.com")
    _seat(db, tenant.organization, persona, OrganizationRole.BILLING)
    db.commit()
    return persona


def test_billing_reads_usage_summary_series_limits_and_spend_limits(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    billing = _billing_persona(db_session, tenant)
    org = tenant.organization.id
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    for path, params in (
        (f"/api/v1/organizations/{org}/usage/summary", {"period": "MONTH"}),
        (f"/api/v1/organizations/{org}/usage/series", {"from": since, "granularity": "DAY"}),
        (f"/api/v1/organizations/{org}/usage/limits", {}),
        (f"/api/v1/organizations/{org}/usage-limits", {}),
    ):
        response = client.get(path, params=params, headers=billing.headers)
        assert response.status_code == 200, (path, response.status_code, response.text[:200])


def test_billing_still_cannot_change_a_spend_limit(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    billing = _billing_persona(db_session, tenant)
    response = client.put(
        f"/api/v1/organizations/{tenant.organization.id}/usage-limits",
        json={"limit_key": "*", "period": "MONTH", "max_cost_micros": 5_000_000, "hard_stop": False},
        headers=billing.headers,
    )
    assert response.status_code in (403, 404), response.text


def test_a_plain_member_is_still_refused(client: TestClient, tenant: Fixture) -> None:
    response = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/usage/summary", headers=tenant.contributor.headers
    )
    assert response.status_code in (403, 404)
