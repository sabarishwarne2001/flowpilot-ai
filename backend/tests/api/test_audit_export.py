import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditLog, AuditResourceType
from app.services.audit_export_service import neutralise_csv_value


def test_neutralise_csv_value_unit():
    assert neutralise_csv_value("=HYPERLINK('http://evil.com')") == "'=HYPERLINK('http://evil.com')"
    assert neutralise_csv_value("+123") == "'+123"
    assert neutralise_csv_value("-123") == "'-123"
    assert neutralise_csv_value("@admin") == "'@admin"
    assert neutralise_csv_value("Normal Text") == "Normal Text"


def test_export_csv_and_jsonl_headers_and_content(client: TestClient, tenant):
    org_id = tenant.organization.id
    headers = tenant.org_admin.headers

    # CSV Export
    res_csv = client.get(
        f"/api/v1/organizations/{org_id}/audit-logs/export?format=csv",
        headers=headers,
    )
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.headers["content-type"]
    assert "id,created_at" in res_csv.text

    # JSONL Export
    res_jsonl = client.get(
        f"/api/v1/organizations/{org_id}/audit-logs/export?format=jsonl",
        headers=headers,
    )
    assert res_jsonl.status_code == 200
    assert "application/x-ndjson" in res_jsonl.headers["content-type"]


def test_csv_formula_injection_neutralised(client: TestClient, db_session: Session, tenant):
    org_id = tenant.organization.id

    row = AuditLog(
        organization_id=org_id,
        resource_type=AuditResourceType.WORKSPACE,
        action=AuditAction.UPDATED,
        user_agent="=HYPERLINK('http://evil.com','click')",
    )
    db_session.add(row)
    db_session.flush()

    res = client.get(
        f"/api/v1/organizations/{org_id}/audit-logs/export?format=csv",
        headers=tenant.org_admin.headers,
    )
    assert res.status_code == 200
    assert "'=HYPERLINK" in res.text


def test_export_records_audit_event(client: TestClient, db_session: Session, tenant):
    org_id = tenant.organization.id

    res = client.get(
        f"/api/v1/organizations/{org_id}/audit-logs/export?format=csv",
        headers=tenant.org_admin.headers,
    )
    assert res.status_code == 200

    stmt = select(AuditLog).where(
        AuditLog.organization_id == org_id,
        AuditLog.resource_type == AuditResourceType.AUDIT_LOG,
        AuditAction.EXPORTED == AuditLog.action,
    )
    audit_row = db_session.execute(stmt).scalar_one_or_none()
    assert audit_row is not None
    assert audit_row.details["format"] == "csv"


@pytest.mark.parametrize(
    ("sent", "media_type", "extension"),
    [("CSV", "text/csv", "csv"), ("NDJSON", "application/x-ndjson", "jsonl"),
     ("ndjson", "application/x-ndjson", "jsonl"), ("JSONL", "application/x-ndjson", "jsonl")],
)
def test_export_accepts_the_console_spellings(client: TestClient, tenant, sent, media_type, extension):
    """F-052: the console sent format=CSV / format=NDJSON and got 422, so no export ever worked."""
    res = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/audit-logs/export?format={sent}",
        headers=tenant.org_admin.headers,
    )
    assert res.status_code == 200, res.text
    assert media_type in res.headers["content-type"]
    assert res.headers["content-disposition"].endswith(f'.{extension}"')


def test_export_still_refuses_an_unknown_format(client: TestClient, tenant):
    res = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/audit-logs/export?format=xlsx",
        headers=tenant.org_admin.headers,
    )
    assert res.status_code == 422
