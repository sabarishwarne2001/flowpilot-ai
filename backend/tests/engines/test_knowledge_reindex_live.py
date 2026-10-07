"""Settings -> "Reindex knowledge base", run the way the worker runs it.

Two defects, found by clicking the button on a running server:

* Every `knowledge.reindex` job crashed with `SpendLimitMisconfiguredError:
  'embedding.backfill_token' is neither the wildcard '*' nor a billable usage
  event type.` Re-embedding is metered on the NON-billable backfill meter (the
  tenant is not charged twice for a document it already paid to embed), but the
  spend guard asked for the tenant's limits on that meter, and a non-billable
  meter cannot carry a tenant limit, so the guard refused it as misconfigured.
  The job retried until it was dead and nothing was re-embedded.
* The jobs were enqueued with a key per document that never expires, so once a
  document had been reindexed (or had failed to be) every later click answered
  "Queued N document(s)" and queued nothing.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.document_chunk import DocumentChunk
from app.models.job import Job, JobStatus
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


def test_reindex_can_be_requested_again_after_it_ran(engines: Engines) -> None:
    work_item_id = engines.process("refunds.pdf", [PAGE])

    assert engines.post("/work-items/knowledge-base/reindex").status_code == 202
    first = drain(only=["knowledge.reindex"])
    assert [run.ok for run in first] == [True]
    once = _backfill_tokens(engines)

    again = engines.post("/work-items/knowledge-base/reindex")
    assert again.status_code == 202, again.text
    assert again.json()["queued"] == 1
    second = drain(only=["knowledge.reindex"])
    assert [run.ok for run in second] == [True], "the second click queued nothing"
    assert second[0].payload["work_item_id"] == str(work_item_id)
    # The platform's monthly backfill ceiling counts the second run's work too.
    assert _backfill_tokens(engines) == 2 * once


def test_a_double_click_does_not_queue_the_same_document_twice(engines: Engines) -> None:
    engines.process("refunds.pdf", [PAGE])

    assert engines.post("/work-items/knowledge-base/reindex").json()["queued"] == 1
    assert engines.post("/work-items/knowledge-base/reindex").json()["queued"] == 0

    engines.refresh()
    waiting = engines.db.execute(
        select(func.count()).select_from(Job).where(
            Job.job_type == "knowledge.reindex", Job.status == JobStatus.PENDING
        )
    ).scalar_one()
    assert waiting == 1
