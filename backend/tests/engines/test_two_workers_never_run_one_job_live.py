"""Two workers never run the same job, and a stopping worker hands back what it has not started.

    pytest tests/engines/test_two_workers_never_run_one_job_live.py -q

Campaign session 2, brief D. A worker claims a batch of jobs (25 by default) under one lease
(60 s) and runs them one after another. Reading a scanned document or asking a model can take
longer than that, so the lease of a job still waiting in the batch, or of the one running,
ran out; another worker's reaper put it back and that worker ran it too, while the first
worker also ran it: the same document read and charged twice. Here two real worker loops
(`app.worker.run_jobs_loop`) share slow jobs with a one-second lease.
"""

from __future__ import annotations

import dataclasses
import threading
import time
import uuid
from collections import Counter

import pytest
from sqlalchemy import select

from app.models.job import Job, JobStatus
from app.services import job_service
from app.services.job_service import JOB_HANDLERS
from tests.engines.conftest import Engines

JOB_TYPE = "test.slow_counted"
RUNS: Counter = Counter()
_LOCK = threading.Lock()


def _slow(payload: dict) -> dict:
    with _LOCK:
        RUNS[payload["n"]] += 1
    time.sleep(float(payload.get("seconds", 2.5)))
    return {"n": payload["n"]}


class _Until:
    """A GracefulShutdown the test sets."""

    def __init__(self) -> None:
        self.event = threading.Event()

    @property
    def requested(self) -> bool:
        return self.event.is_set()


@pytest.fixture
def slow_jobs(engines: Engines, monkeypatch):
    from app.workers import claim

    RUNS.clear()
    monkeypatch.setitem(JOB_HANDLERS, JOB_TYPE, _slow)
    # Each loop here stands for a worker process: its own identity (production: host:pid).
    monkeypatch.setattr(claim, "worker_identity", lambda: f"test-{threading.current_thread().name}")
    # A reaped job is claimable at once (10 s in production), so the race shows in seconds.
    monkeypatch.setattr(claim, "JOBS_QUEUE", dataclasses.replace(claim.JOBS_QUEUE, retry_base_seconds=0))

    def enqueue(count: int, seconds: float = 2.5) -> list[uuid.UUID]:
        ids = []
        for n in range(count):
            job = job_service.enqueue(engines.db, job_type=JOB_TYPE, payload={"n": n, "seconds": seconds},
                                      organization_id=engines.org, idempotency_key=f"{JOB_TYPE}:{uuid.uuid4()}")
            ids.append(job.id)
        engines.db.commit()
        return ids

    return enqueue


def _statuses(engines: Engines, ids: list[uuid.UUID]) -> dict[uuid.UUID, Job]:
    engines.refresh()
    return {job.id: job for job in engines.db.execute(select(Job).where(Job.id.in_(ids))).scalars()}


def _loop(shutdown: _Until, *, reap_every: int, name: str) -> threading.Thread:
    import app.worker as worker

    thread = threading.Thread(name=name, target=worker.run_jobs_loop, kwargs=dict(
        shutdown=shutdown, batch_size=3, lease_seconds=1, idle_sleep_seconds=0.1,
        reap_every_n_passes=reap_every, job_types=[JOB_TYPE]), daemon=True)
    thread.start()
    return thread


def test_two_workers_never_run_the_same_job(engines: Engines, slow_jobs) -> None:
    ids = slow_jobs(3)
    shutdown = _Until()
    first = _loop(shutdown, reap_every=10_000, name="worker-a")  # claims all three, never reaps
    time.sleep(0.3)
    second = _loop(shutdown, reap_every=1, name="worker-b")  # reaps every pass, as an idle replica soon does
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if all(job.status == JobStatus.SUCCEEDED for job in _statuses(engines, ids).values()):
            break
        time.sleep(0.2)
    shutdown.event.set()
    first.join(15)
    second.join(15)

    assert dict(RUNS) == {0: 1, 1: 1, 2: 1}, f"a job ran more than once: {dict(RUNS)}"
    assert all(job.status == JobStatus.SUCCEEDED for job in _statuses(engines, ids).values())


def test_a_stopping_worker_hands_back_the_jobs_it_has_not_started(engines: Engines, slow_jobs) -> None:
    import app.worker as worker

    ids = slow_jobs(3, seconds=1.0)
    shutdown = _Until()
    thread = threading.Thread(target=worker.run_jobs_loop, kwargs=dict(
        shutdown=shutdown, batch_size=3, lease_seconds=300, idle_sleep_seconds=0.1,
        reap_every_n_passes=10_000, job_types=[JOB_TYPE]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not RUNS and time.monotonic() < deadline:
        time.sleep(0.05)
    shutdown.event.set()  # SIGTERM while the first job runs
    thread.join(15)

    jobs = _statuses(engines, ids)
    assert sum(RUNS.values()) == 1, f"the stopping worker started more jobs: {dict(RUNS)}"
    started = [job for job in jobs.values() if job.status == JobStatus.SUCCEEDED]
    waiting = [job for job in jobs.values() if job.status != JobStatus.SUCCEEDED]
    assert len(started) == 1, "the job that was running did not finish"
    # The others go back at once (not after the 5-minute lease) and are not counted as an attempt.
    assert {(job.status, job.claimed_by, job.attempts) for job in waiting} == {(JobStatus.PENDING, None, 0)}, [
        (job.status, job.claimed_by, job.attempts, job.claim_expires_at) for job in waiting]
