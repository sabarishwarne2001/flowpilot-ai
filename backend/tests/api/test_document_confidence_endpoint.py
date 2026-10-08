"""The document viewer's field confidence (GET /workspaces/{id}/work-items/{id}/confidence).

Verification scores every extracted field, but the only routes that returned those scores were
the review queue's (CONTRIBUTOR, by verification id). The document viewer, which every member uses,
could not show how sure the extraction is about a field. This read route gives it, to viewers too.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.verification import DocumentVerification, DocumentVerificationField, VerificationStatus


def _url(tenant, work_item_id) -> str:
    return f"/api/v1/workspaces/{tenant.workspace.id}/work-items/{work_item_id}/confidence"


def test_a_verified_document_reports_its_fields(client, db_session, tenant, work_item_factory):
    item = work_item_factory(extracted_entities={"vendor_name": "Acme", "total_amount": 10})
    verification = DocumentVerification(
        work_item_id=item.id, workspace_id=tenant.workspace.id, organization_id=tenant.organization.id,
        status=VerificationStatus.DISAGREED, agent_count=2, agreement_score=Decimal("0.5"),
        confidence=Decimal("0.75"), details={},
    )
    db_session.add(verification)
    db_session.flush()
    db_session.add_all([
        DocumentVerificationField(verification_id=verification.id, field_path="vendor_name", agreed=True,
                                  confidence=Decimal("0.99"), agent_values=["Acme", "Acme"]),
        DocumentVerificationField(verification_id=verification.id, field_path="total_amount", agreed=False,
                                  confidence=Decimal("0.5"), agent_values=[10, 12], disagreement_kind="CONFLICT"),
    ])
    db_session.commit()

    response = client.get(_url(tenant, item.id), headers=tenant.viewer.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["confidence"] == 0.75 and body["in_review"] is True
    assert body["reason"] == "The verification agents disagree on 1 field."
    assert {f["field"]: (f["confidence"], f["agreed"], f["disagreement"]) for f in body["fields"]} == {
        "vendor_name": (0.99, True, None),
        "total_amount": (0.5, False, "CONFLICT"),
    }


def test_an_unverified_document_has_no_score(client, db_session, tenant, work_item_factory):
    item = work_item_factory()
    db_session.commit()

    body = client.get(_url(tenant, item.id), headers=tenant.viewer.headers).json()

    assert body["confidence"] is None and body["fields"] == [] and body["in_review"] is False


def test_another_workspace_document_is_not_found(client, db_session, tenant, work_item_factory):
    foreign = work_item_factory(workspace_id=tenant.foreign_workspace.id)
    db_session.commit()

    assert client.get(_url(tenant, foreign.id), headers=tenant.owner.headers).status_code == 404
    assert client.get(_url(tenant, uuid.uuid4()), headers=tenant.owner.headers).status_code == 404
