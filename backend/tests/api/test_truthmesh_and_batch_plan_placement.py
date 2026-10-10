"""Owner decisions N-032 and N-033 — Batch operations and TruthMesh are on Business and Enterprise.

    pytest tests/api/test_truthmesh_and_batch_plan_placement.py -q

Both features shipped with provisional placements: Batch operations on Business
and Enterprise, TruthMesh on Enterprise only. The owner decided (2026-10-10) that
both belong to Business and Enterprise. The published tiers are the one source of
truth every gate reads, so these tests pin the tier table and the API answer on
each plan.
"""

from __future__ import annotations

import pytest

from app.core import entitlements
from tests.conftest import Fixture
from tests.security.plans import put_on_plan


def _holders(capability: str) -> list[str]:
    from tests.security.plans import _seed

    matrix = _seed().capability_matrix()
    return sorted(plan for plan, keys in matrix.items() if capability in keys)


def test_the_tiers_that_include_truthmesh_are_business_and_enterprise() -> None:
    assert _holders(entitlements.TRUTHMESH_CAPABILITY) == ["business", "enterprise"]


def test_the_tiers_that_include_batch_operations_are_business_and_enterprise() -> None:
    assert _holders(entitlements.BATCH_DISPATCH_CAPABILITY) == ["business", "enterprise"]


@pytest.mark.parametrize("plan", ["business", "enterprise"])
def test_truthmesh_answers_on_business_and_enterprise(client, db_session, tenant: Fixture, plan: str) -> None:
    put_on_plan(db_session, tenant.organization, plan)
    db_session.commit()
    response = client.get(
        f"/api/v1/workspaces/{tenant.workspace.id}/truthmesh/overview", headers=tenant.owner.headers
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("plan", ["free", "developer"])
def test_truthmesh_is_refused_below_business(client, db_session, tenant: Fixture, plan: str) -> None:
    put_on_plan(db_session, tenant.organization, plan)
    db_session.commit()
    response = client.get(
        f"/api/v1/workspaces/{tenant.workspace.id}/truthmesh/overview", headers=tenant.owner.headers
    )
    assert response.status_code == 402, response.text
    body = response.json()
    details = body.get("details") or (body.get("detail") or {}).get("details") or {}
    assert entitlements.TRUTHMESH_CAPABILITY in (response.text + str(details))
