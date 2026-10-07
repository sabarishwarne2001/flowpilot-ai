"""Settings -> "Reindex knowledge base", run the way the worker runs it.

Found by clicking the button on a running server:

* Every `knowledge.reindex` job crashed with `SpendLimitMisconfiguredError:
  'embedding.backfill_token' is neither the wildcard '*' nor a billable usage
  event type.` Re-embedding is metered on the NON-billable backfill meter (the
  tenant is not charged twice for a document it already paid to embed), but the
  spend guard asked for the tenant's limits on that meter, and a non-billable
  meter cannot carry a tenant limit, so the guard refused it as misconfigured.
  The job retried until it was dead and nothing was re-embedded.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.document_chunk import DocumentChunk
from app.models.usage_event import UsageEvent
from tests.engines.conftest import Engines, drain

PAGE = [
    "REFUND POLICY HANDBOOK",
    "Customers may return goods within thirty days of delivery for a full refund.",
    "Refunds are issued to the original payment method within five business days.",
    "Damaged goods must be reported within forty-eight hours of delivery.",
]


def _backfill_tokens(engines: Engines) -> int:
    engines.refresh()
    return engines.db.execute(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.organization_id == engines.org,
            UsageEvent.event_type == "embedding.backfill_token",
        )
    ).scalar_one()


def _chunks(engines: Engines, work_item_id) -> int:
    engines.refresh()
    return engines.db.execute(
        select(func.count()).select_from(DocumentChunk).where(DocumentChunk.work_item_id == work_item_id)
    ).scalar_one()


def test_reindex_job_completes_and_meters_on_the_backfill_meter(engines: Engines) -> None:
    work_item_id = engines.process("refunds.pdf", [PAGE])
    assert str(engines.item(work_item_id).status).endswith("COMPLETED")
    assert _chunks(engines, work_item_id) > 0

    response = engines.post("/work-items/knowledge-base/reindex")
    assert response.status_code == 202, response.text
    assert response.json()["queued"] == 1

    runs = drain(only=["knowledge.reindex"])
    assert len(runs) == 1
    assert runs[0].ok, runs[0].error
    assert runs[0].result["outcome"] == "COMPLETED", runs[0].result
    assert _chunks(engines, work_item_id) > 0

    assert _backfill_tokens(engines) > 0, "the re-embedding was not recorded on the backfill meter"
