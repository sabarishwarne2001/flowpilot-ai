"""A document deleted or reprocessed while the pipeline is running (campaign session 2, brief C).

    pytest tests/engines/test_hostile_timing_live.py -q

People delete a document seconds after uploading it, and double-click "Reprocess". The
pipeline is a chain of background jobs (read, enrich, then the engines), so each job may find
its document gone. Every job must then finish quietly (no failed or dead job, no traceback),
and nothing may bring the document back: no role, case, chunk, finding or entity row that
names it once it has been deleted. Reprocessing a document already being read is refused,
so one click is one run and one charge.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.models.job import Job, JobStatus
from tests.engines.conftest import Engines, drain, make_pdf

INVOICE = [
    "ACME SUPPLIES LTD",
    "TAX INVOICE",
    "Invoice Number: {marker}",
    "Invoice Date: 2026-09-01",
    "PO Number: PO-HOSTILE-5001",
    "Item  Qty  Unit Price  Amount",
    "Steel bolts M8  100  2.50  250.00",
    "Total: 250.00 USD",
]


def _upload(engines: Engines, marker: str) -> uuid.UUID:
    engines.llm.record(marker, "Invoice", {
        "vendor_name": "Acme Supplies Ltd", "invoice_number": marker, "total_amount": "250.00",
        "currency": "USD", "date": "2026-09-01", "po_number": "PO-HOSTILE-5001",
    })
    return engines.upload(f"{marker}.pdf", make_pdf([[line.format(marker=marker) for line in INVOICE]]))


def _rows_naming(engines: Engines, work_item_id: uuid.UUID) -> dict[str, int]:
    """Every table with a work_item_id column that still has a row for this document."""
    engines.refresh()
    tables = engines.db.execute(text(
        "SELECT table_name FROM information_schema.columns WHERE table_schema = 'public' "
        "AND column_name = 'work_item_id' AND table_name NOT IN ('audit_logs', 'jobs')"
    )).scalars().all()
    found = {}
    for table in tables:
        count = engines.db.execute(text(f'SELECT count(*) FROM "{table}" WHERE work_item_id = :id'),
                                   {"id": str(work_item_id)}).scalar_one()
        if count:
            found[table] = count
    return found


def _no_dead_jobs(engines: Engines) -> None:
    engines.refresh()
    dead = engines.db.execute(select(Job.job_type, Job.last_error).where(
        Job.status.in_([JobStatus.DEAD, JobStatus.FAILED]))).all()
    assert not dead, dead


@pytest.mark.parametrize("ran_first", [
    (),
    ("document.extract",),
    ("document.extract", "document.enrich"),
], ids=["before-reading", "after-reading", "after-enrichment"])
def test_a_document_deleted_mid_pipeline_stays_deleted(engines: Engines, ran_first: tuple[str, ...]) -> None:
    engines.plan("enterprise")  # every engine that follows enrichment is on
    work_item_id = _upload(engines, f"INV-HOSTILE-{len(ran_first)}")
    for job_type in ran_first:
        drain(only=[job_type])

    deleted = engines.delete(f"/work-items/{work_item_id}")
    assert deleted.status_code == 204, deleted.text

    drain()  # raises if any job failed
    _no_dead_jobs(engines)
    assert _rows_naming(engines, work_item_id) == {}, "a job brought the deleted document back"
    assert engines.get(f"/work-items/{work_item_id}").status_code == 404


def test_reprocess_clicked_twice_reads_the_document_once(engines: Engines) -> None:
    work_item_id = _upload(engines, "INV-HOSTILE-RE-1")
    drain()
    assert engines.item(work_item_id).status == "COMPLETED"

    first = engines.post(f"/work-items/{work_item_id}/reprocess")
    second = engines.post(f"/work-items/{work_item_id}/reprocess")
    assert first.status_code == 202, first.text
    assert second.status_code == 409, second.text
    assert "already" in second.json()["detail"].lower()

    engines.refresh()
    queued = engines.db.execute(select(Job).where(
        Job.job_type == "document.extract", Job.status == JobStatus.PENDING,
        Job.payload["work_item_id"].astext == str(work_item_id))).scalars().all()
    assert len(queued) == 1, [job.id for job in queued]

    drain()
    _no_dead_jobs(engines)
    assert engines.item(work_item_id).status == "COMPLETED"
    # Once the run has finished, the document can be reprocessed again.
    assert engines.post(f"/work-items/{work_item_id}/reprocess").status_code == 202
    drain()
    assert engines.item(work_item_id).status == "COMPLETED"


def test_reprocess_reads_the_document_again_and_charges_each_run_once(engines: Engines) -> None:
    """A reprocess is a real new run: the model reads it again and its answer replaces the old one.

    The recorded model is not metered, so the upload's run is given the input-token rows the
    real path writes (`llm:<document>:enrich:<operation>:input`). Before the fix those rows
    made the reprocess skip every model call as "already recorded".
    """
    from app.services import usage_service
    from app.services.llm_metering import ENRICH_OPERATIONS, INPUT_EVENT, enrichment_scope

    work_item_id = _upload(engines, "INV-HOSTILE-RE-3")
    drain()
    assert engines.item(work_item_id).extracted_entities["total_amount"] == "250.00"
    engines.refresh()
    for operation in ENRICH_OPERATIONS:
        usage_service.record_usage(
            engines.db, organization_id=engines.tenant.organization.id, workspace_id=engines.ws,
            event_type=INPUT_EVENT, quantity=100, cost_micros=1, provider="groq", resource_type="WORK_ITEM",
            resource_id=work_item_id, idempotency_key=f"{enrichment_scope(work_item_id, operation)}:input",
        )
    engines.db.commit()

    engines.llm.record("INV-HOSTILE-RE-3", "Invoice", {"invoice_number": "INV-HOSTILE-RE-3", "total_amount": "275.00"})
    reads = len([call for call in engines.llm.calls if call[0] == "entities"])
    assert engines.post(f"/work-items/{work_item_id}/reprocess").status_code == 202
    runs = drain()
    assert {run.job_type for run in runs} >= {"document.extract", "document.enrich"}
    skipped = [(run.job_type, run.result) for run in runs if run.job_type in ("document.extract", "document.enrich")
               and ((run.result or {}).get("outcome") == "SKIPPED" or (run.result or {}).get("enrichment_skipped"))]
    assert not skipped, skipped
    assert len([call for call in engines.llm.calls if call[0] == "entities"]) == reads + 1, "the model was not asked again"
    item = engines.item(work_item_id)
    assert (item.status, item.pipeline_stage) == ("COMPLETED", "COMPLETED")
    assert item.extracted_entities["total_amount"] == "275.00", "the reprocessed reading did not replace the old one"


def test_a_document_still_being_read_for_the_first_time_cannot_be_reprocessed(engines: Engines) -> None:
    work_item_id = _upload(engines, "INV-HOSTILE-RE-2")
    response = engines.post(f"/work-items/{work_item_id}/reprocess")
    assert response.status_code == 409, response.text
    drain()
    _no_dead_jobs(engines)


def test_bulk_reprocess_reads_each_document_again_and_skips_one_already_running(engines: Engines) -> None:
    """The bulk action takes the same path as the single button: a real new run, never two at once."""
    done = _upload(engines, "INV-HOSTILE-BULK-1")
    drain()
    engines.llm.record("INV-HOSTILE-BULK-1", "Invoice", {"invoice_number": "INV-HOSTILE-BULK-1", "total_amount": "300.00"})
    running = _upload(engines, "INV-HOSTILE-BULK-2")  # still waiting for its first read

    response = engines.post("/work-items/bulk", {"action": "reprocess", "ids": [str(done), str(running)],
                                                 "idempotency_key": "bulk-reprocess-hostile-1"})
    assert response.status_code == 200, response.text
    outcome = {row["work_item_id"]: row for row in response.json()["results"]}
    assert outcome[str(done)]["outcome"] == "ok", outcome
    assert outcome[str(running)]["outcome"] == "refused", outcome
    assert outcome[str(running)]["code"] == "ALREADY_PROCESSING", outcome

    drain()
    _no_dead_jobs(engines)
    item = engines.item(done)
    assert (item.status, item.pipeline_stage) == ("COMPLETED", "COMPLETED")
    assert item.extracted_entities["total_amount"] == "300.00", "the bulk reprocess did not read the document again"
    assert engines.item(running).status == "COMPLETED"


def test_a_pipeline_job_delivered_twice_does_its_work_once(engines: Engines) -> None:
    """At-least-once delivery: a worker whose lease ran out after it finished hands the job on again."""
    from sqlalchemy import update

    work_item_id = _upload(engines, "INV-HOSTILE-TWICE-1")
    drain()
    before = (_rows_naming(engines, work_item_id), engines.item(work_item_id).extracted_entities)
    asked = len(engines.llm.calls)

    engines.db.execute(update(Job).where(
        Job.job_type.in_(["document.extract", "document.enrich"]),
        Job.payload["work_item_id"].astext == str(work_item_id),
    ).values(status=JobStatus.PENDING, attempts=0, succeeded_at=None))
    engines.db.commit()
    runs = drain(only=["document.extract", "document.enrich"])

    assert sorted(run.job_type for run in runs) == ["document.enrich", "document.extract"], runs
    assert all((run.result or {}).get("outcome") == "SKIPPED" for run in runs), [run.result for run in runs]
    assert len(engines.llm.calls) == asked, "the model was asked again for work already done"
    assert (_rows_naming(engines, work_item_id), engines.item(work_item_id).extracted_entities) == before
    assert engines.item(work_item_id).status == "COMPLETED"
