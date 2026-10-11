"""The calibrated-autonomy error limit changes what is approved without a person (campaign session 2, brief C).

    pytest tests/engines/test_autonomy_limit_changes_decisions_live.py -q

Settings -> Calibrated autonomy lets an Enterprise owner set the error limit for automatic
approval. The control is only real if saving a tighter limit, through the same endpoint
the page calls, raises the confidence a document needs and so sends more of them to review,
and a looser limit does the reverse.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from app.services.calibration import apply
from app.services.calibration import vocabulary as vocab
from tests.engines.conftest import Engines

DECISION = vocab.DECISION_VERIFICATION_DOCUMENT


def _reviewed_history(engines: Engines, count: int = 600) -> None:
    """Reviewed outcomes where a higher score is right more often, as a real engine's are."""
    org = engines.tenant.organization.id
    now = datetime.now(timezone.utc)
    rows = []
    for index in range(count):
        score = Decimal(index) / Decimal(count)
        # Right in a share of cases that rises with the score: wrong below ~0.3, right above ~0.9.
        correct = (index * 7919) % 100 < int(score * 120) - 20
        rows.append({"id": str(uuid.uuid4()), "org": str(org), "decision": DECISION, "score": score,
                     "correct": correct, "source": vocab.SOURCE_DOCUMENT_VERIFICATIONS,
                     "source_id": str(uuid.uuid4()), "at": now - timedelta(hours=count - index)})
    engines.db.execute(text(
        "INSERT INTO calibration_labels (id, organization_id, decision_type, raw_score, correct, source_table, "
        "source_id, was_auto_approved, was_audit_sample, auto_eligible, sample_weight, observed_at) VALUES "
        "(:id, :org, :decision, :score, :correct, :source, :source_id, false, false, true, 1, :at)"), rows)
    engines.db.commit()


def _save_limit(engines: Engines, limit: str) -> dict:
    org = engines.tenant.organization.id
    response = engines.client.put(
        f"/api/v1/organizations/{org}/autonomy/{DECISION}",
        json={"target_error_rate": limit, "audit_sample_rate": "0.01"},
        headers=engines.tenant.owner.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"], body
    return body["entry"]


def _sent_to_review(engines: Engines) -> int:
    engines.refresh()
    below = 0
    for step in range(50, 100):
        decision = apply.decide(engines.db, organization_id=engines.tenant.organization.id, decision_type=DECISION,
                                raw_score=Decimal(step) / 100, sample_key=f"score-{step}")
        assert decision is not None, "an Enterprise tenant has no autonomy decision"
        below += decision.reason == "below_threshold"
    return below


def test_a_tighter_error_limit_sends_more_documents_to_review(engines: Engines) -> None:
    engines.plan("enterprise")
    _reviewed_history(engines)

    loose = _save_limit(engines, "0.15")
    assert loose["threshold"] is not None, loose
    loose_review = _sent_to_review(engines)

    tight = _save_limit(engines, "0.03")
    assert Decimal(str(tight["threshold"])) > Decimal(str(loose["threshold"])), (loose, tight)
    tight_review = _sent_to_review(engines)
    assert tight_review > loose_review, (loose_review, tight_review)

    back = _save_limit(engines, "0.15")
    assert Decimal(str(back["threshold"])) == Decimal(str(loose["threshold"]))
    assert _sent_to_review(engines) == loose_review
