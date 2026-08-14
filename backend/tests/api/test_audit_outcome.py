import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditLog, AuditOutcome, AuditResourceType


def test_require_org_role_denial_records_audit_event(
    client: TestClient, db_session: Session, tenant
):
    org_id = tenant.organization.id
    # Viewer persona has insufficient privileges for org admin-only endpoint
    viewer_headers = tenant.viewer.headers

    res = client.get(
        f"/api/v1/organizations/{org_id}/audit-logs",
        headers=viewer_headers,
    )
    assert res.status_code == 403

    stmt = select(AuditLog).where(
        AuditLog.organization_id == org_id,
        AuditLog.outcome == AuditOutcome.DENIED,
    )
    denial_row = db_session.execute(stmt).scalar_one_or_none()
    assert denial_row is not None
    assert denial_row.action == AuditAction.ACCESSED
    assert denial_row.actor_id == tenant.viewer.user.id