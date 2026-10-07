"""Settings -> "Reindex knowledge base": what the page can ask while the jobs run.

Found on a running server (F-141): the reindex request answered 202 and the page
had nothing to ask afterwards. The button stayed enabled, nothing said whether the
background re-embedding was still running, had finished, or had failed, and a
second click looked exactly like the first. The status route reads the
`knowledge.reindex` jobs of this workspace (never another's) and reports the run
in progress, its counts, and when the last run finished.
"""

from __future__ import annotations

from sqlalchemy import update

from app.models.job import Job, JobStatus
from tests.engines.conftest import Engines, drain

PAGE = [
    "SHIPPING POLICY",
    "Orders placed before noon ship the same business day from the central warehouse.",
    "Express delivery is available for an additional fee in every supported region.",
]
STATUS = "/work-items/knowledge-base/reindex/status"


def test_status_before_any_reindex_is_idle(engines: Engines) -> None:
    response = engines.get(STATUS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "idle"
    assert body["active_jobs"] == 0
    assert body["latest"] is None
    assert body["last_completed_at"] is None


def test_status_follows_a_reindex_from_queued_to_finished(engines: Engines) -> None:
    engines.process("shipping.pdf", [PAGE])
    engines.process("shipping-2.pdf", [PAGE + ["Second copy."]])

    assert engines.post("/work-items/knowledge-base/reindex").json()["queued"] == 2

    running = engines.get(STATUS).json()
    assert running["state"] == "running"
    assert running["active_jobs"] == 2
    assert running["latest"]["total"] == 2
    assert running["latest"]["waiting"] == 2
    assert running["latest"]["completed"] == 0
    assert running["latest"]["finished_at"] is None
    assert running["latest"]["requested_at"] is not None
    assert running["last_completed_at"] is None

    runs = drain(only=["knowledge.reindex"])
    assert [run.ok for run in runs] == [True, True]

    done = engines.get(STATUS).json()
    assert done["state"] == "idle"
    assert done["active_jobs"] == 0
    assert done["latest"]["total"] == 2
    assert done["latest"]["completed"] == 2
    assert done["latest"]["failed"] == 0
    assert done["latest"]["waiting"] == 0
    assert done["latest"]["finished_at"] is not None
    assert done["last_completed_at"] == done["latest"]["finished_at"]


def test_a_new_run_keeps_the_previous_completion_time(engines: Engines) -> None:
    engines.process("shipping.pdf", [PAGE])
    engines.post("/work-items/knowledge-base/reindex")
    drain(only=["knowledge.reindex"])
    first_finish = engines.get(STATUS).json()["last_completed_at"]
    assert first_finish is not None

    engines.post("/work-items/knowledge-base/reindex")
    again = engines.get(STATUS).json()
    assert again["state"] == "running"
    assert again["latest"]["waiting"] == 1
    # The card says "last completed on ..." from the earlier run while this one runs.
    assert again["last_completed_at"] == first_finish


def test_a_dead_job_is_reported_as_failed_not_completed(engines: Engines) -> None:
    engines.process("shipping.pdf", [PAGE])
    engines.post("/work-items/knowledge-base/reindex")

    engines.refresh()
    engines.db.execute(
        update(Job)
        .where(Job.job_type == "knowledge.reindex", Job.status == JobStatus.PENDING)
        .values(status=JobStatus.DEAD, last_error="boom")
    )
    engines.refresh()

    body = engines.get(STATUS).json()
    assert body["state"] == "idle"
    assert body["latest"]["failed"] == 1
    assert body["latest"]["completed"] == 0
    assert body["latest"]["finished_at"] is not None


def test_status_is_scoped_to_the_workspace_and_to_admins(engines: Engines) -> None:
    engines.process("shipping.pdf", [PAGE])
    engines.post("/work-items/knowledge-base/reindex")

    # Another tenant's workspace (its admin asking) sees none of this organization's runs.
    foreign = engines.client.get(
        f"/api/v1/workspaces/{engines.tenant.foreign_workspace.id}{STATUS}",
        headers=engines.tenant.other_org_member.headers,
    )
    assert foreign.status_code == 200, foreign.text
    assert foreign.json()["state"] == "idle"
    assert foreign.json()["latest"] is None

    # Same audience as the button: workspace admins and above.
    assert engines.get(STATUS, as_user=engines.tenant.viewer).status_code == 403
    assert engines.get(STATUS, as_user=engines.tenant.contributor).status_code == 403
    assert engines.get(STATUS, as_user=engines.tenant.other_org_member).status_code in (403, 404)
