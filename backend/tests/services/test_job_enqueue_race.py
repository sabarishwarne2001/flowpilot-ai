"""Two requests that queue the same job at the same moment (box 8, double clicks).

`job_service.enqueue` looks for a job with the same idempotency key, and when there is none inserts
one. Two requests can both look before either commits: the second insert then hits the unique key
`uq_jobs_org_idempotency_key`, and enqueue must hand back the first request's job, leaving the rest
of the caller's transaction (the mandate, the setting, the click it queued the job for) intact. If
the insert ran outside the savepoint meant to catch that, the whole request failed with a 500.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.job import Job
from app.services import job_service
from tests.conftest import TestSessionLocal


def test_the_loser_of_an_enqueue_race_gets_the_winners_job_and_keeps_its_own_work(db_session, tenant,
                                                                                 monkeypatch) -> None:
    organization_id = tenant.organization.id
    key = f"race:{uuid.uuid4()}"
    with TestSessionLocal() as other:  # the request that got there first, already committed
        other.begin()
        winner = job_service.enqueue(other, job_type="test.noop", organization_id=organization_id,
                                     payload={"first": True}, idempotency_key=key)
        other.commit()
        winner_id = winner.id

    # This request looked for the key before the other one committed, so it did not see it.
    real = job_service._existing_job_by_idempotency_key
    looks = []

    def missed_the_look(db, **kw):
        looks.append(kw)
        return None if len(looks) == 2 else real(db, **kw)

    monkeypatch.setattr(job_service, "_existing_job_by_idempotency_key", missed_the_look)
    with TestSessionLocal() as db:
        db.begin()
        # The request's own work, written before it queued the job (here: another job of its own).
        own = job_service.enqueue(db, job_type="test.noop", organization_id=organization_id,
                                  payload={"own": True}, idempotency_key=f"own:{uuid.uuid4()}")
        job = job_service.enqueue(db, job_type="test.noop", organization_id=organization_id,
                                  payload={"first": False}, idempotency_key=key)
        assert job.id == winner_id
        db.commit()  # the caller's own work still commits
        own_id = own.id

    db_session.expire_all()
    assert db_session.get(Job, own_id) is not None
    jobs = db_session.execute(select(Job).where(Job.organization_id == organization_id,
                                                Job.idempotency_key == key)).scalars().all()
    assert [j.id for j in jobs] == [winner_id] and jobs[0].payload.get("first") is True
