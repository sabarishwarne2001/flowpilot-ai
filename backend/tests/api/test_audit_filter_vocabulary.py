"""The audit log's filters offer what the server accepts (campaign session 3, F-311).

The audit explorer's "Resource type" and "Action" filters were free text sent as enum
parameters: a partial or lowercase word ("updated") was a 422 and the page showed "Audit records
could not be loaded" on every keystroke. The page now offers the vocabulary as choices, read from
the server so the two can never disagree.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models.audit_log import AuditAction, AuditResourceType


def test_the_vocabulary_is_every_code_the_filters_accept_with_a_label(client: TestClient, tenant) -> None:
    url = f"/api/v1/organizations/{tenant.organization.id}/audit-logs/vocabulary"
    response = client.get(url, headers=tenant.org_admin.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["value"] for row in body["resource_types"]] == sorted(m.value for m in AuditResourceType)
    assert [row["value"] for row in body["actions"]] == sorted(m.value for m in AuditAction)
    labels = {row["value"]: row["label"] for row in body["actions"]}
    assert labels["UPDATED"] == "Updated"
    assert all(row["label"] and row["label"] == row["label"].strip() for row in body["actions"])

    for row in body["actions"][:5]:
        filtered = client.get(f"/api/v1/organizations/{tenant.organization.id}/audit-logs",
                              params={"action": row["value"]}, headers=tenant.org_admin.headers)
        assert filtered.status_code == 200, (row, filtered.text)


def test_members_who_cannot_read_the_log_cannot_read_its_vocabulary(client: TestClient, tenant) -> None:
    url = f"/api/v1/organizations/{tenant.organization.id}/audit-logs/vocabulary"
    assert client.get(url, headers=tenant.viewer.headers).status_code in (403, 404)
    assert client.get(url, headers=tenant.other_org_member.headers).status_code in (403, 404)
