"""The job service levels on every organization's dashboard are measured (F-189).

Service levels shows eight targets to every organization (Enterprise buys a "Priority 99.9% SLO").
Two of them, "Job completion rate" (claimed jobs reaching SUCCEEDED rather than DEAD) and "Job
end-to-end p95" (enqueue to terminal state), were never recorded: the API records its two, the
assistant records its stages, and the worker recorded nothing. Both read "No traffic in this
window" forever, however many documents an organization processed.

This runs the real worker loop (`app.worker.run_jobs_loop`) over a document's jobs.
"""

from __future__ import annotations

from app.core import slo_recorder
from tests.engines.conftest import Engines, make_pdf


class _StopAfter:
    """A GracefulShutdown that asks the loop to stop after `passes` checks."""

    def __init__(self, passes: int) -> None:
        self._left = passes

    @property
    def requested(self) -> bool:
        self._left -= 1
        return self._left < 0


def test_the_worker_records_job_completion_and_latency_for_the_organization(engines: Engines, monkeypatch) -> None:
    import app.worker as worker
    from app.workers.handlers import register_all

    register_all(replace=True)
    monkeypatch.setattr(worker, "_idle_sleep", lambda seconds: None)
    slo_recorder.recorder.drain()

    engines.upload("delivery-note.pdf", make_pdf([["DELIVERY NOTE", "Twelve pallets delivered to dock 4."]]))
    worker.run_jobs_loop(
        shutdown=_StopAfter(60),
        batch_size=20,
        lease_seconds=300,
        idle_sleep_seconds=0,
        reap_every_n_passes=10_000,  # keep the samples in memory for the assertion
    )

    from sqlalchemy import func, select

    from app.models.job import Job, JobStatus

    engines.refresh()
    succeeded = engines.db.execute(
        select(func.count()).select_from(Job).where(Job.organization_id == engines.org, Job.status == JobStatus.SUCCEEDED)
    ).scalar_one()
    assert succeeded >= 1, "the worker loop ran no job for the organization"

    series = {
        key.slo_key: values
        for key, values in slo_recorder.recorder.drain()
        if key.organization_id == str(engines.org)
    }
    assert "jobs.completion" in series, f"no job completion sample; recorded: {sorted(series)}"
    assert series["jobs.completion"].sample_count >= 1
    assert "jobs.latency.p95_ms" in series, f"no job latency sample; recorded: {sorted(series)}"
    assert series["jobs.latency.p95_ms"].sample_count == series["jobs.completion"].sample_count
