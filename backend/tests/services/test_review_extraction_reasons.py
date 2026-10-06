"""F-111 — the review hub says why an extraction is waiting for a person.

Calibrated-autonomy holds, accuracy-audit samples and extraction-memory trials
are all parked as DISAGREED verifications. The hub's view tested the status
first, so every one of them read "Extracted fields disagree" (DISAGREEMENT,
HIGH) and the Autonomy audits tab was always empty.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.verification import DocumentVerification, VerificationStatus


def _verification(db, tenant, item, details):
    row = DocumentVerification(
        work_item_id=item.id,
        workspace_id=tenant.workspace.id,
        organization_id=tenant.organization.id,
        status=VerificationStatus.DISAGREED,
        agent_count=3,
        agreement_score=Decimal("1"),
        confidence=Decimal("0.99"),
        details=details,
    )
    db.add(row)
    db.flush()
    return row


@pytest.mark.parametrize(
    ("details", "reason", "severity", "headline"),
    [
        ({"disagreed_fields": ["invoice_number"]}, "DISAGREEMENT", "HIGH", "Extracted fields disagree"),
        ({"disagreed_fields": [], "unresolved_conflicts": ["total"]}, "DISAGREEMENT", "HIGH",
         "Extracted fields disagree"),
        ({"disagreed_fields": [], "calibration": {"review_all_fields": True}}, "CALIBRATION_HOLD", "MEDIUM",
         "Held for review: confirm every field"),
        ({"disagreed_fields": [], "calibration": {"review_all_fields": True, "audit_sample": True}},
         "AUTONOMY_AUDIT", "LOW", "Accuracy audit: confirm every field"),
        ({"disagreed_fields": [], "extraction_memory": {"review_all_fields": True}}, "PENDING_REVIEW", "MEDIUM",
         "Extraction memory trial: confirm every field"),
        ({"disagreed_fields": ["vendor"], "escalation": {"review_all_fields": True}}, "ESCALATION", "HIGH",
         "Escalated by an automation rule"),
        # A real disagreement outranks a calibration hold on the same document.
        ({"disagreed_fields": ["vendor"], "calibration": {"review_all_fields": True}}, "DISAGREEMENT", "HIGH",
         "Extracted fields disagree"),
        ({}, "DISAGREEMENT", "HIGH", "Extracted fields disagree"),
    ],
)
def test_each_hold_has_its_own_reason(db_session, tenant, work_item_factory, details, reason, severity, headline):
    item = work_item_factory()
    verification = _verification(db_session, tenant, item, details)
    db_session.commit()

    row = db_session.execute(
        text("SELECT review_reason, severity, headline FROM review_queue_items WHERE item_id = :id"),
        {"id": verification.id},
    ).one()

    assert (row.review_reason, row.severity, row.headline) == (reason, severity, headline)
