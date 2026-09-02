import uuid
from datetime import UTC, datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.pagination import decode_cursor, encode_cursor, filter_digest
from app.models.audit_log import AuditAction, AuditLog, AuditResourceType


def test_keyset_pagination_same_timestamp_and_tiebreak(
    client: TestClient, db_session: Session, tenant
):
    org_id = tenant.organization.id
    ws_id = tenant.workspace.id
    user_id = tenant.owner.user.id

    # Seed 150 rows sharing exact transaction timestamp
    now = datetime.now(UTC)
    for _ in range(150):
        row = AuditLog(
            created_at=now,
            organization_id=org_id,
            workspace_id=ws_id,
            actor_id=user_id,
            resource_type=AuditResourceType.WORKSPACE,
            action=AuditAction.UPDATED,
        )
        db_session.add(row)
    db_session.commit()

    headers = tenant.org_admin.headers

    # Page 1
    res1 = client.get(f"/api/v1/organizations/{org_id}/audit-logs?limit=100", headers=headers)
    assert res1.status_code == 200
    p1 = res1.json()
    assert len(p1["items"]) == 100
    assert p1["has_more"] is True
    assert p1["next_cursor"] is not None

    # Page 2 using next_cursor
    res2 = client.get(
        f"/api/v1/organizations/{org_id}/audit-logs?limit=100&cursor={p1['next_cursor']}",
        headers=headers,
    )
    assert res2.status_code == 200
    p2 = res2.json()
    assert len(p2["items"]) == 50
    assert p2["has_more"] is False
    assert p2["next_cursor"] is None

    # Ensure zero overlap
    p1_ids = {item["id"] for item in p1["items"]}
    p2_ids = {item["id"] for item in p2["items"]}
    assert p1_ids.isdisjoint(p2_ids)


def test_offset_parameter_returns_422_tombstone(client: TestClient, tenant):
    org_id = tenant.organization.id
    res = client.get(
        f"/api/v1/organizations/{org_id}/audit-logs?offset=10",
        headers=tenant.org_admin.headers,
    )
    assert res.status_code == 422
    assert "offset pagination was removed" in res.json()["detail"]


def test_filter_digest_mismatch_returns_422(client: TestClient, tenant):
    org_id = tenant.organization.id
    bogus_cursor = encode_cursor(
        created_at=datetime.now(UTC),
        id=uuid.uuid4(),
        digest="invalid_digest",
    )
    res = client.get(
        f"/api/v1/organizations/{org_id}/audit-logs?cursor={bogus_cursor}",
        headers=tenant.org_admin.headers,
    )
    assert res.status_code == 422
