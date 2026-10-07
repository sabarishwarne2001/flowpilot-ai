"""F-127 — the extraction workbench's queue must list items in one stable order.

`GET /verifications` sorted by `created_at` only and paged by offset. Every
verification written in one transaction shares `created_at` (Postgres `now()` is
the transaction's start), and so does a burst of uploads processed together. For
those rows the database may return any order, and a different one per request:
the workbench's cursor ("item 3 of 12") then pointed at another document after a
refetch, and a row could appear on two pages or on none.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.verification import DocumentVerification, VerificationStatus

pytestmark = pytest.mark.usefixtures("test_database")

SAME_MOMENT = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def simultaneous(db_session: Session, tenant, work_item_factory) -> list[str]:
    ids = []
    for _ in range(7):
        row = DocumentVerification(
            work_item_id=work_item_factory().id,
            workspace_id=tenant.workspace.id,
            organization_id=tenant.organization.id,
            status=VerificationStatus.DISAGREED,
            agent_count=2,
            agreement_score=Decimal("0.5"),
        )
        db_session.add(row)
        db_session.flush()
        ids.append(str(row.id))
    db_session.execute(
        update(DocumentVerification)
        .where(DocumentVerification.workspace_id == tenant.workspace.id)
        .values(created_at=SAME_MOMENT)
    )
    db_session.commit()
    return ids


def _list(client: TestClient, tenant, **params) -> list[str]:
    query = "&".join(f"{key}={value}" for key, value in {"status": "DISAGREED", **params}.items())
    response = client.get(
        f"/api/v1/workspaces/{tenant.workspace.id}/verifications?{query}",
        headers={"Authorization": f"Bearer {tenant.contributor.token}"},
    )
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()]


def test_rows_with_the_same_timestamp_come_back_in_one_defined_order(client, tenant, simultaneous):
    # Newest first, then the id: the same answer on every request, whatever the heap holds.
    assert _list(client, tenant) == sorted(simultaneous, reverse=True)


def test_paging_through_the_queue_shows_every_item_exactly_once(client, tenant, simultaneous):
    seen: list[str] = []
    for skip in range(0, len(simultaneous), 2):
        seen.extend(_list(client, tenant, skip=skip, limit=2))
    assert seen == sorted(simultaneous, reverse=True)
