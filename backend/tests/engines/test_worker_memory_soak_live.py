"""A worker that runs 1,200 jobs does not grow (campaign session 2, brief D).

    pytest tests/engines/test_worker_memory_soak_live.py -q

The real jobs loop (`app.worker.run_jobs_loop`) claims, runs and settles 1,200 jobs; its
resident memory after the first 200 (imports, pools and caches warm) is compared with the end.
A per-job leak of even 40 KB would show as 40 MB here.
"""

from __future__ import annotations

import gc
import threading
import time
import uuid

import psutil
from sqlalchemy import func, select

from app.models.job import Job, JobStatus
from app.services import job_service
from tests.engines.conftest import Engines

WARM = 200
TOTAL = 1_200
ALLOWED_GROWTH_MB = 25


class _Until:
    def __init__(self, done) -> None:
        self._done = done

    @property
    def requested(self) -> bool:
        return self._done()


def _enqueue(engines: Engines, count: int) -> None:
    for n in range(count):
        job_service.enqueue(engines.db, job_type="test.noop", organization_id=engines.org,
                            payload={"n": n, "blob": "x" * 2_000}, idempotency_key=f"soak:{uuid.uuid4()}")
    engines.db.commit()


def _succeeded(engines: Engines) -> int:
    engines.refresh()
    return engines.db.execute(select(func.count()).select_from(Job).where(
        Job.job_type == "test.noop", Job.status == JobStatus.SUCCEEDED)).scalar_one()


def _run_until(engines: Engines, target: int) -> None:
    import app.worker as worker

    stop = threading.Event()
    thread = threading.Thread(target=worker.run_jobs_loop, kwargs=dict(
        shutdown=_Until(stop.is_set), batch_size=25, lease_seconds=60, idle_sleep_seconds=0.05,
        reap_every_n_passes=20, job_types=["test.noop"]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 300
    while _succeeded(engines) < target and time.monotonic() < deadline:
        time.sleep(0.5)
    stop.set()
    thread.join(30)
    assert _succeeded(engines) >= target, f"only {_succeeded(engines)} of {target} jobs ran"


def test_a_worker_running_1200_jobs_keeps_its_memory(engines: Engines) -> None:
    process = psutil.Process()
    _enqueue(engines, WARM)
    _run_until(engines, WARM)
    gc.collect()
    warm_rss = process.memory_info().rss

    _enqueue(engines, TOTAL - WARM)
    _run_until(engines, TOTAL)
    gc.collect()
    growth_mb = (process.memory_info().rss - warm_rss) / 1_048_576
    dead = engines.db.execute(select(func.count()).select_from(Job).where(Job.status == JobStatus.DEAD)).scalar_one()
    assert dead == 0
    assert growth_mb < ALLOWED_GROWTH_MB, f"the worker grew {growth_mb:.1f} MB over {TOTAL - WARM} jobs"
