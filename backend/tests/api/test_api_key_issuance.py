import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditLog, AuditResourceType


def test_api_key_issuance_and_authentication(client: TestClient, tenant):
    org_id = tenant.organization.id
    headers = tenant.org_admin.headers

    # 1. Issue API Key
    payload = {
        "name": "CI Deployment Key",
        "scopes": ["workspaces:read", "audit_logs:read"],
    }
    res = client.post(
        f"/api/v1/organizations/{org_id}/api-keys",
        json=payload,
        headers=headers,
    )
    assert res.status_code == 201
    data = res.json()
    assert "token" in data
    assert data["token"].startswith("fp_")
    token = data["token"]
    key_id = data["api_key"]["id"]

    # 2. Authenticate using issued API key on audit logs endpoint
    key_headers = {"Authorization": f"Bearer {token}"}
    res_audit = client.get(
        f"/api/v1/organizations/{org_id}/audit-logs",
        headers=key_headers,
    )
    assert res_audit.status_code == 200

    # 3. Rotate API key
    res_rotate = client.post(
        f"/api/v1/organizations/{org_id}/api-keys/{key_id}/rotate",
        json={"force": False},
        headers=headers,
    )
    assert res_rotate.status_code == 200
    new_token = res_rotate.json()["token"]

    # 4. Both tokens work during 7-day overlap window
    assert client.get(f"/api/v1/organizations/{org_id}/audit-logs", headers=key_headers).status_code == 200
    assert client.get(f"/api/v1/organizations/{org_id}/audit-logs", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200

    # 5. Revoke key
    res_del = client.delete(
        f"/api/v1/organizations/{org_id}/api-keys/{key_id}",
        headers=headers,
    )
    assert res_del.status_code == 200
    assert res_del.json()["deactivated_reason"] == "MANUAL"

    # Revoked token receives 401
    assert client.get(f"/api/v1/organizations/{org_id}/audit-logs", headers={"Authorization": f"Bearer {new_token}"}).status_code == 401