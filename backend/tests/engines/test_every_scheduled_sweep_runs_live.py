"""Every scheduled sweep is emitted once per period and runs to success (campaign session 2, brief D).

    pytest tests/engines/test_every_scheduled_sweep_runs_live.py -q

The scheduler (`app.workers.scheduler.tick`) emits each entry of DEFAULT_SCHEDULE; every job is
then run through its real handler (`drain()`), in a database holding an organization with a
processed document, so the sweeps have something to look at. A sweep that crashes, has no
handler, or is emitted twice in one period fails here. Run at 23:59 UTC, every daily entry is due.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models.job import Job, JobStatus
from app.workers import scheduler
from tests.engines.conftest import Engines, drain

INVOICE = ["ACME SUPPLIES LTD", "TAX INVOICE", "Invoice Number: INV-SWEEP-1", "Total: 250.00 USD"]


def test_every_scheduled_sweep_is_emitted_once_and_succeeds(engines: Engines) -> None:
    engines.process("sweep.pdf", [INVOICE], marker="INV-SWEEP-1", classification="Invoice",
                    entities={"invoice_number": "INV-SWEEP-1", "total_amount": "250.00", "currency": "USD"})
    late = datetime.now(timezone.utc).replace(hour=23, minute=59, second=0, microsecond=0)

    emitted = scheduler.tick(now=late)
    assert emitted == len(scheduler.DEFAULT_SCHEDULE), emitted
    assert scheduler.tick(now=late) == 0, "a sweep was emitted twice in one period"

    runs = drain()  # raises if any job failed
    swept = {run.job_type for run in runs}
    expected = {entry.job_type for entry in scheduler.DEFAULT_SCHEDULE}
    assert expected <= swept, f"never ran: {sorted(expected - swept)}"

    engines.refresh()
    statuses = dict(engines.db.execute(select(Job.job_type, Job.status).where(
        Job.idempotency_key.like("sched:%"))).all())
    assert set(statuses) == expected
    assert set(statuses.values()) == {JobStatus.SUCCEEDED}, {k: v for k, v in statuses.items() if v != JobStatus.SUCCEEDED}
